from pathlib import Path
import os

p = Path(r"C:\Users\MR\Desktop\检查文件\学习2.0\新建文件夹 (18)\新建文件夹")
print("exists", p.exists(), p)
if not p.exists():
    parent = Path(r"C:\Users\MR\Desktop\检查文件\学习2.0")
    print("parent exists", parent.exists())
    if parent.exists():
        for x in parent.rglob("*"):
            if x.is_dir():
                print("DIR", x)
            elif x.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".ts", ".txt", ".json", ".md"}:
                print("FILE", x, x.stat().st_size)
    raise SystemExit(0)

for root, dirs, files in os.walk(p):
    r = Path(root)
    print("DIR", r)
    for f in files:
        fp = r / f
        print(f"  {f}\t{fp.stat().st_size}\t{fp.suffix}")
