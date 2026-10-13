"""Lumina worker — encodes Lumina's "Úpravy souborů" jobs on this computer (Windows / Linux).

The computer needs the server's share mounted (e.g. Z: = \\\\server\\Share): the worker reads the file from it,
encodes here and writes the result next to the file — replacing it and the library are the server's work. It asks
Lumina for work itself (a long poll: about one small request a minute when idle) and works only when allowed:
within its schedule, while other programs leave the CPU alone, while none of the listed programs runs, not on battery.
When that changes mid-job, the encoder is paused (not stopped) and goes on later.

Settings: lumina-worker.json next to this file (written on the first start). Run: python lumina_worker.py
(needs ffmpeg 9 with libx265 / hevc_nvenc and libvmaf, and the psutil package)."""

import ctypes
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import psutil

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
HOME = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HOME.parent / "backend" / "app" / "modules" / "convert"))   # the repository's shrink.py
import shrink  # noqa: E402  — the same encoding and measuring as the server's

VERSION = "0.3"
# the app (an .exe): its settings and log in the user's AppData; run from the repository: next to this file
DATA = Path(os.environ.get("APPDATA") or Path.home()) / "Lumina Worker" if getattr(sys, "frozen", False) else HOME
DATA.mkdir(parents=True, exist_ok=True)
CONFIG = DATA / "lumina-worker.json"
LOG_FILE = DATA / "lumina-worker.log"
TEMPLATE = {
    "server": "https://lumina.example.com",
    "token": "lw_… (Lumina → Knihovna → Úpravy souborů → Pracovníci → Přidat)",
    "path_map": [["/data", "Z:\\"]],
    "ffmpeg_dir": "",
    "work_dir": "",
    "schedule": {"always": True, "days": [0, 1, 2, 3, 4, 5, 6], "ranges": []},
    "cpu_other_max": 40,
    "blocked_programs": [],
    "on_battery": False,
    "idle_minutes": 0,
    "threads": 0,
    "give_back_after_hours": 12,
}
HELP = {
    "path_map": "where this computer sees the server's folders: [server path, path here] (Lumina's /data = Z:\\)",
    "ffmpeg_dir": "folder with ffmpeg and ffprobe (empty: from PATH)",
    "work_dir": "local folder for the work in progress (empty: the system's temp)",
    "schedule": "when it may work: always, or on these days (0 = Monday) within these times (none: all day); "
                "a time over midnight belongs to the day it starts on",
    "cpu_other_max": "pause while other programs use more of the CPU than this (%)",
    "blocked_programs": "pause while one of these runs, e.g. [\"obs64.exe\", \"eldenring.exe\"]",
    "on_battery": "work on battery too (a notebook)",
    "idle_minutes": "work only after this many minutes without mouse and keyboard (0: no matter; Windows)",
    "threads": "how many CPU threads the encoder may use (0: all)",
    "give_back_after_hours": "a job paused this long goes back to Lumina (the server or another worker takes it)",
}
REPORT_S = 30
CHECK_S = 2                 # how often the worker looks (its new encoders get full speed within this)
LOAD_WINDOW_S = 60          # the CPU load of other programs: averaged over this
RESUME_AFTER_S = 60         # paused for load: goes on after this long below the limit

log = logging.getLogger("lumina-worker")


# ── settings ──

def read_config() -> tuple[dict, str]:
    """The settings → (settings, "" or what is missing). The first time a template is written."""
    if not CONFIG.exists():
        save_config(dict(TEMPLATE))
    try:
        cfg = {**TEMPLATE, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except ValueError as e:
        return dict(TEMPLATE), f"Nastavení {CONFIG} je poškozené: {e}"
    cfg.pop("_help", None)
    if "hours" in cfg or "weekend_hours" in cfg:          # the settings of version 0.2 and older
        cfg["schedule"] = _old_hours(cfg.pop("hours", ""), cfg.pop("weekend_hours", ""))
    if not str(cfg["token"]).startswith("lw_") or "example" in cfg["server"]:
        return cfg, f"V nastavení chybí server nebo token ({CONFIG})"
    return cfg, ""


def save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps({**cfg, "_help": HELP}, indent=2, ensure_ascii=False), encoding="utf-8")


