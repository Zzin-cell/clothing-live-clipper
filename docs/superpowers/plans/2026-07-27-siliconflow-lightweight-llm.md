# SiliconFlow Lightweight LLM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SiliconFlow LLM connectivity fail cleanly on invalid tokens, prefer lightweight fast models, and slim planning requests so LLM plan latency drops without breaking rule-based fallback.

**Architecture:** Keep OpenAI-compatible `user_config/llm.json` + `openai_compat.chat_completions` + `llm_plan` pipeline. Harden auth/retry in `openai_compat` (401 fast-stop + error class). Slim `llm_plan` input (≤150 clauses, shorter system, lower max_tokens/timeout). Prefer SiliconFlow light models in auto-pick and UI placeholders.

**Tech Stack:** Python 3, urllib (existing), pytest, FastAPI Web static UI (`index.html` / `app.js`), no new third-party deps.

**Spec:** `docs/superpowers/specs/2026-07-27-siliconflow-lightweight-llm-design.md`

## Global Constraints

- Speed is priority over maximal LLM plan quality.
- LLM secrets only from `output/user_config/llm.json` (user UI); do not reintroduce env keys for runtime LLM.
- OpenAI-compatible only; no local Ollama in this plan.
- No dual-model upgrade ladder.
- Never invent ASR timestamps/ids when trimming clauses.
- Invalid API keys cannot be “fixed” in code — only clearer failure + fewer retries.
- Rule fallback on LLM failure must remain.
- Default preferred model id: `Qwen/Qwen2.5-7B-Instruct`; default example base: `https://api.siliconflow.cn/v1`.
- Work under `clothing-live-clipper/` for code; plan/spec under repo root `docs/superpowers/`.
- Commits: small, one concern each; Windows cmd-friendly commands.

---

## File map

| File | Responsibility |
|------|----------------|
| `clothing-live-clipper/src/clipper/openai_compat.py` | HTTP chat client: 401 fast-stop, `classify_llm_error`, tighter retries, light model preference |
| `clothing-live-clipper/src/clipper/llm_plan.py` | Light system prompt, `select_clauses_for_llm`, lower max_tokens/timeout, meta counters |
| `clothing-live-clipper/src/clipper/static/index.html` | SiliconFlow placeholder defaults |
| `clothing-live-clipper/src/clipper/static/app.js` | Show mapped auth errors instead of raw multi-401 dump |
| `clothing-live-clipper/tests/test_openai_compat.py` | 401 call budget, error classification, model preference |
| `clothing-live-clipper/tests/test_llm_plan.py` | Clause trim, system length / constants, timeline still works |

---

### Task 1: Auth error classification + 401 fast-stop

**Files:**
- Modify: `clothing-live-clipper/src/clipper/openai_compat.py`
- Test: `clothing-live-clipper/tests/test_openai_compat.py`

**Interfaces:**
- Produces:
  - `classify_llm_error(message: str, *, base_url: str = "") -> dict[str, str]`
    - keys: `error_class`, `message`, `provider_hint` (optional empty)
  - `chat_completions(...)` behavior: on confirmed HTTP 401 auth failure, stop after at most **2** auth variants on the first endpoint (no full payload matrix). Prefer raising/including `error_class=auth_invalid` text that includes Chinese guidance when body has `Token is invalid` or `30014`.
  - Optional helper: `is_auth_invalid_error(msg: str) -> bool`
- Consumes: existing `_http_json`, `auth_header_variants`, `candidate_chat_endpoints`

- [ ] **Step 1: Write failing tests**

Append to `clothing-live-clipper/tests/test_openai_compat.py`:

```python
from unittest.mock import patch

from clipper.openai_compat import (
    OpenAICompatError,
    chat_completions,
    classify_llm_error,
    is_auth_invalid_error,
)


def test_classify_llm_error_siliconflow_token_invalid():
    raw = (
        'HTTP 401 https://api.siliconflow.cn/v1/chat/completions: '
        '{"code":30014,"data":null,"message":"Token is invalid."}'
    )
    info = classify_llm_error(raw, base_url="https://api.siliconflow.cn/v1")
    assert info["error_class"] == "auth_invalid"
    assert "Token" in info["message"] or "无效" in info["message"]
    assert info.get("provider_hint") == "siliconflow"
    assert is_auth_invalid_error(raw) is True


def test_chat_completions_401_stops_quickly():
    calls = {"n": 0}

    def fake_http(url, headers, payload=None, method="POST", timeout=180):
        calls["n"] += 1
        raise OpenAICompatError(
            'HTTP 401 https://api.siliconflow.cn/v1/chat/completions: '
            '{"code":30014,"message":"Token is invalid."}'
        )

    with patch("clipper.openai_compat._http_json", side_effect=fake_http):
        try:
            chat_completions(
                messages=[{"role": "user", "content": "1"}],
                model="Qwen/Qwen2.5-7B-Instruct",
                base_url="https://api.siliconflow.cn/v1",
                api_key="sk-invalid-key-for-test",
                force_json=False,
                timeout=10,
                fast=False,
                cfg={
                    "api_key": "sk-invalid-key-for-test",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "last_endpoint": "",
                    "last_auth_variant": 0,
                    "last_payload_variant": 0,
                    "extra_headers": {},
                    "organization": "",
                },
            )
            assert False, "expected OpenAICompatError"
        except OpenAICompatError as e:
            msg = str(e)
            assert "401" in msg or "auth_invalid" in msg or "Token" in msg or "无效" in msg

    # Must not run full endpoint x auth x payload Cartesian product
    assert calls["n"] <= 4, f"too many HTTP attempts on 401: {calls['n']}"
```

- [ ] **Step 2: Run tests to verify they fail**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_openai_compat.py::test_classify_llm_error_siliconflow_token_invalid tests/test_openai_compat.py::test_chat_completions_401_stops_quickly -v
```

Expected: FAIL (functions missing or 401 still multiplies attempts).

- [ ] **Step 3: Implement classification + 401 fast-stop**

In `openai_compat.py`, add near top (after imports / class):

```python
def is_auth_invalid_error(message: str) -> bool:
    m = (message or "").lower()
    if "http 401" in m or "http 403" in m:
        if any(
            k in m
            for k in (
                "token is invalid",
                "invalid api key",
                "incorrect api key",
                "unauthorized",
                "30014",
                "authentication",
                "invalid_api_key",
            )
        ):
            return True
        # bare 401/403 still counts as auth for stop purposes
        return "http 401" in m
    return False


def classify_llm_error(message: str, *, base_url: str = "") -> dict[str, str]:
    msg = message or ""
    base = (base_url or "").lower()
    provider = "siliconflow" if "siliconflow" in base or "siliconflow" in msg.lower() else ""
    if is_auth_invalid_error(msg) or "token is invalid" in msg.lower() or "30014" in msg:
        user = "Token 无效：请到 SiliconFlow 控制台重新复制 API Key 后保存并重试" if provider == "siliconflow" else "API Key 无效或无权限，请检查 Key 后重试"
        return {"error_class": "auth_invalid", "message": user, "provider_hint": provider}
    if "http 404" in msg.lower() or "http 405" in msg.lower():
        return {"error_class": "endpoint", "message": "接口地址不可用，请检查 Base URL", "provider_hint": provider}
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return {"error_class": "timeout", "message": "LLM 请求超时，将回退规则排片", "provider_hint": provider}
    return {"error_class": "request_failed", "message": msg[:300], "provider_hint": provider}
```

Change the `except OpenAICompatError` branch inside `chat_completions` loops:

```python
                except OpenAICompatError as e:
                    msg = str(e)
                    errors.append(msg)
                    # Auth invalid: try at most one alternate auth on this endpoint, then abort ALL retries
                    if "HTTP 401" in msg or "HTTP 403" in msg:
                        # if this is already the second auth attempt (hi>=1) or message clearly invalid token → stop hard
                        if hi >= 1 or is_auth_invalid_error(msg):
                            info = classify_llm_error(msg, base_url=base)
                            raise OpenAICompatError(
                                f"auth_invalid: {info['message']} | {msg[:200]}"
                            ) from e
                        break  # next auth variant only
                    if "HTTP 404" in msg or "HTTP 405" in msg:
                        hi = len(headers_list)
                        break
                    continue
