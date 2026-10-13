"""Lumina worker as a tray app (Windows; Linux with a tray): the icon shows what it does, its menu pauses it, opens
the settings and quits — the work itself is lumina_worker.Engine, in a thread.

Settings window: the pairing code from Lumina (its address and the worker's token in one), where this computer sees
the server's library (found by itself, chosen when there are more), when it may work. Build: python build.py."""

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
    """Lumina's pairing code "LW1.<base64>" → (server, token)."""
    code = code.strip()
    if not code.startswith("LW1."):
        raise ValueError("To není párovací kód Luminy (začíná „LW1.“)")
    raw = code[4:] + "=" * (-len(code[4:]) % 4)
    data = json.loads(base64.urlsafe_b64decode(raw))
    return data["s"], data["t"]


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


# ── the settings window ──

class Settings:
    """The settings window (tkinter, in its own thread): connection, the library's folder, when to work."""

    def __init__(self, app: "App"):
        self.app = app

    def show(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        cfg, _ = w.read_config()
        root = tk.Tk()
        root.title(f"{APP} — nastavení")
        root.resizable(False, False)
        try:
            root.iconphoto(True, tk.PhotoImage(data=_png(icon_image("idle", 32))))
        except tk.TclError:
            pass
        frm = ttk.Frame(root, padding=14)
        frm.grid()
        row = 0

        def section(text: str) -> None:
            nonlocal row
            ttk.Label(frm, text=text, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w",
                                                                         pady=(10 if row else 0, 4))
            row += 1

        def field(label: str, var, width: int = 42, show: str = "", hint: str = "") -> ttk.Entry:
            nonlocal row
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
            e = ttk.Entry(frm, textvariable=var, width=width, show=show)
            e.grid(row=row, column=1, sticky="w")
            if hint:
                ttk.Label(frm, text=hint, foreground="#71717a").grid(row=row, column=2, sticky="w", padx=(8, 0))
            row += 1
            return e

        def spin(label: str, var, lo: int, hi: int, hint: str = "") -> None:
            nonlocal row
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
            ttk.Spinbox(frm, from_=lo, to=hi, textvariable=var, width=6).grid(row=row, column=1, sticky="w")
            if hint:
                ttk.Label(frm, text=hint, foreground="#71717a").grid(row=row, column=2, sticky="w", padx=(8, 0))
            row += 1

        # connection
        section("Připojení k Lumině")
        code = tk.StringVar()
        server = tk.StringVar(value="" if "example" in cfg["server"] else cfg["server"])
        token = tk.StringVar(value=cfg["token"] if str(cfg["token"]).startswith("lw_") else "")
        ttk.Label(frm, text="Párovací kód").grid(row=row, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frm, textvariable=code, width=42).grid(row=row, column=1, sticky="w")

        def use_code() -> None:
            try:
                s, t = parse_pairing(code.get())
            except (ValueError, KeyError) as e:
                messagebox.showerror(APP, str(e), parent=root)
                return
            server.set(s)
            token.set(t)
            code.set("")
            check()
        ttk.Button(frm, text="Použít", command=use_code).grid(row=row, column=2, sticky="w", padx=(8, 0))
        row += 1
        ttk.Label(frm, text="Lumina → Knihovna → Úpravy souborů → Pracovníci → Přidat pracovníka", foreground="#71717a") \
            .grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1
        field("Adresa Luminy", server)
        field("Token", token, show="•")
        result = tk.StringVar(value="")
        ttk.Button(frm, text="Ověřit připojení", command=lambda: check()).grid(row=row, column=1, sticky="w", pady=4)
        row += 1
        ttk.Label(frm, textvariable=result, wraplength=520, justify="left").grid(row=row, column=0, columnspan=3,
                                                                                  sticky="w")
        row += 1

        # the library's folder
        section("Kde tento počítač vidí knihovnu serveru")
        mapping = (cfg.get("path_map") or [["/data", ""]])[0]
        srv_dir = tk.StringVar(value=mapping[0])
        loc_dir = tk.StringVar(value=mapping[1])
        ttk.Label(frm, text="Složka serveru").grid(row=row, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frm, textvariable=srv_dir, width=20).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Je tady").grid(row=row, column=0, sticky="w", padx=(0, 8))
        choice = ttk.Combobox(frm, textvariable=loc_dir, width=40)
        choice.grid(row=row, column=1, sticky="w")
        ttk.Button(frm, text="Vybrat…", command=lambda: loc_dir.set(
            filedialog.askdirectory(parent=root, initialdir=loc_dir.get() or None) or loc_dir.get())) \
            .grid(row=row, column=2, sticky="w", padx=(8, 0))
        row += 1
        roots_info = tk.StringVar(value="")
        ttk.Label(frm, textvariable=roots_info, wraplength=520, justify="left", foreground="#71717a") \
            .grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        found: dict[str, str] = {}
        roots: list[str] = []

        def show_roots(*_):
            if not roots:
                return
            lines = []
            for r in roots:
                try:
                    local = w.to_local(r, {"path_map": [[srv_dir.get() or "/", loc_dir.get()]]})
                    ok = bool(loc_dir.get()) and os.path.isdir(local)
                except RuntimeError:
                    local, ok = "?", False
                lines.append(f"{'✓' if ok else '✗'} {r} = {local}")
            roots_info.set("\n".join(lines))
        loc_dir.trace_add("write", show_roots)
        srv_dir.trace_add("write", show_roots)

        def pick(_=None):
            if loc_dir.get() in found:
                srv_dir.set(found[loc_dir.get()])
        choice.bind("<<ComboboxSelected>>", pick)

        def check() -> None:
            nonlocal roots
            test = {**cfg, "server": server.get().strip(), "token": token.get().strip()}
            result.set("Ověřuji…")
            root.update_idletasks()
            try:
                lumina = w.Lumina(test)
                lumina.find()
                hello = lumina.call("POST", "/worker/hello", {"state": {"waiting": "nastavuji se"}})
            except (RuntimeError, OSError) as e:
                result.set(f"✗ {e}")
                return
            roots = hello.get("roots") or []
            result.set(f"✓ Připojeno jako „{hello.get('name')}“")
            maps = find_mappings(roots)
            found.clear()
            found.update({local: srv for srv, local in maps})
            choice["values"] = list(found)
            if maps and loc_dir.get() not in found:
                srv_dir.set(maps[0][0])
                loc_dir.set(maps[0][1])
            if len(maps) > 1:
                result.set(result.get() + f" — knihovnu vidím na {len(maps)} místech, vyber to správné")
            elif not maps:
                result.set(result.get() + " — knihovnu serveru tu nevidím: připoj síťovou složku serveru (např. Z:)")
            show_roots()

        # when to work
        section("Kdy smí pracovat")
        hours = tk.StringVar(value=cfg["hours"])
        weekend = tk.StringVar(value=cfg["weekend_hours"])
        field("Hodiny", hours, 24, hint="např. 22:00-07:00 (prázdné: kdykoli)")
        field("O víkendu", weekend, 24, hint="prázdné: jako ve všední den")
        cpu = tk.IntVar(value=int(cfg["cpu_other_max"]))
        spin("Jiné programy smí brát CPU", cpu, 5, 100, "% — nad tím se kódování pozastaví")
        programs = tk.StringVar(value=", ".join(cfg["blocked_programs"]))
        field("Nepracovat, když běží", programs, hint="např. obs64.exe, eldenring.exe")
        battery = tk.BooleanVar(value=bool(cfg["on_battery"]))
        ttk.Checkbutton(frm, text="Pracovat i na baterii", variable=battery).grid(row=row, column=1, sticky="w")
        row += 1
        idle = tk.IntVar(value=int(cfg["idle_minutes"]))
        spin("Jen když se nepoužívá", idle, 0, 240, "min bez myši a klávesnice (0: nezáleží)")
        threads = tk.IntVar(value=int(cfg["threads"]))
        spin("Vlákna pro kódování", threads, 0, os.cpu_count() or 64, f"0 = všechna ({os.cpu_count()}); méně = počítač tolik nezpomalí")
        give_back = tk.IntVar(value=int(cfg["give_back_after_hours"]))
        spin("Vrátit úlohu po pauze", give_back, 1, 72, "h — pak ji vezme server nebo jiný pracovník")

        def save() -> None:
            try:
                new = {**cfg, "server": server.get().strip().rstrip("/"), "token": token.get().strip(),
                       "path_map": [[srv_dir.get().strip() or "/", loc_dir.get().strip()]],
                       "hours": hours.get().strip(), "weekend_hours": weekend.get().strip(),
                       "cpu_other_max": int(cpu.get()), "on_battery": bool(battery.get()),
                       "blocked_programs": [p.strip() for p in programs.get().split(",") if p.strip()],
                       "idle_minutes": int(idle.get()), "threads": int(threads.get()),
                       "give_back_after_hours": int(give_back.get())}
            except (tk.TclError, ValueError):
                messagebox.showerror(APP, "Čísla nejsou v pořádku", parent=root)
                return
            if not new["token"].startswith("lw_") or not new["server"].startswith("http"):
                messagebox.showerror(APP, "Chybí adresa Luminy nebo token — vlož párovací kód", parent=root)
                return
            if self.app.busy() and not messagebox.askyesno(
                    APP, "Právě se kóduje — po uložení se úloha vrátí Lumině a začne znovu. Uložit?", parent=root):
                return
            w.save_config(new)
            root.destroy()
            self.app.restart()

        buttons = ttk.Frame(frm)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Zrušit", command=root.destroy).pack(side="right")
        ttk.Button(buttons, text="Uložit", command=save).pack(side="right", padx=(0, 8))
        if token.get() and server.get():
            root.after(200, check)
        root.lift()
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))
        root.mainloop()


