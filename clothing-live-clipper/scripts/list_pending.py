from pathlib import Path
src = Path(r"C:\Users\MR\Desktop\检查文件\待剪辑")
print("exists", src.exists())
if src.exists():
    for p in sorted(src.iterdir()):
        print(p.name, p.stat().st_size if p.is_file() else "DIR")
