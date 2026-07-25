from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

base = (os.getenv("CLIPPER_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
key = (os.getenv("CLIPPER_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
model = (os.getenv("CLIPPER_LLM_MODEL") or "gpt-4o-mini").strip()
print("base", base)
print("model_cfg", model)
print("key_len", len(key))

headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

# list models
try:
    req = urllib.request.Request(f"{base}/models", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    ids = []
    for m in data.get("data") or []:
        mid = m.get("id")
        if mid:
            ids.append(mid)
    print("models_count", len(ids))
    # print likely chat models
    prefer = [x for x in ids if any(k in x.lower() for k in ("gpt", "claude", "deepseek", "qwen", "gemini", "mini", "flash", "chat"))]
    print("prefer_sample:")
    for x in prefer[:40]:
        print(" -", x)
    if not prefer:
        for x in ids[:30]:
            print(" -", x)
except Exception as e:
    print("list_models_error", type(e).__name__, e)

# probe a few candidates
candidates = [model, "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "deepseek-chat", "qwen-plus", "qwen2.5-72b-instruct"]
# unique preserve
seen = set()
cands = []
for c in candidates:
    if c and c not in seen:
        seen.add(c)
        cands.append(c)

print("\nprobe chat completions:")
for m in cands:
    payload = {
        "model": m,
        "temperature": 0,
        "messages": [{"role": "user", "content": "reply with ok only"}],
        "max_tokens": 8,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = json.loads(r.read().decode("utf-8", errors="replace"))
        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        print("OK", m, "->", repr(content)[:80])
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print("FAIL", m, e.code, err[:180].replace("\n", " "))
    except Exception as e:
        print("FAIL", m, type(e).__name__, str(e)[:180])
