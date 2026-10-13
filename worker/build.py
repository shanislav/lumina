"""Builds the worker's Windows app: dist/Lumina Worker/ (the .exe with ffmpeg inside) and a zip of it.

    py -m venv build/venv && build/venv/Scripts/python -m pip install pyinstaller pystray pillow psutil
    build/venv/Scripts/python build.py

(a plain venv of a python.org Python — its Tcl/Tk goes into the app; uv's environments add the global packages)

ffmpeg and ffprobe are taken from PATH (or FFMPEG_DIR): version 9 with libx265 and libvmaf — the same as the
server's, so that a worker's encodes and measurements are the server's."""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def tool(name: str) -> Path:
    found = Path(os.environ["FFMPEG_DIR"]) / f"{name}.exe" if os.environ.get("FFMPEG_DIR") else shutil.which(name)
    if not found:
        sys.exit(f"{name} není v PATH (nebo nastav FFMPEG_DIR)")
    return Path(found).resolve()                      # winget's Links are symbolic links


def main() -> None:
    # a virtual environment (uv) does not find Tcl/Tk of its Python — PyInstaller would leave the settings window out
    tcl = Path(sys.base_prefix) / "tcl"
    os.environ.setdefault("TCL_LIBRARY", str(tcl / "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", str(tcl / "tk8.6"))
    import lumina_tray
    import lumina_worker
    build = HERE / "build"
    build.mkdir(exist_ok=True)
    icon = build / "icon.ico"
    lumina_tray.icon_image("idle", 256).save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    ffmpeg, ffprobe = tool("ffmpeg"), tool("ffprobe")
    version = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    print(f"ffmpeg: {version}")
    sep = ";" if os.name == "nt" else ":"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "Lumina Worker",
                    "--icon", str(icon), "--distpath", str(HERE / "dist"), "--workpath", str(build / "pyi"),
                    "--specpath", str(build), "--paths", str(HERE.parent / "backend" / "app" / "modules" / "convert"),
                    "--hidden-import", "shrink", "--hidden-import", "pystray._win32",
                    "--add-binary", f"{ffmpeg}{sep}.", "--add-binary", f"{ffprobe}{sep}.",
                    str(HERE / "lumina_tray.py")], check=True)
    app = HERE / "dist" / "Lumina Worker"
    archive = HERE / "dist" / f"Lumina-Worker-{lumina_worker.VERSION}-windows.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in app.rglob("*"):
            z.write(p, Path("Lumina Worker") / p.relative_to(app))
    print(f"Hotovo: {app}\\Lumina Worker.exe\n        {archive} ({archive.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
