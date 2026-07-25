from __future__ import annotations

import json
import time
from pathlib import Path

from clipper.openai_compat import discover_models_and_pick, list_models, ping
from clipper.user_llm import USER_CFG_PATH, public_user_llm, runtime_llm

print("store", USER_CFG_PATH, "exists", USER_CFG_PATH.exists())
if USER_CFG_PATH.exists():
    raw = json.loads(USER_CFG_PATH.read_text(encoding="utf-8"))
    safe = dict(raw)
    k = str(safe.get("api_key") or "")
    safe["api_key"] = f"len={len(k)} head={k[:8]!r} tail={k[-6:]!r}" if k else ""
    print("raw_user_cfg", json.dumps(safe, ensure_ascii=False, indent=2))

print("public", json.dumps(public_user_llm(), ensure_ascii=False, indent=2))
rt = runtime_llm()
print("runtime base", rt.get("base_url"))
print("runtime model", rt.get("model"))
print("runtime key_len", len(rt.get("api_key") or ""))

print("\n=== list_models ===")
t0 = time.perf_counter()
try:
    models = list_models(base_url=rt.get("base_url"), api_key=rt.get("api_key"), timeout=25)
    print("models", len(models), "ms", int((time.perf_counter() - t0) * 1000))
    print("sample", models[:20])
except Exception as e:
    print("list_models_error", e)

print("\n=== discover ===")
t1 = time.perf_counter()
disc = discover_models_and_pick(
    base_url=rt.get("base_url"),
    api_key=rt.get("api_key"),
    preferred=rt.get("model"),
    timeout=25,
)
print(json.dumps({**disc, "models": (disc.get("models") or [])[:20]}, ensure_ascii=False, indent=2))
print("discover_ms", int((time.perf_counter() - t1) * 1000))

print("\n=== ping ===")
t2 = time.perf_counter()
p = ping(
    base_url=rt.get("base_url"),
    api_key=rt.get("api_key"),
    model=rt.get("model"),
    timeout=45,
    auto_pick_model=True,
)
p2 = dict(p)
if "models" in p2:
    p2["models"] = (p2.get("models") or [])[:20]
print(json.dumps(p2, ensure_ascii=False, indent=2))
print("ping_total_ms", int((time.perf_counter() - t2) * 1000))
