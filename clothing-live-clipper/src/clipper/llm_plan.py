"""
LLM logic planner on top of local ASR transcript.

Pipeline role:
  ASR lines -> LLM selects/orders small clauses by sales logic
  -> TimelinePlan (role=story) -> reverse cut / render

Falls back to rule-based rank when:
  - no API key
  - network/API error
  - invalid JSON / empty keep set
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from clipper.config import Settings
from clipper.learning import learning_status, split_clauses
from clipper.models import PlanSlot, TimelinePlan
from clipper.openai_compat import OpenAICompatError, chat_completions
from clipper.user_llm import runtime_llm


SYSTEM_PROMPT = """你是服装带货短视频剪辑导演（抖音/快手完播导向）。
输入是直播口播 ASR 的【全部小句】（已尽量按逗号/句号切开，含时间戳）。
你的核心任务：
1) 先通读全部小句，提取主要内容（主卖点/版型/面料/体验/细节）
2) 再从全部小句中挑选必要短句并重新排列成约 55–65 秒成片剧本
不要只看前几句；要用全量口播信息做提炼。

====================
一、开场钩子（前 3 秒，必选 1 种，只留 1 句最强）
====================
1) 视觉冲击型：全身穿搭成品、显瘦对比、面料特写、色差/黑白对比
2) 痛点直击型：一句话戳穿搭痛点（微胖显壮、小个子压身高、显廉价、夏天闷汗、遮肉遮胯）
3) 福利悬念型：开场极短低价/限时限量/现货清仓/专柜平替（最多 1 句，禁止后面反复讲价）

开场避雷（一律 drop）：
- 主播打招呼、晚上好、家人们、调试镜头、对焦、收音
- 闲聊、重复开场白、欢迎语、扣1、点关注、公屏互动

====================
二、快节奏无冗余（提升完播）
====================
1) 重度精简：拖沓讲解、来回试穿、重复话术只留一次最优
2) 全删：与货无关闲聊、卡顿、整理衣服、喝水、回答无关弹幕、调试
3) 同一卖点重复出现：只保留最清楚的一句
4) 优先“信息密度高”的短句，避免长段灌水

====================
三、内容优先级（从高到低）
====================
上身效果 ＞ 面料 ＞ 价格
- 上身效果：显瘦、收腰、版型、长短、遮肉、适配小个子/梨形
- 面料：软、垂感、透气、不闷、冰凉、抗皱、不透
- 价格：仅可作开场悬念，正文少讲或不讲；禁止尺码报数长段

口播若提到画面类型，优先保留对应信息：
- 全身/全景：版型、长短、显瘦、适配
- 半身/近景：领口、肩线、腰线、遮副乳
- 细节特写：肌理、刺绣、扣子、拉链、走线、透气网眼
- 对比：穿前穿后、宽松显瘦、两色上身

====================
四、成片顺序（严格按此组织 keep 顺序）
====================
1) 0–3s 钩子（视觉冲击 / 痛点 / 福利悬念 三选一）
2) 版型/上身效果讲解
3) 细节特写对应口播
4) 对比效果 / 穿着体验证明
5) 必要时极短收束（不再寒暄）

不要输出“黄金/信任/收尾”分区标题；输出一条通顺时间线即可。

====================
五、完整逻辑（非常重要，禁止戛然而止）
====================
1) 成片必须是“完整表达”，不能话说一半就结束
2) keep 里每一条都必须是语义完整的小句（主谓/卖点完整），禁止半截词、半截转折
3) 若某卖点只讲了上半句，必须补上下一句把意思说完，否则整段不要
4) 结尾必须有收束感：用体验确认/效果总结/行动暗示其一自然结束
   （例如“穿上就显瘦”“夏天也不会闷”“这个细节真的加分”）
5) 禁止在“然后/因为/所以/你看/而且”等连接词处切断
6) 可以短，但不能断；完整逻辑 > 硬凑满 60 秒

