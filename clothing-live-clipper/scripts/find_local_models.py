from pathlib import Path
import os

homes = [
    Path.home() / ".cache" / "huggingface",
    Path.home() / ".cache" / "whisper",
    Path(os.environ.get("LOCALAPPDATA", "")) / "whisper",
    Path(r"C:\Users\MR\AppData\grok"),
]
for h in homes:
    print("===", h, "exists", h.exists())
    if not h.exists():
        continue
    for p in h.rglob("*"):
        name = p.name.lower()
        if p.is_file() and (
            "whisper" in name
            or name.endswith(".bin")
            or name.endswith(".pt")
            or name == "model.bin"
        ):
            if p.stat().st_size > 1_000_000:
                print(p, p.stat().st_size)
