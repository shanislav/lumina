"""Lumina worker as a tray app: the icon shows what it does, its menu pauses it, opens its window and quits — the
work itself is lumina_worker.Engine, in a thread.

The window is ui.html in the system's own web view (pywebview: Edge on Windows, WebKit on macOS, GTK on Linux — one
page for all): what it does now, the pairing code from Lumina (its address and the worker's token in one), where
this computer sees the server's library (found by itself, chosen when there are more), when it may work and how
considerate it is. Build: python build.py."""

import base64
import json
import logging
import logging.handlers
import os
import string
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
import webview
from PIL import Image, ImageDraw, ImageFont

import lumina_worker as w

APP = "Lumina Worker"
COLORS = {"working": (34, 197, 94), "paused": (245, 158, 11), "waiting": (245, 158, 11), "idle": (139, 92, 246),
          "offline": (239, 68, 68), "starting": (113, 113, 122), "setup": (59, 130, 246)}
STATES = {"working": "kóduje", "paused": "pozastaveno", "waiting": "čeká", "idle": "připraven", "offline": "bez spojení",
          "starting": "připojuje se", "setup": "chybí nastavení"}
log = w.log


def icon_image(state: str, size: int = 64) -> Image.Image:
    """A round icon in the state's colour with an "L"."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=COLORS.get(state, COLORS["starting"]) + (255,))
    try:
        font = ImageFont.truetype("segoeuib.ttf" if os.name == "nt" else "DejaVuSans-Bold.ttf", int(size * 0.62))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2 + 1), "L", fill="white", font=font, anchor="mm")
    return img


# ── pairing, the library's folder ──

def parse_pairing(code: str) -> tuple[str, str]:
    """Lumina's pairing code "LW1.<base64>" → (server, token); a bare token "lw_…" → ("", token)."""
    code = code.strip()
    if code.startswith("lw_"):
        return "", code
    if not code.startswith("LW1."):
        raise ValueError("To není párovací kód Luminy (začíná „LW1.“) ani token pracovníka (začíná „lw_“)")
    raw = code[4:] + "=" * (-len(code[4:]) % 4)
    data = json.loads(base64.urlsafe_b64decode(raw))
    return data["s"], data["t"]


def normal_server(text: str) -> str:
    """The address as typed: "lumina.example.com", "https://lumina.example.com/library" → "https://lumina.example.com"."""
    text = text.strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    scheme, _, rest = text.partition("://")
    return f"{scheme}://{rest.split('/')[0]}"


def _bases() -> list[Path]:
    """Folders where the server's share may be mounted: drives and their first level (Windows), /mnt, /media."""
    out: list[Path] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:\\")
            if letter not in "AB" and root.exists():
                out.append(root)
    else:
        out += [Path("/")] + [p for d in ("/mnt", "/media") if os.path.isdir(d) for p in Path(d).glob("*")]
        out += [p for d in ("/media",) if os.path.isdir(d) for p in Path(d).glob("*/*")]
    deeper = []
    for base in out:
        try:
            deeper += [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(("$", "."))]
        except OSError:
            pass
    return out + deeper


def find_mappings(roots: list[str]) -> list[tuple[str, str]]:
    """Where this computer sees the server's library folders: every (server folder, folder here) under which all
    of them exist — usually one; the dev and the real library on one share are two."""
    if not roots:
        return []
    parts = [r.strip("/").split("/") for r in roots]
    common = []
    for level in zip(*parts):
        if len(set(level)) != 1:
            break
        common.append(level[0])
    found: list[tuple[str, str]] = []
    for base in _bases():
        for k in range(len(common), -1, -1):              # server folder: /data/Video/Movies, /data/Video, /data …
            server = "/" + "/".join(common[:k])
            if all((base / Path(*p[k:])).is_dir() for p in parts):
                local = str(base)
                if not any(l == local for _, l in found):
                    found.append((server if k else "/", local))
                break
    # the most general mapping of a folder (the server's /data → Z:\, not /data/Video → Z:\Video)
    return sorted(found, key=lambda m: (len(Path(m[1]).parts), m[1]))


