from __future__ import annotations

import json

import httpx

from clipper.config import resolve_api_key, resolve_asr_base_url


def main() -> None:
    key = resolve_api_key()
    base = resolve_asr_base_url()
    url = f"{base}/models"
    r = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    print("status", r.status_code)
    if r.status_code >= 400:
        print(r.text[:1000])
        return
    data = r.json()
    items = data.get("data") or data.get("models") or []
    if not isinstance(items, list):
        print(json.dumps(data, ensure_ascii=False)[:2000])
        return
    names = []
    for it in items:
        if isinstance(it, dict):
            names.append(str(it.get("id") or it.get("name") or it))
        else:
            names.append(str(it))
    names = sorted(set(names))
    # prioritize likely asr / whisper / audio
    keys = ("whisper", "asr", "audio", "speech", "transcri", "sensevoice", "paraformer", "funasr")
    print("TOTAL", len(names))
    print("--- likely ASR/audio ---")
    for n in names:
        low = n.lower()
        if any(k in low for k in keys):
            print(n)
    print("--- sample first 80 ---")
    for n in names[:80]:
        print(n)


if __name__ == "__main__":
    main()
