from pathlib import Path
for p in [
    Path(r"C:\Users\MR\Desktop\检查文件\已经完成\001\final.mp4"),
    Path(r"C:\Users\MR\Desktop\检查文件\已经完成\002\final.mp4"),
]:
    print(p.exists(), p.stat().st_size if p.exists() else 0, p)
