from pathlib import Path
import os

p = Path(r"C:\Users\MR\Desktop\检查文件\学习文件\新建文件夹")
print("exists", p.exists(), p)
if not p.exists():
    # try parent
    parent = p.parent
    print("parent", parent, parent.exists())
    if parent.exists():
        for x in parent.iterdir():
            print(" -", x.name, "dir" if x.is_dir() else x.stat().st_size)
    raise SystemExit(0)

for root, dirs, files in os.walk(p):
    r = Path(root)
    print("DIR", r)
    for f in files:
        fp = r / f
        print(f"  {f}\t{fp.stat().st_size}")