```

Also, when building final failure after loops, attach classification:

```python
    detail = " | ".join(errors[-6:]) if errors else "unknown"
    info = classify_llm_error(detail, base_url=base)
    if info["error_class"] == "auth_invalid":
        raise OpenAICompatError(f"auth_invalid: {info['message']} | {detail}")
    raise OpenAICompatError(f"all_compat_attempts_failed: {detail}")
```

Tighten `fast=True` path remains; for planning Task 2 will pass slightly lower timeout.

- [ ] **Step 4: Run tests to verify they pass**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_openai_compat.py -v
```

Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bat
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/src/clipper/openai_compat.py clothing-live-clipper/tests/test_openai_compat.py
git commit -F -
```

Commit message body (type into commit when using `-F -` or write temp file):

```
feat(llm): 401 fast-stop and auth_invalid error classification
```

If `-F -` is awkward on Windows, use:

```bat
echo feat(llm): 401 fast-stop and auth_invalid error classification> %TEMP%\cmsg.txt
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/src/clipper/openai_compat.py clothing-live-clipper/tests/test_openai_compat.py
git commit -F %TEMP%\cmsg.txt
```

---

### Task 2: Prefer lightweight models in auto-pick

**Files:**
- Modify: `clothing-live-clipper/src/clipper/openai_compat.py` (`pick_default_model`)
- Test: `clothing-live-clipper/tests/test_openai_compat.py`

**Interfaces:**
- Produces: `pick_default_model(models: list[str], preferred: str | None = None) -> str | None` prefers SiliconFlow-style light instruct ids when preferred empty/missing.
- Consumes: existing model list from `/models`

- [ ] **Step 1: Write failing test**

```python
def test_pick_default_model_prefers_light_qwen():
    models = [
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "deepseek-ai/DeepSeek-R1",
        "THUDM/glm-4-9b-chat",
    ]
    picked = pick_default_model(models, preferred=None)
    assert picked == "Qwen/Qwen2.5-7B-Instruct"

    picked2 = pick_default_model(models, preferred="THUDM/glm-4-9b-chat")
    assert picked2 == "THUDM/glm-4-9b-chat"
```

- [ ] **Step 2: Run to verify fail**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_openai_compat.py::test_pick_default_model_prefers_light_qwen -v
```

Expected: FAIL if current fuzzy rank picks 72B/R1 first.

- [ ] **Step 3: Update `pick_default_model`**

Replace alias / rank lists so light models win. Minimal target logic:

```python
def pick_default_model(models: list[str], preferred: str | None = None) -> str | None:
    if not models:
        return preferred or None
    pref = (preferred or "").strip()
    if pref and pref in models:
        return pref
    lower_map = {m.lower(): m for m in models}
    if pref and pref.lower() in lower_map:
        return lower_map[pref.lower()]

    aliases = [
        pref,
        "Qwen/Qwen2.5-7B-Instruct",
        "THUDM/glm-4-9b-chat",
        "Qwen/Qwen2.5-14B-Instruct",
        "gpt-4o-mini",
        "deepseek-chat",
        "qwen-turbo",
        "grok-4.5",
        "gpt-4o",
    ]
    for a in aliases:
        if not a:
            continue
        if a in models:
            return a
        if a.lower() in lower_map:
            return lower_map[a.lower()]

    # Prefer smaller instruct/chat before huge reasoning models
    rank_keys = [
        "7b-instruct",
        "7b",
        "9b-chat",
        "9b",
        "14b-instruct",
        "4o-mini",
        "turbo",
        "mini",
        "glm-4-9b",
        "qwen2.5-7b",
        "deepseek-chat",
        "instruct",
        "chat",
        "gpt-4o",
        "grok",
        "qwen",
    ]
    for k in rank_keys:
        for m in models:
            ml = m.lower()
            if k in ml and "whisper" not in ml and "embed" not in ml:
                # skip obvious heavy reasoning if lighter exists later in rank — first match is light-first
                if "deepseek-r1" in ml or "72b" in ml or "671b" in ml:
                    continue
                return m
    for m in models:
        ml = m.lower()
        if "whisper" in ml or "embed" in ml:
            continue
        return m
    return models[0]
```