# ── start with Windows ──

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart() -> bool | None:
    """Whether the app starts with Windows (None: not here — only the built app on Windows)."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, APP)
            except OSError:
                pass


# ── the window (ui.html in the system's own web view: Edge on Windows, WebKit on macOS, GTK on Linux) ──

class Api:
    """What ui.html may ask for (window.pywebview.api.*)."""

    def __init__(self, app: "App"):
        self._app = app

    def state(self) -> dict:
        state, text = self._app.status()
        job = self._app.engine.job if self._app.engine else None
        value = job.value if job and job.phase == "encode" and isinstance(job.value, (int, float)) else None
        return {"state": state, "label": STATES.get(state, state), "text": text, "value": value,
                "paused": self._app.paused, "version": w.VERSION, "name": self._app.engine.name if self._app.engine else ""}

    def load(self) -> dict:
        cfg, _ = w.read_config()
        try:
            import psutil
            procs = sorted({(p.info.get("name") or "") for p in psutil.process_iter(["name"])} - {""}, key=str.lower)
        except Exception:  # noqa: BLE001 — only hints for the field
            procs = []
        return {"config": cfg, "cpus": os.cpu_count() or 8, "processes": procs,
                "server": "" if "example" in cfg["server"] else cfg["server"],
                "token": cfg["token"] if str(cfg["token"]).startswith("lw_") else ""}

    def pair(self, code: str) -> dict:
        try:
            server, token = parse_pairing(code)
        except (ValueError, KeyError) as e:
            return {"error": str(e)}
        return {"server": server, "token": token}

    def check(self, server: str, token: str) -> dict:
        server = normal_server(server)
        if not server or not token.strip().startswith("lw_"):
            return {"server": server, "error": "chybí adresa Luminy nebo token"}
        cfg, _ = w.read_config()
        try:
            lumina = w.Lumina({**cfg, "server": server, "token": token.strip()})
            lumina.find()
            hello = lumina.call("POST", "/worker/hello", {"state": {"waiting": "nastavuje se"}})
        except (RuntimeError, OSError) as e:
            return {"server": server, "error": str(e)[:200]}
        roots = hello.get("roots") or []
        return {"server": server, "name": hello.get("name"), "roots": roots, "mappings": find_mappings(roots)}

    def roots(self, server_dir: str, local_dir: str, roots: list) -> list:
        out = []
        for r in roots:
            try:
                local = w.to_local(r, {"path_map": [[server_dir.strip() or "/", local_dir.strip()]]})
                ok = bool(local_dir.strip()) and os.path.isdir(local)
            except RuntimeError:
                local, ok = "?", False
            out.append({"root": r, "local": local, "ok": ok})
        return out

    def folder(self, start: str) -> str:
        picked = self._app.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or "")
        return picked[0] if picked else ""

    def save(self, cfg: dict, force: bool = False) -> dict:
        cfg = {**cfg, "server": normal_server(cfg.get("server", ""))}
        if not str(cfg.get("token", "")).startswith("lw_") or not cfg["server"]:
            return {"error": "chybí adresa Luminy nebo token — vlož párovací kód"}
        if self._app.busy() and not force:
            return {"confirm": True}
        w.save_config(cfg)
        self._app.restart()
        return {"ok": True}

    def pause(self) -> None:
        self._app.toggle_pause()

    def close(self) -> None:
        self._app.window.hide()


# ── the app ──

class App:
    def __init__(self):
        self.engine: w.Engine | None = None
        self.problem = ""
        self.paused = False
        self.quitting = False
        self.icon = pystray.Icon("lumina-worker", icon_image("starting"), APP, self._menu())
        self.window = webview.create_window(APP, url=str(w.HERE / "ui.html"), js_api=Api(self), width=760, height=860,
                                            min_size=(620, 520), hidden=True, background_color="#09090b")
        self.window.events.closing += self._closing
        self.window.events.shown += lambda: _dark_title(self.window)

    def busy(self) -> bool:
        return bool(self.engine and self.engine.job)

    def status(self) -> tuple[str, str]:
        if self.problem:
            return "setup", self.problem
        if not self.engine:
            return "starting", "spouštím"
        return self.engine.status()

    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: self.status()[1][:120], None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Otevřít", lambda: self.open_window(), default=True),
            item("Pozastavit", self.toggle_pause, checked=lambda _: self.paused),
            item("Otevřít Luminu", self.open_lumina),
            item("Otevřít záznam", lambda: _open(w.LOG_FILE)),
            item("Spouštět s Windows", self.toggle_autostart, checked=lambda _: bool(autostart()),
                 visible=autostart() is not None),
            pystray.Menu.SEPARATOR,
            item("Ukončit", self.quit),
        )

    def start(self) -> None:
        cfg, problem = w.read_config()
        self.problem = problem
        if problem:
            self.engine = None
            self.open_window()
            return
        self.engine = w.Engine(cfg)
        self.engine.pause(self.paused)
        engine = self.engine

        def run() -> None:
            try:
                engine.run()
            except Exception as e:  # noqa: BLE001 — shown in the tray, not a crash
                log.exception("Pracovník spadl")
                if self.engine is engine:
                    self.problem = f"Chyba: {e}"
        threading.Thread(target=run, daemon=True, name="engine").start()

    def restart(self) -> None:
        if self.engine:
            self.engine.stop()
        self.start()

    def toggle_pause(self, *_):
        self.paused = not self.paused
        if self.engine:
            self.engine.pause(self.paused)

    def toggle_autostart(self, *_):
        set_autostart(not autostart())

    def open_window(self) -> None:
        self.window.show()
        self.window.restore()

    def open_lumina(self, *_):
        cfg, _ = w.read_config()
        if "example" not in cfg["server"]:
            webbrowser.open(cfg["server"])

    def _closing(self):
        if self.quitting:
            return True
        self.window.hide()                                    # closing the window only hides it (the tray stays)
        return False

    def quit(self, *_):
        if self.engine:
            self.engine.stop()
            for _ in range(30):                               # a running job is given back first
                if not self.engine.job:
                    break
                time.sleep(0.5)
        self.quitting = True
        self.icon.stop()
        self.window.destroy()

    def _refresh(self) -> None:
        last = None
        while not self.quitting:
            state, text = self.status()
            if (state, text) != last:
                self.icon.icon = icon_image(state)
                self.icon.title = f"{APP} — {STATES.get(state, state)}\n{text}"[:127]
                self.icon.update_menu()
                last = (state, text)
            time.sleep(2)

    def run(self) -> None:
        def tray(icon):
            icon.visible = True
            threading.Thread(target=self._refresh, daemon=True, name="refresh").start()
        threading.Thread(target=self.icon.run, kwargs={"setup": tray}, daemon=True, name="tray").start()
        webview.start(self.start)                             # the window's loop owns the main thread


def _dark_title(window) -> None:
    """A dark title bar like the window's content (Windows 10/11)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = window.native.Handle.ToInt32()
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:  # noqa: BLE001 — only looks
        pass


def _open(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _single() -> bool:
    """Only one app per user (two would take two jobs and fight over the CPU)."""
    if os.name != "nt":
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "LuminaWorkerTray")
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def main() -> None:
    handler = logging.handlers.RotatingFileHandler(w.LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[handler])
    if not _single():
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "Lumina Worker už běží — najdeš ho v liště u hodin.", APP, 0x40)
        return
    App().run()


if __name__ == "__main__":
    main()
