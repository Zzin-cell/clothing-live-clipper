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
    "不闷", "不缩水", "不掉色", "遮盖", "贴肤", "冰冰的", "罗马布", "针织",
    "舒服", "舒适", "亲肤", "凉凉的", "不闷汗", "轻盈", "松弛", "好穿",
    "穿着舒服", "上身舒服", "一整个夏天", "体感", "上身感", "手感",
)

_GENERIC_SKIP = {
    "这个", "那个", "一个", "我们", "你们", "什么", "然后", "因为", "所以", "但是",
    "宝贝", "姐妹", "其实", "而且", "非常", "可以", "还是", "就是", "一下", "一些",
    "不会", "感觉", "更加", "如果", "真的", "那种", "这样", "那样", "有点", "比较",
    "的话", "都是", "不是", "没有", "给你", "这种", "大家", "起来", "是的", "对啊",
}

# clause splitters: treat each short spoken clause as a learning unit
_CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;、\n]+")


def _norm_clause(s: str) -> str:
    t = re.sub(r"\s+", "", (s or "").strip().lower())
    t = re.sub(r"[“”\"'‘’\[\]\(\)（）【】]", "", t)
    return t


def split_clauses(text: str) -> list[str]:
    """
    Split ASR/plan text into small spoken clauses (小句).
    Example:
      "面料很软，显瘦还不透，夏天也好穿"
      -> ["面料很软", "显瘦还不透", "夏天也好穿"]
    """
    raw = (text or "").strip()
    if not raw:
        return []
    # strip known ASR prompt contamination
    raw = raw.replace("不要把衣服讲成食物或故事", "")
    raw = raw.replace("这是服装带货直播口播", "")
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(raw) if p and p.strip()]
    if not parts:
        parts = [raw]

    out: list[str] = []
    for p in parts:
        n = _norm_clause(p)
        if not n:
            continue
        # drop pure filler mono loops
        if re.fullmatch(r"(对|嗯|啊|哦|呃|额|哈|呀)+", n):
            continue
        if re.fullmatch(r"[xy]+", n):
            continue
        # drop stutter loops like 衣服的衣服的衣服的
        if re.search(r"(.{2,6})\1{2,}", n):
            continue
        # keep meaningful clause length
        if len(n) < 3:
            continue
        if len(n) > 36:
            # further hard-split long clause by secondary pauses if any remain
            sub = [x.strip() for x in re.split(r"[/|·•]+", n) if x.strip()]
            if len(sub) > 1:
                for s in sub:
                    sn = _norm_clause(s)
                    if 3 <= len(sn) <= 36 and not re.search(r"(.{2,6})\1{2,}", sn):
                        out.append(sn)
                continue
            # window long text into overlapping chunks (~16 chars)
            win = 16
            for i in range(0, len(n), win - 4):
                chunk = n[i : i + win]
                if len(chunk) >= 3 and not re.search(r"(.{2,6})\1{2,}", chunk):
                    out.append(chunk)
                if i + win >= len(n):
                    break
            continue
        out.append(n)

    # uniq preserve order
    seen = set()
    uniq = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    return uniq[:24]