def to_local(path: str, cfg: dict) -> str:
    """The server's path → this computer's (path_map)."""
    for server, local in cfg["path_map"]:
        server = server.rstrip("/")
        if path == server or path.startswith(server + "/"):
            rest = path[len(server):].lstrip("/")
            return str(Path(local) / Path(*rest.split("/"))) if rest else local
    raise RuntimeError(f"Cestu {path} neumím přeložit — doplň path_map")


# ── when it may work ──

DAYS = ("Po", "Út", "St", "Čt", "Pá", "So", "Ne")


def _minute(text: str) -> int | None:
    try:
        h, m = (int(x) for x in str(text).strip().split(":"))
        return h * 60 + m if 0 <= h <= 24 and 0 <= m < 60 else None
    except ValueError:
        return None


def _old_hours(hours: str, weekend: str) -> dict:
    """Version 0.2's "22:00-07:00" (and a weekend's own) → a schedule."""
    def ranges(spec: str) -> list[list[str]]:
        return [[a.strip(), b.strip()] for a, _, b in (p.partition("-") for p in spec.split(",")) if a.strip() and b.strip()]
    if not hours.strip() and not weekend.strip():
        return {"always": True, "days": list(range(7)), "ranges": []}
    # the weekend's own hours cannot stay apart in one schedule: the weekdays' are kept for every day
    return {"always": False, "days": list(range(7)), "ranges": ranges(hours or weekend)}


def schedule_ok(schedule: dict, now: datetime) -> bool:
    """Whether the schedule lets it work now. A time over midnight (22:00–07:00) belongs to the day it starts on:
    Friday's goes on into Saturday morning."""
    if not schedule or schedule.get("always", True):
        return True
    days = set(schedule.get("days") or [])
    ranges = [(_minute(a), _minute(b)) for a, b in schedule.get("ranges") or []]
    ranges = [(a, b) for a, b in ranges if a is not None and b is not None and a != b]
    today, minute = now.weekday(), now.hour * 60 + now.minute
    if not ranges:
        return today in days
    for a, b in ranges:
        if a < b and today in days and a <= minute < b:
            return True
        if a > b and ((today in days and minute >= a) or ((today - 1) % 7 in days and minute < b)):
            return True
    return False


def schedule_text(schedule: dict) -> str:
    """"Po–Pá 22:00–07:00" — for the reason it waits."""
    if not schedule or schedule.get("always", True):
        return "kdykoli"
    days = sorted(set(schedule.get("days") or []))
    if days == list(range(7)):
        when = "denně"
    elif days == list(range(5)):
        when = "Po–Pá"
    elif days == [5, 6]:
        when = "So–Ne"
    else:
        when = ", ".join(DAYS[d] for d in days) or "žádný den"
    times = ", ".join(f"{a}–{b}" for a, b in schedule.get("ranges") or [])
    return f"{when} {times}".strip()


def _idle_minutes() -> float:
    if os.name != "nt":
        return 1e9
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 1e9
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 60000


def full_speed(pid: int) -> None:
    """Windows 11 puts a process without a window into its power-saving mode (EcoQoS: efficiency cores, low
    clocks) — the encoder ran at ~60 % of its speed. It is switched off for each of the worker's encoders (the
    low priority stays: other programs still go first)."""
    if os.name != "nt":
        return
    from ctypes import wintypes

    class Throttling(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x0200, False, pid)          # PROCESS_SET_INFORMATION
    if not handle:
        return
    state = Throttling(1, 0x1 | 0x4, 0)                   # execution speed + timer resolution: managed, not throttled
    k32.SetProcessInformation(handle, 4, ctypes.byref(state), ctypes.sizeof(state))   # ProcessPowerThrottling
    k32.CloseHandle(handle)


def stay_awake(on: bool) -> None:
    """While a job runs the computer does not fall asleep by itself (Windows: a request of this program only, gone
    with it — the power settings stay; closing the lid still does what it is set to)."""
    if os.name != "nt":
        return
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


