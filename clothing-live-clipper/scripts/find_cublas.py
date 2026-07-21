from pathlib import Path
import os

roots = [
    Path(r"C:\Windows\System32"),
    Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit"),
    Path(r"C:\Program Files\NVIDIA Corporation"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Packages",
]
names = ["cublas64_12.dll", "cublas64_11.dll", "cudart64_12.dll"]
for root in roots:
    if not root.exists():
        continue
    print("scan", root)
    try:
        for n in names:
            for p in root.rglob(n):
                print("FOUND", p)
    except Exception as e:
        print("err", root, e)