====================
六、技术硬规则
====================
1) 输入是全量小句；先提炼 main_points，再从中选 keep 并重排
2) 只能使用输入小句 id；优先整句采用该小句完整 t0~t1，不要随意砍半句
3) 总源片时长尽量接近 target_source_ms；若无法完整讲完，宁可少 5–8 秒，也要完整
4) 删除：尺码建议、长段砍价、直播控场、幻觉垃圾（对对对、xy）
5) keep 按成片播放顺序；每条写 why 与 point；最后 1–2 条必须是收束，不能是未完成句
6) 只输出严格 JSON，不要 markdown

输出 JSON schema:
{
  "product_summary": "一句话主卖点",
  "hook_type": "visual|pain|welfare",
  "main_points": ["主卖点1","版型点","体验点","细节点"],
  "logic": ["钩子","版型上身","细节","对比体验","收束"],
  "keep": [
    {"id":"c00012","t0_ms":12300,"t1_ms":15800,"text":"...","why":"3秒痛点钩子","point":"显瘦","complete":true}
  ],
  "drop_ids": ["c00001","c00002"],
  "notes": "如何保证完整逻辑、删了哪些半句/重复"
}
"""


def _http_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _extract_json_obj(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if not t:
        raise ValueError("empty llm content")
    # strip ```json fences
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError("no json object in llm content")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("llm json is not object")
    return obj


def _normalize_lines(raw_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, u in enumerate(raw_lines or []):
        if not isinstance(u, dict):
            continue
        text = str(u.get("text") or "").strip()
        if not text:
            continue
        t0 = max(0, int(u.get("t0_ms") or 0))
        t1 = max(t0 + 300, int(u.get("t1_ms") or (t0 + 1000)))
        uid = str(u.get("utt_id") or u.get("id") or f"u{i:04d}")
        out.append({"id": uid, "utt_id": uid, "text": text, "t0_ms": t0, "t1_ms": t1})
    return out


def expand_lines_to_clauses(
    raw_lines: list[dict[str, Any]],
    *,
    max_clauses: int = 400,
) -> list[dict[str, Any]]:
    """
    Expand full ASR transcript into 小句 units for LLM.
    Each clause keeps proportional time within parent utterance.
    """
    parents = _normalize_lines(raw_lines)
    out: list[dict[str, Any]] = []
    cid = 0
    for p in parents:
        text = str(p.get("text") or "").strip()
        t0 = int(p["t0_ms"])
        t1 = int(p["t1_ms"])
        clauses = split_clauses(text)
        if not clauses:
            # keep original if cannot split
            clauses = [text]
        # proportional windows
        n = max(1, len(clauses))
        span = max(300, t1 - t0)
        # ensure each clause has at least ~350ms when possible
        step = max(350, span // n)
        cur = t0
        for j, ctext in enumerate(clauses):
            if cid >= max_clauses:
                return out
            if j == n - 1:
                ct1 = t1
            else:
                ct1 = min(t1, cur + step)
            if ct1 <= cur:
                ct1 = min(t1, cur + 350)
            out.append(
                {
                    "id": f"c{cid:05d}",
                    "utt_id": f"c{cid:05d}",
                    "parent_id": p["id"],
                    "text": ctext,
                    "t0_ms": cur,
                    "t1_ms": max(cur + 300, ct1),
                }
            )
            cid += 1
            cur = ct1
    return out


def _learning_hints(limit: int = 12) -> dict[str, Any]:
    try:
        st = learning_status()
        return {
            "events": st.get("events"),
            "top_keep_or_hook": (st.get("top_hook") or [])[:limit],
            "top_drop": (st.get("top_drop") or [])[:limit],
        }
    except Exception:
        return {}


def call_llm_for_plan(
    lines: list[dict[str, Any]],
    *,
    target_seconds: int = 60,
    playback_speed: float = 1.4,
    settings: Settings | None = None,
) -> dict[str, Any]:
    del settings  # LLM credentials come from user UI config only
    cfg = runtime_llm()
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("missing_llm_api_key_user_config")
    if not cfg.get("enabled", True) or not cfg.get("plan_enabled", True):
        raise RuntimeError("llm_plan_disabled_in_user_config")
    base = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "").strip()
    if not base or not model:
        raise RuntimeError("missing_llm_base_url_or_model_user_config")
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    target_source_ms = int(round(target_seconds * 1000 * sp))

    # Full ASR -> 小句 units (all content for main-point extraction)
    clauses = expand_lines_to_clauses(lines, max_clauses=420)
    if not clauses:
        raise RuntimeError("empty_transcript")

    compact = [
        {
            "id": u["id"],
            "parent_id": u.get("parent_id"),
            "t0_ms": u["t0_ms"],
            "t1_ms": u["t1_ms"],
            "text": str(u["text"])[:160],
        }
        for u in clauses
    ]
    user_payload = {
        "task": "read_all_clauses_extract_main_points_then_reorder",
        "target_final_seconds": target_seconds,
        "playback_speed": sp,
        "target_source_ms": target_source_ms,
        "clause_count": len(compact),
        "policy": {
            "submit_mode": "full_asr_all_clauses",
            "extract_first": True,
            "no_size": True,
            "no_live_room_filler": True,
            "clothing_only": True,
            "price_only_as_opening_hook": True,
            "priority": "上身效果 > 面料 > 价格",
            "pacing": "快节奏无冗余，重复试穿/重复话术只留最优一次",
            "opening_hook_types": [
                "visual: 全身成品/显瘦对比/面料特写/色差对比",
                "pain: 微胖显壮/小个子压身高/显廉价/夏天闷汗",
                "welfare: 低价/限时限量/现货清仓/专柜平替（仅开场一句）",
            ],
            "sequence": "3秒钩子 → 版型上身 → 细节特写 → 对比/体验 → 自然收束",
            "complete_logic_required": True,
            "no_mid_sentence_cutoff": True,
            "prefer_complete_under_duration": True,
            "drop_always": [
                "打招呼",
                "调试镜头",
                "闲聊",
                "重复开场白",
                "无关弹幕",
                "整理衣服",
                "喝水",
                "对对对/xy幻觉",
                "话说一半的半截句",
            ],
        },
        "learning_hints": _learning_hints(),
        "all_clauses": compact,
    }

    user_text = (
        "下面是该视频 ASR 全量口播小句。请先提取主要内容 main_points，"
        "再从全部小句中挑选并重新排列 keep，只输出JSON：\n"
        + json.dumps(user_payload, ensure_ascii=False)
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    try:
        out = chat_completions(
            messages=messages,
            model=model,
            base_url=base,
            api_key=key,
            temperature=0.2,
            max_tokens=4096,
            force_json=True,
            timeout=180,
            cfg=cfg,
        )
    except OpenAICompatError as e:
        raise RuntimeError(f"llm_request_failed:{e}") from e

    content = out.get("content") or ""
    obj = _extract_json_obj(content)
    obj["_meta"] = {
        "model": out.get("model") or model,
        "base_url": out.get("base_url") or base,
        "endpoint": out.get("endpoint"),
        "target_source_ms": target_source_ms,
        "input_lines": len(_normalize_lines(lines)),
        "input_clauses": len(clauses),
        "submit_mode": "full_asr_all_clauses",
        "compat": {
            "auth_variant": out.get("auth_variant"),
            "payload_variant": out.get("payload_variant"),
            "endpoint": out.get("endpoint"),
        },
        "auth_source": "user_ui",
        "client": "openai_compat_full",
    }
    # stash clauses for timeline mapping (full ASR units)
    obj["_clauses"] = clauses
    return obj


_INCOMPLETE_TAIL = (
    "然后", "因为", "所以", "而且", "但是", "不过", "就是", "那个", "这个",
    "你看", "你看一下", "还有", "以及", "比如", "比如说", "包括", "以及呢",
    "的话", "的话呢", "的话啊", "的", "了", "呢", "啊", "哦", "嗯",
)


def _looks_incomplete_text(text: str) -> bool:
    t = re.sub(r"\s+", "", (text or "").strip())
    if not t:
        return True
    if len(t) < 4:
        return True
    # ends with connective / dangling particle => incomplete thought
    for w in _INCOMPLETE_TAIL:
        if t.endswith(w):
            return True
    # pure list crumbs
    if t in {"对", "好", "是", "嗯", "啊", "哦"}:
        return True
    return False


def _is_closing_text(text: str) -> bool:
    t = text or ""
    keys = (
        "显瘦", "舒服", "好看", "好穿", "不闷", "凉快", "推荐", "闭眼入",
        "真的", "完全", "足够", "就这些", "就这样", "效果", "气质", "高级",
    )
    return any(k in t for k in keys) and not _looks_incomplete_text(t)


def llm_obj_to_timeline(
    llm_obj: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    target_seconds: int = 60,
    playback_speed: float = 1.4,
) -> TimelinePlan:
    # Prefer clause units if provided by call_llm_for_plan; else expand now
    clause_units = llm_obj.get("_clauses")
    if not isinstance(clause_units, list) or not clause_units:
        clause_units = expand_lines_to_clauses(lines)
    by_id = {str(u.get("utt_id") or u.get("id")): u for u in clause_units}
    # ordered list for neighbor completion
    ordered = list(clause_units)
    id_to_idx = {str(u.get("id")): i for i, u in enumerate(ordered)}
    parents = {str(u.get("id")): u for u in _normalize_lines(lines)}
    keep = llm_obj.get("keep") or []
    slots: list[PlanSlot] = []
    if not isinstance(keep, list):
        keep = []

    used_ids: set[str] = set()

    def _resolve_src(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        uid = str(item.get("id") or item.get("utt_id") or "")
        src = by_id.get(uid) or parents.get(uid)
        if src:
            return uid, src
        text_i = str(item.get("text") or "").strip()
        if text_i:
            for u in by_id.values():
                ut = str(u.get("text") or "")
                if text_i[:10] and text_i[:10] in ut:
                    return str(u.get("id")), u
                if ut[:10] and ut[:10] in text_i:
                    return str(u.get("id")), u
        return uid, None

    def _append_from_src(uid: str, src: dict[str, Any], *, why: str = "", score: float = 50.0) -> None:
        if uid in used_ids:
            return
        text = str(src.get("text") or "").strip()
        if not text:
            return
        if any(x in text for x in ("尺码", "M码", "L码", "m码", "胸围", "腰围", "偏大", "偏小")):
            return
        if any(x in text for x in ("加购", "小黄车", "上链接", "点链接")):
            return
        # always take full clause window first (avoid mid-clause cutoff)
        t0 = int(src["t0_ms"])
        t1 = max(t0 + 300, int(src["t1_ms"]))
        # small natural pad, not truncation
        t1 = t1 + 120
        slots.append(
            PlanSlot(
                clip_id=f"llm_{uid}_{len(slots)}",
                role="story",
                t0_ms=t0,
                t1_ms=t1,
                text=text,
                score=score,
            )
        )
        used_ids.add(uid)

    for i, item in enumerate(keep):
        if not isinstance(item, dict):
            continue
        uid, src = _resolve_src(item)
        if not src:
            continue
        text = str(item.get("text") or src.get("text") or "").strip()
        # skip incomplete crumbs unless we can complete with neighbor below
        if _looks_incomplete_text(text) and len(text) < 6:
            # try neighbor completion first
            pass
        _append_from_src(uid, src, why=str(item.get("why") or ""), score=float(50 + max(0, 20 - i)))

        # if current text looks incomplete, auto-append next clause from full ASR
        if _looks_incomplete_text(text):
            idx = id_to_idx.get(uid)
            if idx is not None:
                for j in range(idx + 1, min(idx + 3, len(ordered))):
                    nxt = ordered[j]
                    nid = str(nxt.get("id"))
                    ntext = str(nxt.get("text") or "")
                    if nid in used_ids:
                        continue
                    # don't pull greetings as completion
                    if any(b in ntext for b in ("家人们", "扣1", "欢迎", "链接")):
                        break
                    _append_from_src(nid, nxt, why="complete_logic_next_clause", score=40)
                    if not _looks_incomplete_text(ntext):
                        break

    # duration trim/pad toward target source length, but NEVER drop the closing complete clause first
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    aim = int(round(target_seconds * 1000 * sp))
    min_ms = int(aim * 0.82)  # allow shorter if complete
    max_ms = int(aim * 1.12)

    def total_ms() -> int:
        return sum(max(0, s.t1_ms - s.t0_ms) for s in slots)

    # trim from middle-low value first; keep first hook and last closer
    guard = 0
    while total_ms() > max_ms and len(slots) > 4 and guard < 20:
        guard += 1
        # drop near-end incomplete crumbs first
        drop_i = None
        for i in range(len(slots) - 2, 0, -1):
            if _looks_incomplete_text(slots[i].text):
                drop_i = i
                break
        if drop_i is None:
            # drop lowest score middle item
            mid = slots[1:-1]
            if not mid:
                break
            victim = min(mid, key=lambda s: s.score)
            drop_i = slots.index(victim)
        slots.pop(drop_i)

    # ensure ending is complete: if last is incomplete, try append a closing candidate
    if slots and _looks_incomplete_text(slots[-1].text):
        # search remaining clauses for a short closer
        for u in ordered:
            uid = str(u.get("id"))
            if uid in used_ids:
                continue
            tx = str(u.get("text") or "")
            if _is_closing_text(tx):
                _append_from_src(uid, u, why="force_complete_ending", score=45)
                break
        # if still incomplete, drop dangling last crumb
        if slots and _looks_incomplete_text(slots[-1].text) and len(slots) > 2:
            slots.pop()

    # merge adjacent continuous clauses into smoother modules (same parent or tiny gap)
    merged: list[PlanSlot] = []
    for s in slots:
        if not merged:
            merged.append(s)
            continue
        prev = merged[-1]
        gap = s.t0_ms - prev.t1_ms
        if 0 <= gap <= 500 and (prev.t1_ms - prev.t0_ms) + (s.t1_ms - s.t0_ms) <= 10000:
            # merge only if it improves completeness
            prev.t1_ms = max(prev.t1_ms, s.t1_ms)
            if s.text and s.text not in (prev.text or ""):
                joiner = "" if (prev.text or "").endswith(("，", "。", "！", "？", ",", ".")) else "，"
                prev.text = f"{prev.text}{joiner}{s.text}"
            prev.score = max(prev.score, s.score)
            continue
        merged.append(s)
    slots = merged

    warnings = [
        "policy:llm_logic_plan",
        "policy:logic_storyline",
        "policy:complete_logic_no_cutoff",
        "policy:size_excluded",
        "policy:de_live_room_feel",
    ]
    if llm_obj.get("product_summary"):
        warnings.append(f"llm_summary:{(str(llm_obj.get('product_summary'))[:80])}")
    if llm_obj.get("main_points"):
        warnings.append("policy:main_points_first")
    if not slots:
        warnings.append("llm_empty_keep")
    tot = total_ms()
    if tot < min_ms:
        warnings.append(f"short_but_complete_ms={tot}")
        # mild pad only on last complete slot
        if slots and not _looks_incomplete_text(slots[-1].text):
            slots[-1].t1_ms += min(1800, min_ms - tot)

    # final guard: never end with incomplete text
    if slots and _looks_incomplete_text(slots[-1].text) and len(slots) > 1:
        slots.pop()
        warnings.append("dropped_incomplete_tail")

    return TimelinePlan(
        target_duration_s=target_seconds,
        golden=slots,
        trust=[],
        cta=[],
        total_duration_ms=total_ms(),
        golden_weight_ratio=1.0 if slots else 0.0,
        golden20_passed=bool(slots),
        warnings=warnings,
    )


def plan_from_asr_with_llm(
    lines: list[dict[str, Any]],
    *,
    target_seconds: int = 60,
    playback_speed: float = 1.4,
    settings: Settings | None = None,
) -> tuple[TimelinePlan, dict[str, Any]]:
    """
    Returns (plan, debug_obj). Raises on hard failure so caller can fallback.
    """
    obj = call_llm_for_plan(
        lines,
        target_seconds=target_seconds,
        playback_speed=playback_speed,
        settings=settings,
    )
    plan = llm_obj_to_timeline(
        obj,
        lines,
        target_seconds=target_seconds,
        playback_speed=playback_speed,
    )
    if not plan.golden:
        raise RuntimeError("llm_plan_has_no_slots")
    return plan, obj