class Watch:
    """Whether the worker may work now, and the CPU load of the other programs (its own encoder not counted)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.samples: list[tuple[float, float]] = []
        self.calm_since: float | None = None
        self.busy = False
        self.procs: dict[int, psutil.Process] = {}     # the encoder's processes, kept for their CPU readings
        self.manual = ""                               # the user's own pause (the tray app)
        psutil.cpu_percent(None)

    def _own(self) -> list[psutil.Process]:
        try:
            return psutil.Process().children(recursive=True)
        except psutil.Error:
            return []

    def sample(self) -> None:
        """One reading of the other programs' load: the whole CPU minus the worker's own encoder. psutil measures
        a process's CPU between two calls on the SAME object — a new one says 0 % (the encoder would count as
        "another program" and pause itself): the objects are kept, a reading with a new process is skipped."""
        total = psutil.cpu_percent(None)
        own, fresh = 0.0, False
        alive = {}
        for p in self._own():
            known = self.procs.get(p.pid)
            try:
                if known is None:
                    p.cpu_percent(None)             # the first call only starts its measurement
                    full_speed(p.pid)
                    alive[p.pid], fresh = p, True
                else:
                    own += known.cpu_percent(None)
                    alive[p.pid] = known
            except psutil.Error:
                pass
        # one that ended since the last reading took CPU we can no longer read — that reading is skipped too
        # (a preview runs many short encodes and measurements)
        fresh = fresh or any(pid not in alive for pid in self.procs)
        self.procs = alive
        if fresh:
            return
        other = max(0.0, total - own / (psutil.cpu_count() or 1))
        now = time.time()
        self.samples = [(t, v) for t, v in self.samples if now - t < LOAD_WINDOW_S] + [(now, other)]

    def other_load(self) -> float:
        return sum(v for _, v in self.samples) / len(self.samples) if self.samples else 0.0

    def blocked(self, working: bool) -> str:
        """Why it may not work now — "" when it may."""
        cfg = self.cfg
        if self.manual:
            return self.manual
        now = datetime.now()
        if not schedule_ok(cfg.get("schedule") or {}, now):
            return f"mimo nastavený čas ({schedule_text(cfg.get('schedule') or {})})"
        battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if battery and not battery.power_plugged and not cfg["on_battery"]:
            return "notebook běží na baterii"
        names = {n.lower() for n in cfg["blocked_programs"]}
        if names:
            for p in psutil.process_iter(["name"]):
                if (p.info.get("name") or "").lower() in names:
                    return f"běží {p.info['name']}"
        if cfg["idle_minutes"] and _idle_minutes() < cfg["idle_minutes"]:
            return "počítač se používá"
        load = self.other_load()
        if load > cfg["cpu_other_max"]:
            self.busy, self.calm_since = True, None
            return f"jiné programy vytěžují procesor ({load:.0f} %)"
        if self.busy:
            # after a busy spell the CPU must stay calm a while (a game's loading screen is not the end of the game)
            self.calm_since = self.calm_since or time.time()
            if time.time() - self.calm_since < RESUME_AFTER_S:
                return f"čekám, až se procesor uklidní ({load:.0f} %)"
            self.busy = False
        return ""

    def pause(self) -> None:
        for p in self._own():
            try:
                p.suspend()
            except psutil.Error:
                pass

    def resume(self) -> None:
        for p in self._own():
            try:
                p.resume()
            except psutil.Error:
                pass

    def kill(self) -> None:
        for p in self._own():
            try:
                p.kill()
            except psutil.Error:
                pass


# ── talking to Lumina ──

class Lumina:
    def __init__(self, cfg: dict):
        server = cfg["server"].rstrip("/")
        # the address one types in the browser: its /api is the backend behind nginx (which may cut one /api off)
        self.bases = [server + "/api/api/convert", server + "/api/convert", server + "/convert"]
        self.base = self.bases[0]
        self.token = cfg["token"]

    def find(self) -> None:
        """Which of the possible addresses is Lumina's backend: the first that does not answer 404."""
        for base in self.bases:
            self.base = base
            try:
                self.call("POST", "/worker/hello", {"state": {"waiting": "připojuji se"}})
                return
            except RuntimeError as e:
                if "404" not in str(e):
                    raise
        raise RuntimeError("Lumina na této adrese neodpovídá — zkontroluj „server“ (adresa, kterou píšeš do prohlížeče)")

    def call(self, method: str, path: str, body=None, timeout: float = 30, raw: bytes | None = None) -> dict:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/octet-stream" if raw is not None else "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"Lumina {e.code}: {detail}") from e


