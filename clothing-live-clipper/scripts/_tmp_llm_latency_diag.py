# -*- coding: utf-8 -*-
"""Read-only diagnosis: why LLM may be slower than before."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ["USERPROFILE"])
CANDIDATES = [
    HOME / "AppData" / "grok" / "clothing-live-clipper" / "output" / "user_config" / "llm.json",
    HOME / "Desktop" / "小面CapCut-便携版" / "output" / "user_config" / "llm.json",
    HOME / "Desktop" / "小面CapCut-便携版" / "clothing-live-clipper" / "output" / "user_config" / "llm.json",
    # dirty old package
    HOME / "Desktop" / "小面CapCut-便携版 (3)" / "小面CapCut-便携版" / "output" / "user_config" / "llm.json",
    HOME / "Desktop" / "小面CapCut-便携版 (3)" / "小面CapCut-便携版" / "clothing-live-clipper" / "output" / "user_config" / "llm.json",
    Path(os.environ.get("TEMP", "")) / "XiaomianFullBlankTest" / "XiaomianCapCut" / "output" / "user_config" / "llm.json",
]


def mask(k: str) -> str:
    k = (k or "").strip()
    if not k:
        return "(empty)"
    if len(k) <= 8:
        return k[:2] + "***"
    return k[:4] + "..." + k[-4:]


def ping_url(url: str, timeout: float = 8.0) -> tuple[bool, int, str]:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "xiaomian-diag/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(64)
            ms = int((time.perf_counter() - t0) * 1000)
            return True, ms, f"HTTP {r.status}"
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return False, ms, f"{type(e).__name__}: {e}"


def main() -> None:
    print("=== local service 8787 ===")
    ok, ms, msg = ping_url("http://127.0.0.1:8787/api/health")
    print(f"health: ok={ok} {ms}ms {msg}")

    print("\n=== llm.json candidates ===")
    found = []
    for p in CANDIDATES:
        if p.exists():
            found.append(p)
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"\n{p}\n  JSON error: {e}")
                continue
            print(f"\n{p}")
            print("  enabled:", data.get("enabled"), "plan_enabled:", data.get("plan_enabled"))
            print("  base_url:", data.get("base_url"))
            print("  model:", data.get("model"))
            print("  api_key:", mask(str(data.get("api_key") or "")))
            print("  last_endpoint:", data.get("last_endpoint") or "(none)")
            print("  last_auth_variant:", data.get("last_auth_variant"))
            print("  last_payload_variant:", data.get("last_payload_variant"))
            print("  last_ok_ms:", data.get("last_ok_ms"))
            bu = str(data.get("base_url") or "")
            mdl = str(data.get("model") or "").lower()
            if "api.openai.com" in bu.lower():
                print("  WARN: Base is OpenAI official — often very slow/timeout in CN")
            if any(k in mdl for k in ("qwen3", "r1", "72b", "32b", "reasoner")):
                print("  WARN: heavy/thinking model family may be much slower than 7B instruct")
            if not data.get("last_endpoint"):
                print("  NOTE: no last_endpoint cache → first probes explore multi-route (slower)")
        else:
            print("missing:", p)

    if not found:
        print("\nNo llm.json found in common locations. Config only exists after 保存并启用.")

    print("\n=== rough gateway reachability (no key sent) ===")
    for u in (
        "https://api.siliconflow.cn/v1/models",
        "https://api.openai.com/v1/models",
    ):
        ok, ms, msg = ping_url(u, timeout=10)
        # 401 without key still means reachable
        print(f"{u}\n  okish_reachable={ok or 'HTTP' in msg} {ms}ms {msg}")

    print("\n=== interpretation ===")
    print("- If last_ok_ms was low before but now last_endpoint empty + new package path: new user_config, cold start.")
    print("- If model changed to qwen3/r1/large: expect multi-second regression.")
    print("- If base became openai.com: expect large regression in CN.")
    print("- If 8787 down during test: UI Failed to fetch (not LLM latency).")


if __name__ == "__main__":
    main()
