import json
from pathlib import Path
p = Path(r"C:\Users\MR\AppData\grok\.git\releases.json")
if not p.exists():
    print("missing")
else:
    d = json.loads(p.read_text(encoding="utf-8"))
    for x in d:
        print(x.get("tag_name"), x.get("html_url"))
