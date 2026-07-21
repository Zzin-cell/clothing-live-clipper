import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agent_clip_video import resolve_local_model

m = resolve_local_model()
print("model", m)
p = Path(m)
print("exists", p.exists())
if p.exists():
    for f in sorted(p.iterdir()):
        print(f.name, f.stat().st_size)