Keep previous tests green: when list is `["whisper-1", "grok-4.5", "text-embedding-3-small"]` and preferred `gpt-4o-mini`, still return `grok-4.5`.

- [ ] **Step 4: Run tests**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_openai_compat.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bat
echo feat(llm): prefer lightweight chat models in auto-pick> %TEMP%\cmsg.txt
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/src/clipper/openai_compat.py clothing-live-clipper/tests/test_openai_compat.py
git commit -F %TEMP%\cmsg.txt
```

---

### Task 3: Clause selection for lightweight planning

**Files:**
- Modify: `clothing-live-clipper/src/clipper/llm_plan.py`
- Test: `clothing-live-clipper/tests/test_llm_plan.py`

**Interfaces:**
- Produces:
  - `LIGHT_MAX_CLAUSES: int = 150`
  - `CLAUSE_TEXT_MAX: int = 120`
  - `select_clauses_for_llm(clauses: list[dict[str, Any]], *, max_clauses: int = LIGHT_MAX_CLAUSES) -> tuple[list[dict[str, Any]], dict[str, Any]]`
    - returns (selected, stats) with stats keys: `clauses_raw`, `clauses_sent`, `dropped_control`, `dropped_size`, `dropped_dup`, `filled_cover`
  - Does **not** rewrite `id` / invent `t0_ms`/`t1_ms`; only filters/reorders subset of existing clause dicts.
- Consumes: `expand_lines_to_clauses` output shape

- [ ] **Step 1: Write failing tests**

```python
from clipper.llm_plan import LIGHT_MAX_CLAUSES, select_clauses_for_llm, expand_lines_to_clauses


def _many_lines(n: int = 80):
    rows = []
    for i in range(n):
        if i % 10 == 0:
            text = "家人们晚上好扣1点关注"
        elif i % 10 == 1:
            text = "建议穿M码偏大一码"
        elif i % 10 == 2:
            text = "这件面料超级软还不透"
        elif i % 10 == 3:
            text = "收腰版型梨形显瘦"
        else:
            text = f"补充一句穿着体验很舒服{i}"
        rows.append(
            {
                "utt_id": f"u{i}",
                "text": text,
                "t0_ms": i * 2000,
                "t1_ms": i * 2000 + 1500,
            }
        )
    return rows


def test_select_clauses_for_llm_caps_and_drops_bad():
    clauses = expand_lines_to_clauses(_many_lines(80), max_clauses=500)
    assert len(clauses) > LIGHT_MAX_CLAUSES or len(clauses) > 100
    selected, stats = select_clauses_for_llm(clauses, max_clauses=150)
    assert stats["clauses_raw"] == len(clauses)
    assert stats["clauses_sent"] == len(selected)
    assert len(selected) <= 150
    assert stats["clauses_sent"] <= stats["clauses_raw"]
    # no invented ids
    raw_ids = {c["id"] for c in clauses}
    assert all(c["id"] in raw_ids for c in selected)
    # size / control should be rare or zero in selection
    joined = " ".join(c["text"] for c in selected)
    assert "M码" not in joined
    assert "扣1" not in joined
    assert stats.get("dropped_size", 0) >= 1 or stats.get("dropped_control", 0) >= 1
```

- [ ] **Step 2: Run to verify fail**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_llm_plan.py::test_select_clauses_for_llm_caps_and_drops_bad -v
```

Expected: FAIL (symbol missing).

- [ ] **Step 3: Implement `select_clauses_for_llm`**

Add constants and function in `llm_plan.py` (after imports / before or after `expand_lines_to_clauses`):

