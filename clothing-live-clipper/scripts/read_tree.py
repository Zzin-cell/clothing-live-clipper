from pathlib import Path
p = Path(r"C:\Users\MR\AppData\grok\models\tree.json")
print("exists", p.exists(), "size", p.stat().st_size if p.exists() else 0)
if p.exists():
    print(p.read_text(encoding="utf-8", errors="replace")[:3000])