def _tokens(text: str) -> list[str]:
    """
    Learning units = 小句 (clauses) first, plus feature phrases.
    No single-char tokens.
    """
    t = _norm_clause(text)
    if not t:
        return []

    out: list[str] = []

    # 1) small clauses (primary)
    for c in split_clauses(text):
        out.append(c)
        # also keep clause head/tail short anchors (still >=3)
        if len(c) >= 8:
            out.append(c[:6])
            out.append(c[-6:])

    # 2) known feature phrases
    for w in _FEATURE_SEED:
        if w in t:
            out.append(w)

    # 3) light multi-char keywords (2~6), but skip generics
    for m in re.finditer(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,6}", t):
        w = m.group(0)
        if w in _STOP or w in _GENERIC_SKIP:
            continue
        if len(w) < 2:
            continue
        out.append(w)

    seen = set()
    uniq: list[str] = []
    for w in out:
        if not w or len(w) < 2 or w in seen:
            continue
        seen.add(w)
        uniq.append(w)
    # prefer longer clause keys first for scoring
    uniq.sort(key=lambda x: (-len(x), x))
    return uniq[:56]


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

    # kept / added = positive (learn by 小句 first)
    for k in kept_keys | added_keys:
        s = after_map[k]
        clauses = split_clauses(s.get("text") or "")
        toks = _tokens(s.get("text") or "")
        is_hook = (s.get("_section") == "hook") or (s.get("role") in {"hook", "golden"})
        for c in clauses:
            pos = 1.8 if k in added_keys else 1.2
            _bump(keep_boost, c, pos)
            if is_hook:
                _bump(hook_boost, c, 2.4 if k in added_keys else 1.6)
        for t in toks:
            if len(t) < 2 or t in clauses:
                continue
            # weaker keyword-level residual
            _bump(keep_boost, t, 0.35)
            if is_hook and t in _FEATURE_SEED:
                _bump(hook_boost, t, 0.6)

    # dropped from baseline = negative (by 小句)
    for k in dropped_keys:
        s = before_map[k]
        clauses = split_clauses(s.get("text") or "")
        is_hook = (s.get("_section") == "hook") or (s.get("role") in {"hook", "golden"})
        for c in clauses:
            # don't hard-penalize pure feature clauses
            if any(f in c for f in _FEATURE_SEED) and len(c) <= 8:
                continue
            _bump(drop_penalty, c, 1.8)
            if is_hook:
                _bump(hook_boost, c, -1.4)
        for t in _tokens(s.get("text") or ""):
            if len(t) < 2 or t in clauses or t in _FEATURE_SEED:
                continue
            if t in _GENERIC_SKIP:
                _bump(drop_penalty, t, 0.4)
                continue
            _bump(drop_penalty, t, 0.7)

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


def _soft_bucket_hit(text_norm: str, bucket: dict[str, float], *, topn: int = 260) -> float:
    """
    Fuzzy match learned 小句 against new ASR text.
    Exact/containment first; then feature-overlap for transfer across videos.
    """
    if not text_norm or not bucket:
        return 0.0
    score = 0.0
    # exact
    if text_norm in bucket:
        score += float(bucket[text_norm]) * 1.0
    # sort once by abs weight
    items = sorted(bucket.items(), key=lambda kv: abs(float(kv[1])), reverse=True)[:topn]
    for k, v in items:
        if not isinstance(k, str) or len(k) < 3:
            continue
        vv = float(v)
        if k == text_norm:
            continue
        # full containment either way
        if k in text_norm:
            score += vv * (0.85 if len(k) >= 6 else 0.55)
            continue
        if len(text_norm) >= 4 and text_norm in k:
            score += vv * 0.40
            continue
        # partial: share 2+ feature seeds or a long common chunk
        if len(k) >= 6:
            shared = 0
            for f in _FEATURE_SEED:
                if f in k and f in text_norm:
                    shared += 1
            if shared >= 2:
                score += vv * (0.22 * min(3, shared))
            elif shared == 1 and any(len(f) >= 2 and f in k and f in text_norm for f in _FEATURE_SEED):
                score += vv * 0.12
    return score


