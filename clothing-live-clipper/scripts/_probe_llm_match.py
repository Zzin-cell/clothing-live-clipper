from __future__ import annotations

import json
from pathlib import Path

from clipper.openai_compat import discover_models_and_pick, list_models, pick_default_model
from clipper.user_llm import USER_CFG_PATH, load_user_llm, public_user_llm

print("USER_CFG_PATH", USER_CFG_PATH)
print("exists", USER_CFG_PATH.exists())
d = load_user_llm()
pub = public_user_llm()
print(
    "cfg",
    {
        "base_url": d.get("base_url"),
        "model": d.get("model"),
        "has_key": bool(d.get("api_key")),
        "key_tail": (d.get("api_key") or "")[-4:],
        "enabled": d.get("enabled"),
        "plan_enabled": d.get("plan_enabled"),
    },
)
print(
    "public",
    {k: pub.get(k) for k in ("has_key", "plan_ready", "base_url", "model", "store")},
)

base = str(d.get("base_url") or "").strip()
key = str(d.get("api_key") or "").strip()
if not base or not key:
    print("SKIP live discover: missing base/key")
else:
    models = list_models(base_url=base, api_key=key, timeout=40)
    print("models_count", len(models))
    print("sample", models[:12])
    picked = pick_default_model(models, preferred=d.get("model") or "Qwen/Qwen2.5-7B-Instruct")
    print("picked", picked)
    disc = discover_models_and_pick(
        base_url=base,
        api_key=key,
        preferred=d.get("model") or "Qwen/Qwen2.5-7B-Instruct",
        timeout=40,
    )
    print("discover", {k: disc.get(k) for k in ("ok", "count", "picked", "base_url")})
