from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from clipper.openai_compat import chat_completions, list_models, normalize_base_url, ping
from clipper.user_llm import auth_header_variants, runtime_llm

rt = runtime_llm()
base = rt.get("base_url")
model = rt.get("model")
key = rt.get("api_key") or ""
print("base", base)
print("model", model)
print("key_len", len(key), "head", key[:6], "tail", key[-4:])
print("norm", normalize_base_url(base or ""))

# raw single request
url = normalize_base_url(base or "") + "/chat/completions"
payload = {
    "model": model or "deepseek-chat",
    "messages": [{"role": "user", "content": "reply with ok only"}],
    "max_tokens": 8,
    "temperature": 0,
}
print("\n=== raw attempts ===")
for i, headers in enumerate(auth_header_variants(rt)):
    # don't print key
    safe = {k: ("***" if "key" in k.lower() or k.lower()=="authorization" else v) for k,v in headers.items()}
    print("auth", i, safe)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print("OK", resp.status, "ms", int((time.perf_counter()-t0)*1000), body[:200])
            break
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", errors="replace")
        print("HTTP", e.code, "ms", int((time.perf_counter()-t0)*1000), b[:300])
    except Exception as e:
        print("ERR", type(e).__name__, e)

print("\n=== list_models ===")
t1 = time.perf_counter()
ms = list_models(base_url=base, api_key=key, timeout=25)
print("count", len(ms), "ms", int((time.perf_counter()-t1)*1000), ms[:20])

print("\n=== chat_completions helper ===")
t2 = time.perf_counter()
try:
    out = chat_completions(
        messages=[{"role": "user", "content": "reply with ok only"}],
        model=model,
        base_url=base,
        api_key=key,
        temperature=0,
        max_tokens=8,
        force_json=False,
        timeout=45,
    )
    print("OK", {k: out.get(k) for k in ("model","endpoint","content","auth_variant","payload_variant")})
    print("ms", int((time.perf_counter()-t2)*1000))
except Exception as e:
    print("FAIL", e)
    print("ms", int((time.perf_counter()-t2)*1000))

print("\n=== ping helper ===")
t3 = time.perf_counter()
p = ping(base_url=base, api_key=key, model=model, timeout=45, auto_pick_model=True)
p2 = dict(p)
if "models" in p2:
    p2["models"] = (p2.get("models") or [])[:10]
print(json.dumps(p2, ensure_ascii=False, indent=2))
print("ping_wall_ms", int((time.perf_counter()-t3)*1000))