def learned_text_score(text: str, *, for_hook: bool = False) -> float:
    """
    Convert learned preferences into a score delta for a transcript line.
    Primary unit = 小句; soft-match so NEW videos (different wording) still benefit.
    """
    prefs = load_preferences()
    keep_boost = prefs.get("keep_boost") or {}
    drop_penalty = prefs.get("drop_penalty") or {}
    hook_boost = prefs.get("hook_boost") or {}
    if not (keep_boost or drop_penalty or hook_boost):
        return 0.0

    tnorm = _norm_clause(text)
    if not tnorm:
        return 0.0

    clauses = split_clauses(text) or ([tnorm] if len(tnorm) >= 3 else [])
    score = 0.0
    hit = 0

    # whole-line soft score (important for transfer to new videos)
    whole_keep = _soft_bucket_hit(tnorm, keep_boost)
    whole_drop = _soft_bucket_hit(tnorm, drop_penalty)
    whole_hook = _soft_bucket_hit(tnorm, hook_boost)
    if abs(whole_keep) + abs(whole_drop) + abs(whole_hook) > 1e-6:
        hit += 1
        score += 2.4 * whole_keep
        score -= 2.8 * whole_drop
        if for_hook:
            score += 3.2 * whole_hook

    # per-clause exact/soft
    for c in clauses:
        kb = float(keep_boost.get(c, 0.0)) + 0.75 * _soft_bucket_hit(c, keep_boost, topn=120)
        dp = float(drop_penalty.get(c, 0.0)) + 0.75 * _soft_bucket_hit(c, drop_penalty, topn=120)
        hb = float(hook_boost.get(c, 0.0)) + 0.75 * _soft_bucket_hit(c, hook_boost, topn=120)
        if abs(kb) + abs(dp) + abs(hb) < 1e-6:
            continue
        hit += 1
        lw = min(2.4, max(1.0, len(c) / 7.0))
        score += 2.2 * kb * lw
        score -= 2.6 * dp * lw
        if for_hook:
            score += 3.2 * hb * lw

    # secondary keyword residual (features + learned short keys)
    for t in _tokens(text):
        if len(t) < 2:
            continue
        kb = float(keep_boost.get(t, 0.0))
        dp = float(drop_penalty.get(t, 0.0))
        hb = float(hook_boost.get(t, 0.0))
        if abs(kb) + abs(dp) + abs(hb) < 1e-6:
            continue
        hit += 1
        w = 0.9 if t in _FEATURE_SEED else 0.55
        score += 1.4 * kb * w
        score -= 1.7 * dp * w
        if for_hook:
            score += 2.0 * hb * w

    if hit == 0:
        return 0.0
    score = score / max(1.15, hit ** 0.22)
    return max(-100.0, min(150.0, score))


def learning_status() -> dict[str, Any]:
    prefs = load_preferences()
    stats = prefs.get("stats") or {}
    top_hook = sorted(
        (prefs.get("hook_boost") or {}).items(), key=lambda kv: kv[1], reverse=True
    )[:12]
    top_keep = sorted(
        (prefs.get("keep_boost") or {}).items(), key=lambda kv: kv[1], reverse=True
    )[:12]
    top_drop = sorted(
        (prefs.get("drop_penalty") or {}).items(), key=lambda kv: kv[1], reverse=True
    )[:12]
    # flatten phrase list for prompt/UI (prefer pure phrase keys)
    def _phrases(items: list[tuple[Any, Any]], n: int = 8) -> list[str]:
        out: list[str] = []
        for k, _v in items:
            s = str(k or "").strip()
            if not s or len(s) < 2:
                continue
            out.append(s[:24])
            if len(out) >= n:
                break
        return out

    return {
        "enabled": True,
        "events": int(stats.get("events") or 0),
        "stats": {
            "events": int(stats.get("events") or 0),
            "kept_slots": int(stats.get("kept_slots") or 0),
            "dropped_slots": int(stats.get("dropped_slots") or 0),
            "hook_slots": int(stats.get("hook_slots") or 0),
        },
        "kept_slots": int(stats.get("kept_slots") or 0),
        "dropped_slots": int(stats.get("dropped_slots") or 0),
        "hook_slots": int(stats.get("hook_slots") or 0),
        "updated_at": prefs.get("updated_at"),
        "top_hook": _phrases(top_hook) or [k for k, _ in top_hook[:8]],
        "top_keep": _phrases(top_keep) or [k for k, _ in top_keep[:8]],
        "top_drop": _phrases(top_drop) or [k for k, _ in top_drop[:8]],
        # raw pairs kept for debug/tools
        "top_hook_pairs": top_hook,
        "top_keep_pairs": top_keep,
        "top_drop_pairs": top_drop,
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