def machine() -> dict:
    """What this computer is: system, CPU, graphics card, the encoders its ffmpeg has (NVENC tried for real)."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, **shrink.SPAWN).stdout
    encoders = [e for e in ("libx265", "hevc_nvenc", "hevc_qsv", "hevc_amf") if f" {e} " in out]
    for hw in [e for e in encoders if e != "libx265"]:
        # built into ffmpeg is not working here: the card, its driver (NVENC of ffmpeg 9 needs driver 610+)
        test = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=size=320x240:d=0.2",
                               "-c:v", hw, "-f", "null", "-"], capture_output=True, text=True, **shrink.SPAWN)
        if test.returncode != 0:
            encoders.remove(hw)
            why = next((line for line in test.stderr.splitlines() if "driver" in line.lower()), "")
            log.info("Kodér %s tu nefunguje%s", hw, f" — {why.split('] ')[-1]}" if why else "")
    gpu = ""
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10, **shrink.SPAWN).stdout.strip().splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        pass
    cpu = platform.processor()
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return {"version": VERSION, "system": f"{platform.system()} {platform.release()}", "cpu": cpu,
            "threads": os.cpu_count() or 0, "gpu": gpu, "encoders": encoders,
            "x265": shrink.x265_version() if "libx265" in encoders else ""}


# ── one job ──

class Job:
    """A job running here: its progress reported to Lumina, paused when the computer is needed."""

    def __init__(self, job: dict, lumina: Lumina, watch: Watch, cfg: dict):
        self.job, self.lumina, self.watch, self.cfg = job, lumina, watch, cfg
        self.phase, self.value, self.paused = "", None, ""
        self.paused_since: float | None = None
        self.give_back = False
        self.lost = False
        self.stop = threading.Event()

    def progress(self, phase: str, value: float | None = None) -> None:
        self.phase, self.value = phase, value

    def _report(self) -> None:
        try:
            self.lumina.call("POST", f"/worker/jobs/{self.job['id']}/progress",
                             {"phase": self.phase, "value": self.value, "paused": self.paused})
        except RuntimeError as e:
            if "409" in str(e):            # not ours any more (removed, expired) — stop
                log.warning("Úloha už není naše: %s", e)
                self.lost = True
                self.watch.kill()
        except OSError as e:
            log.info("Lumina neodpovídá: %s", e)

    def _guard(self) -> None:
        last = 0.0
        while not self.stop.wait(CHECK_S):
            self.watch.sample()
            why = self.watch.blocked(working=not self.paused)
            if why and not self.paused:
                log.info("Pozastaveno: %s", why)
                self.watch.pause()
                self.paused, self.paused_since = why, time.time()
                last = 0.0
            elif not why and self.paused:
                log.info("Pokračuji")
                self.watch.resume()
                self.paused, self.paused_since = "", None
                last = 0.0
            elif why and self.paused:
                self.paused = why
                if time.time() - (self.paused_since or time.time()) > self.cfg["give_back_after_hours"] * 3600:
                    log.info("Pozastaveno moc dlouho — vracím úlohu")
                    self.give_back = True
                    self.watch.resume()
                    self.watch.kill()
            if time.time() - last >= REPORT_S:
                self._report()
                last = time.time()

    def run(self) -> None:
        job = self.job
        guard = threading.Thread(target=self._guard, daemon=True)
        guard.start()
        stay_awake(True)
        tmp = Path(self.cfg["work_dir"] or tempfile.gettempdir()) / "lumina-worker" / f"job{job['id']}"
        try:
            src = to_local(job["path"], self.cfg)
            if not os.path.isfile(src):
                raise RuntimeError(f"Soubor na tomto počítači nevidím: {src}")
            opts = dict(job.get("options") or {})
            if self.cfg["threads"]:
                opts["threads"] = self.cfg["threads"]
            opts = {**shrink.options(opts), "crf": (job.get("options") or {}).get("crf")}
            tmp.mkdir(parents=True, exist_ok=True)
            if job["kind"] == "plan":
                self._plan(src, opts, tmp)
            elif job["kind"] == "part":
                self._part(src, opts, tmp)
            else:
                self._shrink(src, opts, tmp)
        except KeyboardInterrupt:
            # closed by its user: the job goes back to Lumina at once (not after the lease runs out)
            self.watch.resume()
            self.watch.kill()
            try:
                self.lumina.call("POST", f"/worker/jobs/{job['id']}/fail", {"give_back": True})
            except (RuntimeError, OSError):
                pass
            raise
        except Exception as e:  # noqa: BLE001 — every failure goes to Lumina
            if self.lost:
                pass
            elif self.give_back:
                self.lumina.call("POST", f"/worker/jobs/{job['id']}/fail", {"give_back": True})
            else:
                log.exception("Úloha %s selhala", job["id"])
                try:
                    self.lumina.call("POST", f"/worker/jobs/{job['id']}/fail", {"error": str(e)[:500]})
                except (RuntimeError, OSError):
                    pass
        finally:
            stay_awake(False)
            self.stop.set()
            guard.join(timeout=10)
            shutil.rmtree(tmp, ignore_errors=True)

    def _plan(self, src: str, opts: dict, tmp: Path) -> None:
        preview = tmp / "preview"
        data = shrink.plan(src, opts, str(tmp / "work"), str(preview), self.progress)
        for shot in data.get("stills") or []:
            for name in shot.values():
                self.lumina.call("POST", f"/worker/jobs/{self.job['id']}/still/{name}", raw=(preview / name).read_bytes())
        r = self.lumina.call("POST", f"/worker/jobs/{self.job['id']}/done", {"data": data}, timeout=120)
        log.info("Náhled hotový: %s", data.get("note") or f"VMAF {data.get('vmaf')}, −{data.get('saving')} %")
        if not r.get("ok"):
            log.warning("Lumina náhled nepřijala: %s", r.get("error"))

    def _part(self, src: str, opts: dict, tmp: Path) -> None:
        """One part of a file the server and the workers encode together: into the job's folder on the share."""
        o = self.job["options"]
        info = shrink.probe(src)
        out = tmp / self.job["out_name"]
        seconds = shrink.encode_part(src, float(o["start"]), int(o["frames"]), info, opts, float(opts.get("crf") or 0),
                                     str(out), self.progress)
        self.progress("upload")
        share_work = Path(to_local(self.job["work"], self.cfg))
        share_work.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out, share_work / out.name)
        r = self.lumina.call("POST", f"/worker/jobs/{self.job['id']}/done",
                             {"data": {"seconds": seconds}, "out_name": out.name}, timeout=300)
        log.info("Část %s/%s: %s", o["idx"] + 1, o.get("count", "?"),
                 f"hotovo za {seconds:.0f} s" if r.get("counted") else r.get("error") or "jiný byl rychlejší")

    def _shrink(self, src: str, opts: dict, tmp: Path) -> None:
        crf = float(opts.get("crf") or 0)
        out, check = shrink.encode(src, crf, opts, str(tmp), self.progress)
        self.progress("upload")
        share_work = Path(to_local(self.job["work"], self.cfg))
        share_work.mkdir(parents=True, exist_ok=True)
        target = share_work / Path(out).name
        shutil.copyfile(out, target)
        r = self.lumina.call("POST", f"/worker/jobs/{self.job['id']}/done",
                             {"data": check, "out_name": target.name}, timeout=1800)
        log.info("Překódováno: %s", "hotovo" if r.get("ok") else r.get("error"))


