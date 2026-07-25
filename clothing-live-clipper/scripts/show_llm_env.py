from pathlib import Path
from clipper.config import resolve_llm_base_url, resolve_llm_key, resolve_llm_model, llm_status

env = Path(__file__).resolve().parents[1] / ".env"
print("env_path", env, "exists", env.exists())
if env.exists():
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "LLM" in s or "PLAYBACK" in s or "OPENAI" in s:
            if "KEY" in s.upper():
                k, _, v = s.partition("=")
                v = v.strip()
                print(f"{k}=***{v[-4:] if len(v)>=4 else ''} (len={len(v)})")
            else:
                print(s)
print("resolve_model", resolve_llm_model())
print("resolve_base", resolve_llm_base_url())
k = resolve_llm_key()
print("resolve_key_len", len(k))
print("llm_status", llm_status())
