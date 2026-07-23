from pathlib import Path

p = Path(r"C:\Users\MR\AppData\grok\models")
files = [f for f in p.rglob("*") if f.is_file()] if p.exists() else []
total = sum(f.stat().st_size for f in files)
print("exists", p.exists())
print("files", len(files))
print("MB", round(total / 1024 / 1024, 1))
for f in sorted(files, key=lambda x: -x.stat().st_size)[:12]:
    print(round(f.stat().st_size / 1024 / 1024, 1), f)