def _png(img: Image.Image) -> str:
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── the app ──

class App:
    def __init__(self):
        self.engine: w.Engine | None = None
        self.problem = ""
        self.paused = False
        self.settings_open = threading.Lock()
        self.icon = pystray.Icon("lumina-worker", icon_image("starting"), APP, self._menu())

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
            item("Pozastavit", self.toggle_pause, checked=lambda _: self.paused),
            item("Nastavení…", lambda: self.open_settings(), default=True),
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
            self.open_settings()
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

    def open_settings(self) -> None:
        def run():
            if not self.settings_open.acquire(blocking=False):
                return                                        # one window at a time
            try:
                Settings(self).show()
            finally:
                self.settings_open.release()
        threading.Thread(target=run, daemon=True, name="settings").start()

    def open_lumina(self, *_):
        cfg, _ = w.read_config()
        if "example" not in cfg["server"]:
            webbrowser.open(cfg["server"])

    def quit(self, *_):
        if self.engine:
            self.engine.stop()
            for _ in range(30):                               # a running job is given back first
                if not self.engine.job:
                    break
                time.sleep(0.5)
        self.icon.stop()

    def _refresh(self) -> None:
        last = None
        while True:
            state, text = self.status()
            if (state, text) != last:
                self.icon.icon = icon_image(state)
                self.icon.title = f"{APP} — {STATES.get(state, state)}\n{text}"[:127]
                self.icon.update_menu()
                last = (state, text)
            time.sleep(2)

    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            self.start()
            threading.Thread(target=self._refresh, daemon=True, name="refresh").start()
        self.icon.run(setup=setup)


def _open(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
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
