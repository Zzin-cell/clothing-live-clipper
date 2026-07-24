"""
Human-in-the-loop learning (Plan D).

Every time the user edits plan / transcript and re-cuts, we record:
- kept lines (positive)
- dropped lines (negative, if baseline plan available)
- preferred hook phrases / tokens

Later ranking reads this store and boosts/penalizes similar speech so the system
gradually behaves more like the human editor.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEARN_DIR = ROOT / "output" / "learning"
PREF_PATH = LEARN_DIR / "preferences.json"
EVENTS_PATH = LEARN_DIR / "events.jsonl"

# lightweight Chinese token-ish units (2-grams + keywords)
_STOP = set(
    "的了呢啊哦嗯吧呀嘛是就很也又还把被在有和与及或这个那个什么一下一些"
    "我们你们他们她们它们因为所以但是然后还有就是可以一个"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dir() -> None:
    LEARN_DIR.mkdir(parents=True, exist_ok=True)


def _default_prefs() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "stats": {
            "events": 0,
            "kept_slots": 0,
            "dropped_slots": 0,
            "hook_slots": 0,
        },
        # phrase/token -> weight
        "keep_boost": {},
        "drop_penalty": {},
        "hook_boost": {},
        "recent_cases": [],
    }


def load_preferences() -> dict[str, Any]:
    _ensure_dir()
    if not PREF_PATH.exists():
        return _default_prefs()
    try:
        data = json.loads(PREF_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_prefs()
        base = _default_prefs()
        base.update({k: data.get(k, base.get(k)) for k in base})
        for k in ("keep_boost", "drop_penalty", "hook_boost", "stats"):
            if not isinstance(base.get(k), dict):
                base[k] = _default_prefs()[k]
        if not isinstance(base.get("recent_cases"), list):
            base["recent_cases"] = []
        return base
    except Exception:
        return _default_prefs()


def save_preferences(prefs: dict[str, Any]) -> None:
    _ensure_dir()
    prefs["updated_at"] = _utc_now()
    PREF_PATH.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


_FEATURE_SEED = (
    "面料", "版型", "显瘦", "遮肉", "不透", "柔软", "超软", "软到", "垂感", "弹力",
    "收腰", "修身", "高腰", "梨形", "闭眼入", "天丝", "醋酸", "雪纺", "纯棉",
    "蕾丝", "破洞", "拼接", "凉感", "不起球", "可机洗", "抗皱", "显白", "独家",
    "专利", "限定", "百搭", "通勤", "牛仔", "裙子", "上衣", "外套", "无袖",
    "遮肚子", "遮胯", "显腿长", "不挑人", "好打理", "高级感", "凉快", "透气",
    "不闷", "不起球", "不缩水", "不掉色",
)

# high-signal single Chinese chars for clothing selling speech
_FEATURE_CHARS = set(
    "瘦透软弹垂薄厚凉热透气闷皱白黑粉蓝绿灰杏米咖"
    "裙裤袖腰胯肚腿肩领扣袋缝线料布丝棉麻牛仔版型"
)

_LIVE_CHARS = set("家扣粉弹幕袋券码价车链")  # weak alone; used with context

_GENERIC_SKIP = {
    "这个", "那个", "一个", "我们", "你们", "什么", "然后", "因为", "所以", "但是",
    "宝贝", "姐妹", "其实", "而且", "非常", "可以", "还是", "就是", "一下", "一些",
    "不会", "感觉", "更加", "如果", "真的", "那种", "这样", "那样", "有点", "比较",
}


def _tokens(text: str) -> list[str]:
    """
    Multi-granularity tokens for learning:
    - feature phrases
    - 2~6 char chunks
    - bigrams
    - SINGLE high-signal chars (显/瘦/透/软/裙/料…)
    """
    t = re.sub(r"\s+", "", (text or "").strip().lower())
    if not t:
        return []
    out: list[str] = []

    # 1) known feature phrases first (highest value)
    for w in _FEATURE_SEED:
        if w in t:
            out.append(w)

    # 2) alnum / multi-char CJK chunks
    for m in re.finditer(r"[a-z0-9]+|[\u4e00-\u9fff]{2,6}", t):
        w = m.group(0)
        if w in _STOP or w in _GENERIC_SKIP:
            continue
        out.append(w)

    # 3) all CJK bigrams (not only feature context) for finer memory
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", t)
    for i in range(len(cjk) - 1):
        bg = cjk[i : i + 2]
        if bg in _GENERIC_SKIP:
            continue
        if bg[0] in _STOP and bg[1] in _STOP:
            continue
        out.append(bg)

    # 4) single-char learning (user requested "具体到单个字")
    for ch in cjk:
        if ch in _STOP:
            continue
        if ch in _FEATURE_CHARS:
            out.append(ch)
        # digits often map to price (negative when with 块/块钱 handled elsewhere)
        # keep single digits only if surrounding looks like size/price later via phrases

    # 5) keep short english fabric codes etc already from alnum

    # uniq preserve order, prefer longer tokens earlier for scoring diversity
    seen = set()
    uniq: list[str] = []
    for w in out:
        if not w or w in seen:
            continue
        seen.add(w)
        uniq.append(w)
    # hard cap but keep chars: first phrases/bigrams then chars
    if len(uniq) > 64:
        chars = [w for w in uniq if len(w) == 1]
        multi = [w for w in uniq if len(w) > 1]
        uniq = multi[:48] + chars[:16]
    return uniq


def _bump(bucket: dict[str, float], key: str, delta: float, lo: float = -50.0, hi: float = 80.0) -> None:
    v = float(bucket.get(key, 0.0)) + delta
    bucket[key] = max(lo, min(hi, v))


def _slot_key(s: dict[str, Any]) -> tuple:
    return (
        int(s.get("t0_ms") or 0),
        int(s.get("t1_ms") or 0),
        str(s.get("text") or "").strip(),
    )


def _flatten_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for role in ("golden", "trust", "cta"):
        for s in plan.get(role) or []:
            if not isinstance(s, dict):
                continue
            text = str(s.get("text") or "").strip()
            if not text:
                continue
            item = dict(s)
            item["_section"] = "hook" if role == "golden" else role
            item["text"] = text
            out.append(item)
    return out


def record_plan_feedback(
    *,
    job_id: str,
    before_plan: dict[str, Any] | None,
    after_plan: dict[str, Any],
    source: str = "plan_edit",
) -> dict[str, Any]:
    """
    Compare auto/baseline plan vs human-edited plan and update preferences.
    """
    prefs = load_preferences()
    before_slots = _flatten_plan(before_plan or {})
    after_slots = _flatten_plan(after_plan or {})
    before_map = {_slot_key(s): s for s in before_slots}
    after_map = {_slot_key(s): s for s in after_slots}

    kept_keys = set(after_map.keys())
    dropped_keys = set(before_map.keys()) - kept_keys
    added_keys = kept_keys - set(before_map.keys())

    keep_boost: dict[str, float] = dict(prefs.get("keep_boost") or {})
    drop_penalty: dict[str, float] = dict(prefs.get("drop_penalty") or {})
    hook_boost: dict[str, float] = dict(prefs.get("hook_boost") or {})

    # kept / added = positive
    for k in kept_keys | added_keys:
        s = after_map[k]
        toks = _tokens(s.get("text") or "")
        for t in toks:
            # feature single chars get stronger keep
            pos = 1.5 if (len(t) == 1 and t in _FEATURE_CHARS) else (1.2 if k in added_keys else 0.8)
            _bump(keep_boost, t, pos)
            # if human put into golden, strong hook preference
            if (s.get("_section") == "hook") or (s.get("role") in {"hook", "golden"}):
                hook_pos = 2.2 if (len(t) == 1 and t in _FEATURE_CHARS) else (1.8 if k in added_keys else 1.2)
                _bump(hook_boost, t, hook_pos)

    # dropped from baseline = negative
    for k in dropped_keys:
        s = before_map[k]
        for t in _tokens(s.get("text") or ""):
            # NEVER punish core feature chars/phrases just because they appeared in a dropped long line
            if t in _FEATURE_SEED or (len(t) == 1 and t in _FEATURE_CHARS):
                continue
            if t in _GENERIC_SKIP:
                # generic words: mild penalty only
                _bump(drop_penalty, t, 0.6)
                continue
            _bump(drop_penalty, t, 1.5)
            # if it was golden and user removed, reduce hook preference
            if (s.get("_section") == "hook") or (s.get("role") in {"hook", "golden"}):
                _bump(hook_boost, t, -1.2)

    stats = dict(prefs.get("stats") or {})
    stats["events"] = int(stats.get("events") or 0) + 1
    stats["kept_slots"] = int(stats.get("kept_slots") or 0) + len(kept_keys)
    stats["dropped_slots"] = int(stats.get("dropped_slots") or 0) + len(dropped_keys)
    stats["hook_slots"] = int(stats.get("hook_slots") or 0) + sum(
        1 for s in after_slots if s.get("_section") == "hook"
    )

    case = {
        "job_id": job_id,
        "source": source,
        "at": _utc_now(),
        "kept": len(kept_keys),
        "dropped": len(dropped_keys),
        "added": len(added_keys),
        "hook": [s.get("text") for s in after_slots if s.get("_section") == "hook"][:8],
        "dropped_samples": [before_map[k].get("text") for k in list(dropped_keys)[:8]],
    }
    recent = list(prefs.get("recent_cases") or [])
    recent.insert(0, case)
    recent = recent[:50]

    # prune maps to keep file small
    def top_n(d: dict[str, float], n: int = 400) -> dict[str, float]:
        items = sorted(d.items(), key=lambda kv: abs(kv[1]), reverse=True)[:n]
        return {k: float(v) for k, v in items}

    prefs.update(
        {
            "stats": stats,
            "keep_boost": top_n(keep_boost),
            "drop_penalty": top_n(drop_penalty),
            "hook_boost": top_n(hook_boost),
            "recent_cases": recent,
        }
    )
    save_preferences(prefs)

    # append event log
    _ensure_dir()
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # per-job case snapshot
    try:
        job_case_dir = ROOT / "output" / "web_jobs" / job_id / "cases"
        job_case_dir.mkdir(parents=True, exist_ok=True)
        (job_case_dir / f"feedback-{job_id}.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "before_count": len(before_slots),
                    "after_count": len(after_slots),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    return prefs


def learned_text_score(text: str, *, for_hook: bool = False) -> float:
    """
    Convert learned preferences into a score delta for a transcript line.
    Supports phrase + bigram + single-char hits.
    """
    prefs = load_preferences()
    keep_boost = prefs.get("keep_boost") or {}
    drop_penalty = prefs.get("drop_penalty") or {}
    hook_boost = prefs.get("hook_boost") or {}
    toks = _tokens(text)
    if not toks:
        return 0.0
    score = 0.0
    hit = 0
    for t in toks:
        kb = float(keep_boost.get(t, 0.0))
        dp = float(drop_penalty.get(t, 0.0))
        hb = float(hook_boost.get(t, 0.0))
        if abs(kb) + abs(dp) + abs(hb) < 1e-6:
            continue
        hit += 1
        # single-char weights slightly softer to avoid overreacting
        scale = 0.65 if len(t) == 1 else 1.0
        score += 1.8 * kb * scale
        score -= 2.4 * dp * scale
        if for_hook:
            score += 3.0 * hb * scale
    if hit == 0:
        return 0.0
    # milder normalization so rare strong tokens still matter
    score = score / max(1.4, hit ** 0.30)
    return max(-100.0, min(150.0, score))


def learning_status() -> dict[str, Any]:
    prefs = load_preferences()
    stats = prefs.get("stats") or {}
    return {
        "enabled": True,
        "events": int(stats.get("events") or 0),
        "kept_slots": int(stats.get("kept_slots") or 0),
        "dropped_slots": int(stats.get("dropped_slots") or 0),
        "hook_slots": int(stats.get("hook_slots") or 0),
        "updated_at": prefs.get("updated_at"),
        "top_hook": sorted(
            (prefs.get("hook_boost") or {}).items(), key=lambda kv: kv[1], reverse=True
        )[:12],
        "top_drop": sorted(
            (prefs.get("drop_penalty") or {}).items(), key=lambda kv: kv[1], reverse=True
        )[:12],
        "recent_cases": (prefs.get("recent_cases") or [])[:5],
        "store": str(PREF_PATH),
    }


def seed_negative_live_phrases(phrases: list[str] | None = None) -> dict[str, Any]:
    """
    Inject common livestream-feel negatives so learning has contrast.
    Without negatives, bootstrap-from-good-examples only mildly reweights positives.
    """
    prefs = load_preferences()
    drop_penalty: dict[str, float] = dict(prefs.get("drop_penalty") or {})
    hook_boost: dict[str, float] = dict(prefs.get("hook_boost") or {})
    defaults = phrases or [
        "家人们",
        "老铁们",
        "宝宝们",
        "扣1",
        "扣一",
        "点关注",
        "双击",
        "直播间",
        "公屏",
        "弹幕",
        "福袋",
        "上链接",
        "小黄车",
        "欢迎进来",
        "过一下",
        "过一遍",
        "听得到吗",
        "在不在",
        "来了吗",
        "尺码",
        "建议穿",
        "M码",
        "L码",
        "券后",
        "只要",
        "包邮",
        "加购",
        "下单",
    ]
    for p in defaults:
        for t in _tokens(p) or [p]:
            _bump(drop_penalty, t, 6.0)
            _bump(hook_boost, t, -4.0)
    prefs["drop_penalty"] = drop_penalty
    prefs["hook_boost"] = hook_boost
    stats = dict(prefs.get("stats") or {})
    stats["seed_negatives"] = int(stats.get("seed_negatives") or 0) + 1
    prefs["stats"] = stats
    save_preferences(prefs)
    return prefs


def clear_learning(*, keep_events_backup: bool = True) -> dict[str, Any]:
    """
    Wipe learned preferences (and optionally rotate events log).
    Used when user wants a clean learning slate.
    """
    _ensure_dir()
    if keep_events_backup and EVENTS_PATH.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        bak = LEARN_DIR / f"events.backup.{ts}.jsonl"
        try:
            EVENTS_PATH.replace(bak)
        except Exception:
            # best-effort
            try:
                EVENTS_PATH.write_text("", encoding="utf-8")
            except Exception:
                pass
    elif EVENTS_PATH.exists():
        try:
            EVENTS_PATH.write_text("", encoding="utf-8")
        except Exception:
            pass

    prefs = _default_prefs()
    prefs["updated_at"] = _utc_now()
    prefs["cleared_at"] = _utc_now()
    save_preferences(prefs)
    return learning_status()