# ── the loop ──

def job_label(job: dict) -> str:
    name = Path(job["path"].replace("/", os.sep)).name
    if job["kind"] == "part":
        o = job["options"]
        return f"část {o['idx'] + 1}/{o.get('count', '?')} — {name}"
    return f"{'náhled' if job['kind'] == 'plan' else 'zmenšení'} — {name}"


class Engine:
    """The worker's loop: connect, say what it may do, ask for work, run it. In the foreground (python
    lumina_worker.py) or in a thread of the tray app, which reads ``status()`` and may pause or stop it."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lumina = Lumina(cfg)
        self.watch = Watch(cfg)
        self.job: Job | None = None
        self.quit = threading.Event()
        self.name = ""
        self.roots: list[tuple[str, str, bool]] = []      # Lumina's library folders: (server's, here, seen)
        self.state, self.text = "starting", "připojuji se"
        self.phases: dict[str, str] = {}

    # what the tray shows
    def status(self) -> tuple[str, str]:
        """(state, text): working | paused | waiting | idle | offline | starting."""
        job = self.job
        if job:
            label = job_label(job.job)
            if job.paused:
                return "paused", f"Pozastaveno ({job.paused}): {label}"
            phase = self.phases.get(job.phase, job.phase or "začínám")
            value = f" {job.value:.0f} %" if isinstance(job.value, (int, float)) and job.phase == "encode" else ""
            return "working", f"{phase}{value}: {label}"
        return self.state, self.text

    def pause(self, on: bool) -> None:
        """The user's own pause (the tray): no new job, a running one is paused — not given back."""
        self.watch.manual = "pozastaveno ručně" if on else ""

    def stop(self) -> None:
        """Quit: a running job goes back to Lumina at once (another takes it), not after its lease runs out."""
        self.quit.set()
        job = self.job
        if job:
            job.give_back = True
            self.watch.resume()
            self.watch.kill()

    def _set(self, state: str, text: str) -> None:
        if (state, text) != (self.state, self.text):
            log.info("%s", text)
        self.state, self.text = state, text

    def _connect(self, info: dict) -> dict:
        self.lumina.find()
        hello = self.lumina.call("POST", "/worker/hello", {**info, "state": {"waiting": ""}})
        self.name = hello.get("name") or ""
        self.phases = hello.get("phases") or {}
        log.info("Připojeno k Lumině jako „%s“", self.name)
        self.roots = []
        for root in hello.get("roots") or []:
            try:
                local = to_local(root, self.cfg)
                seen = os.path.isdir(local)
            except RuntimeError:
                local, seen = "", False
            self.roots.append((root, local, seen))
            log.info("Knihovna %s = %s %s", root, local or "?", "✓" if seen else "✗ NEVIDÍM — zkontroluj složky")
        return hello

    def run(self) -> None:
        ffmpeg_dir = self.cfg["ffmpeg_dir"] or (str(HERE) if (HERE / "ffmpeg.exe").exists() else "")   # the app's own
        if ffmpeg_dir and ffmpeg_dir not in os.environ["PATH"].split(os.pathsep):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]
        info = machine()
        log.info("Lumina worker %s — %s, %s, %s vláken, %s; kodéry %s; x265 %s", VERSION, info["system"], info["cpu"],
                 info["threads"], info["gpu"] or "bez NVIDIA", ", ".join(info["encoders"]) or "žádné", info["x265"] or "—")
        hello: dict | None = None
        last_hello, last_reason = 0.0, None
        while not self.quit.is_set():
            try:
                if hello is None:
                    self._set("starting", "připojuji se")
                    hello = self._connect(info)
                    last_hello, last_reason = 0.0, None
                self.watch.sample()
                why = self.watch.blocked(working=False)
                if why or time.time() - last_hello > 300 or why != last_reason:
                    self.lumina.call("POST", "/worker/hello", {**info, "state": {"waiting": why}})
                    last_hello, last_reason = time.time(), why
                if why:
                    self._set("waiting", f"Čekám: {why}")
                    self.quit.wait(60 if not self.watch.manual else 5)
                    continue
                self._set("idle", f"Připraven{f' ({self.name})' if self.name else ''} — čekám na práci")
                got = self.lumina.call("GET", f"/worker/next?wait={hello.get('poll_s', 55)}", timeout=90)
                job = got.get("job")
                if job and self.quit.is_set():               # taken while quitting: straight back
                    self.lumina.call("POST", f"/worker/jobs/{job['id']}/fail", {"give_back": True})
                elif job:
                    log.info("Úloha %s: %s", job["id"], job_label(job))
                    self.job = Job(job, self.lumina, self.watch, self.cfg)
                    try:
                        self.job.run()
                    finally:
                        self.job = None
            except (RuntimeError, OSError) as e:
                hello = None if "401" in str(e) or hello is None else hello
                self._set("offline", f"Lumina neodpovídá: {str(e)[:150]}")
                self.quit.wait(60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")])
    cfg, problem = read_config()
    if problem:
        print(problem)
        sys.exit(1)
    try:
        Engine(cfg).run()
    except KeyboardInterrupt:
        log.info("Končím")


if __name__ == "__main__":
    main()
