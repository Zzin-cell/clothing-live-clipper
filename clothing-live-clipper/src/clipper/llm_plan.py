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

from clipper.config import Settings, resolve_llm_base_url, resolve_llm_key, resolve_llm_model
from clipper.learning import learned_text_score, learning_status
from clipper.models import PlanSlot, TimelinePlan


SYSTEM_PROMPT = """你是服装带货短视频剪辑导演（抖音/快手完播导向）。
输入是直播口播 ASR 句子（含时间戳）。请输出约 55–65 秒成片的逻辑剧本。

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
五、技术硬规则
====================
1) 只能使用输入句子 id；t0_ms/t1_ms 必须落在该句原时间范围内（可微调切小段）
2) 总源片时长尽量接近 target_source_ms（已按倍速预留，默认约 1.4x→60s）
3) 删除：尺码建议（M/L/偏大偏小/胸围腰围）、长段砍价、直播控场、幻觉垃圾（对对对、xy）
4) keep 按成片播放顺序；每条写 why（钩子/版型/细节/对比/体验）
5) 只输出严格 JSON，不要 markdown

输出 JSON schema:
{
  "product_summary": "一句话主卖点",
  "hook_type": "visual|pain|welfare",
  "logic": ["钩子","版型上身","细节","对比体验"],
  "keep": [
    {"id":"u0003","t0_ms":12300,"t1_ms":15800,"text":"...","why":"3秒痛点钩子"}
  ],
  "drop_ids": ["u0001","u0002"],
  "notes": "简短说明删了什么重复/闲聊"
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
    settings = settings or Settings.from_env()
    key = (settings.llm_api_key or resolve_llm_key() or "").strip()
    if not key:
        raise RuntimeError("missing_llm_api_key")
    base = (settings.llm_base_url or resolve_llm_base_url()).rstrip("/")
    model = (settings.llm_model or resolve_llm_model() or "gpt-4o-mini").strip()
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    target_source_ms = int(round(target_seconds * 1000 * sp))

    norm = _normalize_lines(lines)
    if not norm:
        raise RuntimeError("empty_transcript")

    # compact payload for token efficiency
    compact = [
        {
            "id": u["id"],
            "t0_ms": u["t0_ms"],
            "t1_ms": u["t1_ms"],
            "text": u["text"][:180],
        }
        for u in norm[:120]
    ]
    user_payload = {
        "target_final_seconds": target_seconds,
        "playback_speed": sp,
        "target_source_ms": target_source_ms,
        "policy": {
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
            "sequence": "3秒钩子 → 版型上身 → 细节特写 → 对比/体验",
            "drop_always": [
                "打招呼",
                "调试镜头",
                "闲聊",
                "重复开场白",
                "无关弹幕",
                "整理衣服",
                "喝水",
                "对对对/xy幻觉",
            ],
        },
        "learning_hints": _learning_hints(),
        "utterances": compact,
    }

    url = f"{base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请基于以下ASR口播稿做逻辑剪辑剧本，只输出JSON：\n"
                    + json.dumps(user_payload, ensure_ascii=False)
                ),
            },
        ],
    }

    try:
        resp = _http_json(url, headers, payload, timeout=120)
    except urllib.error.HTTPError as e:
        # some providers don't support response_format
        err = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        if "response_format" in err or e.code in {400, 404}:
            payload.pop("response_format", None)
            resp = _http_json(url, headers, payload, timeout=120)
        else:
            raise RuntimeError(f"llm_http_{e.code}:{err[:400]}") from e

    content = ""
    try:
        content = resp["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"llm_bad_response:{str(resp)[:400]}") from e
    obj = _extract_json_obj(content)
    obj["_meta"] = {
        "model": model,
        "base_url": base,
        "target_source_ms": target_source_ms,
        "input_lines": len(norm),
    }
    return obj


def llm_obj_to_timeline(
    llm_obj: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    target_seconds: int = 60,
    playback_speed: float = 1.4,
) -> TimelinePlan:
    by_id = {str(u.get("utt_id") or u.get("id")): u for u in _normalize_lines(lines)}
    keep = llm_obj.get("keep") or []
    slots: list[PlanSlot] = []
    if not isinstance(keep, list):
        keep = []

    for i, item in enumerate(keep):
        if not isinstance(item, dict):
            continue
        uid = str(item.get("id") or item.get("utt_id") or "")
        src = by_id.get(uid)
        if not src:
            # try fuzzy by text
            text_i = str(item.get("text") or "").strip()
            if text_i:
                for u in by_id.values():
                    if text_i[:12] and text_i[:12] in str(u.get("text") or ""):
                        src = u
                        uid = str(u.get("id"))
                        break
        if not src:
            continue
        st0 = int(src["t0_ms"])
        st1 = int(src["t1_ms"])
        t0 = int(item.get("t0_ms") or st0)
        t1 = int(item.get("t1_ms") or st1)
        # clamp into source utterance window (+small pad)
        t0 = max(st0, min(st1 - 300, t0))
        t1 = max(t0 + 300, min(st1 + 200, t1))
        text = str(item.get("text") or src.get("text") or "").strip()
        if not text:
            continue
        # hard safety: still drop obvious price/size tokens
        if any(x in text for x in ("尺码", "M码", "L码", "券后", "只要", "加购", "小黄车", "包邮")):
            continue
        slots.append(
            PlanSlot(
                clip_id=f"llm_{uid}_{i}",
                role="story",
                t0_ms=t0,
                t1_ms=t1,
                text=text,
                score=float(50 + max(0, 20 - i)),
            )
        )

    # duration trim/pad toward target source length
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    aim = int(round(target_seconds * 1000 * sp))
    min_ms = int(aim * 0.88)
    max_ms = int(aim * 1.12)

    def total_ms() -> int:
        return sum(max(0, s.t1_ms - s.t0_ms) for s in slots)

    while total_ms() > max_ms and len(slots) > 3:
        slots.pop()

    warnings = [
        "policy:llm_logic_plan",
        "policy:logic_storyline",
        "policy:size_excluded",
        "policy:de_live_room_feel",
    ]
    if llm_obj.get("product_summary"):
        warnings.append(f"llm_summary:{(str(llm_obj.get('product_summary'))[:80])}")
    if not slots:
        warnings.append("llm_empty_keep")
    tot = total_ms()
    if tot < min_ms:
        warnings.append(f"short_content_ms={tot}")
        if slots:
            slots[-1].t1_ms += min(2500, min_ms - tot)

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