```python
LIGHT_MAX_CLAUSES = 150
CLAUSE_TEXT_MAX = 120

_CONTROL_MARKERS = (
    "家人们", "扣1", "点关注", "晚上好", "欢迎", "公屏", "调试", "对焦", "链接", "小黄车", "加购",
)
_SIZE_MARKERS = ("尺码", "M码", "L码", "m码", "胸围", "腰围", "偏大", "偏小", "建议穿")
_VALUE_MARKERS = (
    "面料", "显瘦", "版型", "收腰", "不透", "透气", "舒服", "垂感", "冰凉", "不闷",
    "细节", "蕾丝", "刺绣", "上身", "遮肉", "梨形", "小个子",
)


def _is_control(text: str) -> bool:
    return any(x in text for x in _CONTROL_MARKERS)


def _is_size(text: str) -> bool:
    return any(x in text for x in _SIZE_MARKERS)


def _value_score(text: str) -> int:
    t = text or ""
    s = 0
    for k in _VALUE_MARKERS:
        if k in t:
            s += 3
    if 4 <= len(t) <= 40:
        s += 1
    if _is_control(t) or _is_size(t):
        s -= 50
    if len(t) < 2:
        s -= 20
    return s


def select_clauses_for_llm(
    clauses: list[dict[str, Any]],
    *,
    max_clauses: int = LIGHT_MAX_CLAUSES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = list(clauses or [])
    stats = {
        "clauses_raw": len(raw),
        "clauses_sent": 0,
        "dropped_control": 0,
        "dropped_size": 0,
        "dropped_dup": 0,
        "filled_cover": 0,
    }
    if not raw:
        return [], stats

    kept: list[dict[str, Any]] = []
    seen_norm: set[str] = set()
    for c in raw:
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        if _is_control(text):
            stats["dropped_control"] += 1
            continue
        if _is_size(text):
            stats["dropped_size"] += 1
            continue
        norm = re.sub(r"\s+", "", text)[:24]
        if norm in seen_norm:
            stats["dropped_dup"] += 1
            continue
        seen_norm.add(norm)
        kept.append(c)

    # score and take best, but preserve chronological order of chosen set
    ranked = sorted(kept, key=lambda c: _value_score(str(c.get("text") or "")), reverse=True)
    hard_cap = max(20, int(max_clauses))
    top = ranked[:hard_cap]
    top_ids = {str(c.get("id")) for c in top}

    # time-cover fill if too sparse
    if len(top) < min(hard_cap, max(30, hard_cap // 2)):
        for c in raw:
            cid = str(c.get("id"))
            if cid in top_ids:
                continue
            text = str(c.get("text") or "")
            if _is_control(text) or _is_size(text):
                continue
            top.append(c)
            top_ids.add(cid)
            stats["filled_cover"] += 1
            if len(top) >= hard_cap:
                break

    # chronological order
    order = {str(c.get("id")): i for i, c in enumerate(raw)}
    selected = sorted(top, key=lambda c: order.get(str(c.get("id")), 10**12))[:hard_cap]
    stats["clauses_sent"] = len(selected)
    return selected, stats
```

- [ ] **Step 4: Run tests**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_llm_plan.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bat
echo feat(llm): select and cap clauses for lightweight planning> %TEMP%\cmsg.txt
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/src/clipper/llm_plan.py clothing-live-clipper/tests/test_llm_plan.py
git commit -F %TEMP%\cmsg.txt
```

---

### Task 4: Wire light prompt + slimmer call_llm_for_plan

**Files:**
- Modify: `clothing-live-clipper/src/clipper/llm_plan.py` (`SYSTEM_PROMPT` light variant, `call_llm_for_plan`)
- Test: `clothing-live-clipper/tests/test_llm_plan.py`

**Interfaces:**
- Produces:
  - `SYSTEM_PROMPT_LIGHT: str` — short hard-rules prompt (Chinese), requires JSON schema keys: product_summary, hook_type, main_points, logic, keep, drop_ids, notes
  - `call_llm_for_plan` uses light prompt, `select_clauses_for_llm`, `text[:CLAUSE_TEXT_MAX]`, `max_tokens=2048`, `timeout=60`, and records trim stats in `_meta`
- Consumes: Task 3 selection + Task 1 chat_completions

- [ ] **Step 1: Write failing tests**

```python
from clipper import llm_plan as lp


