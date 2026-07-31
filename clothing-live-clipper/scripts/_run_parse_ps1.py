from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
ps = root / "scripts" / "_parse_ps1.ps1"
files = [
    root / "pack" / "portable" / "install_all.ps1",
    root / "pack" / "portable" / "start_service.ps1",
    root / "pack" / "portable" / "ensure_ready.ps1",
]
code = 0
for f in files:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(ps), str(f)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f.name, "exit", r.returncode)
    print((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        code = 1
raise SystemExit(code)
