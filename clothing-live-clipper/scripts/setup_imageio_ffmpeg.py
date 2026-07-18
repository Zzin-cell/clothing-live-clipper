import os
import shutil
import subprocess
import sys
from pathlib import Path

def main() -> int:
    try:
        import imageio_ffmpeg
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "imageio-ffmpeg", "-q"])
        import imageio_ffmpeg

    src = Path(imageio_ffmpeg.get_ffmpeg_exe())
    print("source", src, "exists", src.exists())
    if not src.exists():
        print("MISSING_SOURCE")
        return 1

    dest_dir = Path(os.environ["LOCALAPPDATA"]) / "ffmpeg" / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "ffmpeg.exe"
    shutil.copy2(src, dest)
    print("copied", dest)

    # User PATH
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
    )
    try:
        current, _ = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        current = ""
    parts = [p for p in current.split(";") if p]
    bin_s = str(dest_dir)
    if bin_s not in parts:
        parts.append(bin_s)
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
        print("PATH_UPDATED")
    else:
        print("PATH_OK")
    winreg.CloseKey(key)

    # verify
    out = subprocess.check_output([str(dest), "-version"], text=True, stderr=subprocess.STDOUT)
    print(out.splitlines()[0])
    print("INSTALL_OK", bin_s)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