def test_system_prompt_light_is_much_shorter():
    assert hasattr(lp, "SYSTEM_PROMPT_LIGHT")
    assert len(lp.SYSTEM_PROMPT_LIGHT) < len(lp.SYSTEM_PROMPT)
    assert len(lp.SYSTEM_PROMPT_LIGHT) < 2200
    assert "JSON" in lp.SYSTEM_PROMPT_LIGHT or "json" in lp.SYSTEM_PROMPT_LIGHT.lower()
    assert "尺码" in lp.SYSTEM_PROMPT_LIGHT


def test_call_llm_for_plan_uses_trim_and_lower_tokens(monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {
            "content": '{"product_summary":"x","hook_type":"pain","main_points":["a"],"logic":["钩子"],"keep":[],"drop_ids":[],"notes":""}',
            "model": kwargs.get("model") or "m",
            "base_url": kwargs.get("base_url") or "https://api.siliconflow.cn/v1",
            "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
            "auth_variant": 0,
            "payload_variant": 0,
            "latency_ms": 1,
        }

    monkeypatch.setattr(lp, "chat_completions", fake_chat)
    monkeypatch.setattr(
        lp,
        "runtime_llm",
        lambda: {
            "enabled": True,
            "plan_enabled": True,
            "api_key": "sk-test-key-xxxxxxxx",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "extra_headers": {},
            "organization": "",
            "last_endpoint": "",
            "last_auth_variant": 0,
            "last_payload_variant": 0,
        },
    )
    lines = _many_lines(60) if "_many_lines" in dir() else [
        {"utt_id": "u1", "text": "这件面料超级软还不透", "t0_ms": 0, "t1_ms": 2000}
    ]
    # ensure helper exists in test file from Task 3; if running isolated, define minimal lines
    try:
        obj = lp.call_llm_for_plan(lines, target_seconds=60, playback_speed=1.4)
    except Exception:
        # empty keep may raise later in plan_from_asr — call_llm_for_plan itself should return obj
        raise
    assert captured.get("max_tokens") == 2048
    assert captured.get("timeout") == 60
    assert captured.get("force_json") is True
    # system should be light
    msgs = captured.get("messages") or []
    assert msgs and msgs[0]["role"] == "system"
    assert len(msgs[0]["content"]) < len(lp.SYSTEM_PROMPT)
    assert obj.get("_meta", {}).get("clauses_sent", 10**9) <= 150
```

If `_many_lines` is only in previous test addition, keep both tests in same file.

Note: current `call_llm_for_plan` may not raise on empty keep (raise is in `plan_from_asr_with_llm`). Test only `call_llm_for_plan`.

- [ ] **Step 2: Run to verify fail**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_llm_plan.py::test_system_prompt_light_is_much_shorter tests/test_llm_plan.py::test_call_llm_for_plan_uses_trim_and_lower_tokens -v
```

Expected: FAIL.

- [ ] **Step 3: Implement light prompt + wire call**

Add `SYSTEM_PROMPT_LIGHT` (complete string — implementer must paste fully):

```python
SYSTEM_PROMPT_LIGHT = """你是服装带货短视频剪辑导演。输入为口播小句(id+时间戳+text)。
任务：先提炼 main_points，再从输入中选 keep 并按成片顺序重排。只使用输入 id，禁止编造时间。

硬规则：
1) 开场 0-3s 仅 1 句最强钩子：视觉冲击/痛点/福利悬念 三选一
2) 删除：打招呼/控场/扣1/闲聊/调试、尺码建议、长段讲价、重复话术只留最优一句
3) 优先：上身效果>面料>价格(价格最多开场一句)
4) 顺序：钩子→版型上身→细节→对比/体验→自然收束；句子必须语义完整，禁止半截
5) 总源片时长接近 target_source_ms；宁可略短也要完整
6) 只输出严格 JSON，不要 markdown

JSON:
{"product_summary":"...","hook_type":"visual|pain|welfare","main_points":["..."],"logic":["钩子","版型上身","细节","对比体验","收束"],"keep":[{"id":"c00012","t0_ms":0,"t1_ms":1,"text":"...","why":"...","point":"...","complete":true}],"drop_ids":["c00001"],"notes":"..."}
"""
```

In `call_llm_for_plan`, after expanding clauses:

```python
    clauses_all = expand_lines_to_clauses(lines, max_clauses=420)
    if not clauses_all:
        raise RuntimeError("empty_transcript")
    clauses, trim_stats = select_clauses_for_llm(clauses_all, max_clauses=LIGHT_MAX_CLAUSES)

    compact = [
        {
            "id": u["id"],
            "parent_id": u.get("parent_id"),
            "t0_ms": u["t0_ms"],
            "t1_ms": u["t1_ms"],
            "text": str(u["text"])[:CLAUSE_TEXT_MAX],
        }
        for u in clauses
    ]
```

Change messages system content to `SYSTEM_PROMPT_LIGHT`.

Change chat call:

```python
        out = chat_completions(
            messages=messages,
            model=model,
            base_url=base,
            api_key=key,
            temperature=0.2,
            max_tokens=2048,
            force_json=True,
            timeout=60,
            cfg=cfg,
            fast=True,  # prefer last_route; still falls back within tightened auth rules
        )
```

Extend `_meta`:

```python
    obj["_meta"] = {
        ...
        "input_clauses_raw": trim_stats.get("clauses_raw"),
        "input_clauses": len(clauses),
        "clauses_sent": trim_stats.get("clauses_sent"),
        "trim_stats": trim_stats,
        "submit_mode": "light_asr_selected_clauses",
        ...
    }
    # keep FULL raw expanded clauses for neighbor completion if desired:
    # Spec: timeline mapping may still use selected-only OR full.
    # Decision: store selected as _clauses for id resolution consistency with what LLM saw;
    # also stash raw for optional completion:
    obj["_clauses"] = clauses
    obj["_clauses_raw"] = clauses_all
```

Update user_payload policy `submit_mode` to `light_asr_selected_clauses`.

Optional: in `llm_obj_to_timeline`, prefer `_clauses` then `_clauses_raw` for neighbor completion — if implementer chooses selected-only, neighbor completion still works within selected set (acceptable for speed priority).

- [ ] **Step 4: Run tests**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_llm_plan.py tests/test_openai_compat.py -v
```

Expected: PASS. If empty keep causes later code path issues, only test `call_llm_for_plan` return + meta.

- [ ] **Step 5: Commit**

```bat
echo feat(llm): light system prompt and slimmer plan request> %TEMP%\cmsg.txt
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/src/clipper/llm_plan.py clothing-live-clipper/tests/test_llm_plan.py
git commit -F %TEMP%\cmsg.txt
```

---

### Task 5: UI defaults + friendlier probe errors

**Files:**
- Modify: `clothing-live-clipper/src/clipper/static/index.html`
- Modify: `clothing-live-clipper/src/clipper/static/app.js`
- Optional touch: `clothing-live-clipper/src/clipper/openai_compat.py` `ping()` to return `error_class` / mapped message (preferred)

**Interfaces:**
- Produces: UI placeholders for SiliconFlow; probe failure shows Chinese auth message when error contains `auth_invalid` / `Token is invalid` / `30014`
- Consumes: `/api/system/probe` JSON `{ok, probe:{error,...}}`

- [ ] **Step 1: Update HTML placeholders**

In `index.html` LLM panel:

```html
<input type="text" id="llm_base_url" placeholder="https://api.siliconflow.cn/v1" />
...
<input type="password" id="llm_api_key" placeholder="硅基流动 API Key（sk-...）" autocomplete="off" />
...
<input type="text" id="llm_model" placeholder="Qwen/Qwen2.5-7B-Instruct（可自动匹配）" list="llm-model-list" />
...
<p class="jy-hint" id="llm-cfg-msg">推荐 SiliconFlow 轻量模型。填 Base URL + Key + Model 后点「测试连通」。Token 无效时请重新复制 Key。</p>
```

Also shorten intro line if needed:

```html
<p class="jy-hint llm-intro">OpenAI 兼容。推荐 https://api.siliconflow.cn/v1 + Qwen/Qwen2.5-7B-Instruct。配置仅存本机用户文件，不读环境变量。</p>
```

- [ ] **Step 2: Map probe errors in app.js**

In `llm-probe` handler where failure message is set, replace raw dump with helper:

```javascript
function formatLlmProbeError(probe, data) {
  const raw = String((probe && (probe.error || probe.detail)) || data.detail || "unknown");
  if (
    /auth_invalid|Token is invalid|30014|API Key 无效|Token 无效/i.test(raw)
  ) {
    return "Token 无效：请到 SiliconFlow 控制台重新复制 API Key 后保存并重试";
  }
  if (/missing_api_key|missing_llm/i.test(raw)) {
    return "请先填写并保存 API Key";
  }
  // keep short
  return raw.length > 220 ? raw.slice(0, 220) + "…" : raw;
}
```

Use:

```javascript
msg.textContent = ok
  ? `连通成功 · ${modelName} · API延迟 ${total}ms${endpoint ? ` · ${endpoint}` : ""}`
  : `连通失败 · ${total}ms：${formatLlmProbeError(probe, data)}`;
```

- [ ] **Step 3: Optional backend ping mapping**

In `openai_compat.ping` except / fail branches, if error string matches auth:

```python
info = classify_llm_error(str(e), base_url=base_url or "")
return {
    "ok": False,
    "error": info["message"] if info["error_class"] == "auth_invalid" else str(e)[:500],
    "error_class": info["error_class"],
    ...
}
```

- [ ] **Step 4: Smoke unit tests still pass (no browser automation required)**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_openai_compat.py tests/test_llm_plan.py tests/test_user_llm.py -q
```

Expected: PASS.

Manual check list (document in commit body if not automated):
1. Open Web → LLM panel shows SiliconFlow placeholders  
2. Invalid key → short Chinese Token message  
3. Valid key (if available) → probe OK + light model selectable  

- [ ] **Step 5: Commit**

```bat
echo feat(ui): SiliconFlow defaults and clearer LLM auth errors> %TEMP%\cmsg.txt
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/src/clipper/static/index.html clothing-live-clipper/src/clipper/static/app.js clothing-live-clipper/src/clipper/openai_compat.py
git commit -F %TEMP%\cmsg.txt
```

---

### Task 6: Regression gate + ARCHITECTURE note

**Files:**
- Modify: `clothing-live-clipper/docs/ARCHITECTURE.md` (short bullet under LLM section)
- Run full focused tests

- [ ] **Step 1: Add architecture note**

In `ARCHITECTURE.md` §2 after LLM bullet list, add:

```markdown
轻量模式（SiliconFlow 推荐）：
- 默认示例 Base：`https://api.siliconflow.cn/v1`，优先轻量 Instruct（如 Qwen2.5-7B）
- 规划请求裁剪小句（约 ≤150）+ 短 system；`max_tokens=2048`、timeout≈60s
- HTTP 401 Token invalid：快速失败并提示更新 Key，避免兼容层穷举重试
```

- [ ] **Step 2: Run regression**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
pytest tests/test_openai_compat.py tests/test_llm_plan.py tests/test_user_llm.py tests/test_rank.py -q
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bat
echo docs: note SiliconFlow lightweight LLM path in ARCHITECTURE> %TEMP%\cmsg.txt
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/docs/ARCHITECTURE.md
git commit -F %TEMP%\cmsg.txt
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| 401 fast-stop + Token invalid UX | Task 1, Task 5 |
| Prefer light SiliconFlow models | Task 2, Task 5 placeholders |
| Slim system / ≤150 clauses / max_tokens 2048 / timeout 45–60 | Task 3–4 (timeout 60) |
| last_route / fewer blind retries | Task 1 + `fast=True` in Task 4 |
| No invented timestamps | Task 3 rules |
| Rule fallback unchanged | No change to job_worker except benefiting from faster fail (existing path) |
| Tests S1–S5 coverage | Tasks 1–4 unit; S5 size/control drop tests; manual S1/S2 residual |
| Non-goals (no Ollama, no dual model) | Respected |

Placeholder scan: no TBD/TODO left for implementers; code blocks included for each code step.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-siliconflow-lightweight-llm.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with `executing-plans`, batch + checkpoints  

Which approach?
