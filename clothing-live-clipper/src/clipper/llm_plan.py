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
3) 效果悬念型：一句点出穿上效果/适用感（禁止谈价格/发货）

开场避雷（一律 drop）：
- 主播打招呼、晚上好、家人们、调试镜头、对焦、收音
- 闲聊、重复开场白、欢迎语、扣1、点关注、公屏互动
- 价格/发货/物流/促销（任何优惠、包邮、几天发货、现货预售等）

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
上身效果 ＞ 细节(含面料/适用人群) ＞ 对比体验
- 上身效果：显瘦、收腰、版型、长短、遮肉、梨形/小个子友好
- 细节可包含：
  · 面料：软、垂感、透气、不闷、冰凉、抗皱、不透、亲肤
  · 适用人群：微胖/梨形/小个子/大码友好/通勤/日常/季节场景
  · 工艺细点：肌理、刺绣、扣子、拉链、走线、领口、腰线
- 严禁：价格、发货、物流、包邮、预售、尺码报数

口播若提到画面类型，优先保留对应信息：
- 全身/全景：版型、长短、显瘦、适配人群
- 半身/近景：领口、肩线、腰线、遮副乳
- 细节特写：面料肌理、刺绣、扣子、拉链、走线、透气网眼
- 对比：穿前穿后、宽松显瘦、两色上身

====================
四、成片顺序（严格按此组织 keep 顺序）
====================
1) 0–3s 钩子（视觉冲击 / 痛点 / 效果悬念；禁止价格福利）
2) 版型/上身效果
3) 面料与工艺细节
4) 适用人群（谁穿好看/好搭）
5) 对比/穿着体验 + 自然收束
目标成片观感约 55–65 秒（默认 60 秒@倍速后），像短视频不像直播。

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
4) 删除：尺码建议、任何价格/发货/物流、直播控场、幻觉垃圾（对对对、xy）
5) keep 按成片播放顺序；每条写 why 与 point；最后 1–2 条必须是收束，不能是未完成句
6) 只输出严格 JSON，不要 markdown

输出 JSON schema:
{
  "product_summary": "一句话主卖点",
  "hook_type": "visual|pain|effect",
  "main_points": ["主卖点1","版型点","面料/适用人群","细节点"],
  "logic": ["钩子","版型上身","细节","对比体验","收束"],
  "keep": [
    {"id":"c00012","t0_ms":12300,"t1_ms":15800,"text":"...","why":"3秒痛点钩子","point":"显瘦","complete":true}
  ],
  "drop_ids": ["c00001","c00002"],
  "notes": "如何保证完整逻辑、删了哪些半句/价格发货"
}
"""


SYSTEM_PROMPT_LIGHT = """你是服装短视频剪辑导演（成品要像短视频，不要像直播间）。
输入：口播小句 id + t0/t1 毫秒 + text。先提 main_points，再选 keep 重排。
只用输入 id，禁止编造时间/半截句；只输出严格 JSON。

【成片目标】
- 成片观感约 55–65 秒（对应输入 target_s，默认≈60）；源片总时长尽量贴近 target_src_ms
- 内容只讲服装特点：版型/上身效果、面料体验、适用人群、必要工艺细节
- 去直播感：无打招呼、控场、互动、闲聊、调试镜头

【必须 KEEP 的卖点（优先出现）】
1) 版型/上身：显瘦、收腰、修身、长短、遮肉、不显胯、肩线领口腰线
2) 面料：软、垂感、透气、不闷、冰凉、不透、亲肤、抗皱、不起球
3) 适用人群：微胖、梨形、小个子、大码友好、通勤、日常、季节场景
4) 对比/体验：穿上前后、两色、好穿不难受
每条 keep 尽量能对应以上一类 point（版型/面料/人群/细节/体验）

【必须 DROP】
- 直播控场：家人们/宝宝/姐妹/老铁、扣1、点关注、公屏、福袋、欢迎、过一下、在不在
- 交易与履约：价格/定价/拨分/多少钱/券后/包邮/秒杀、发货/物流/现货预售、小黄车/链接/加购下单
- 尺码顾问：M/L 码、偏大偏小、围度报数、建议穿
- 空泛重复、话术复读、无货盘信息的情绪句

【结构与节奏】
顺序：0–3s 钩子(视觉/痛点/效果，禁价格) → 版型上身 → 面料/细节 → 适用人群 → 体验对比 → 自然收束
信息密度要高，像成片口播；完整表达优先于硬凑时长（可短 3–8 秒，禁静音尾巴）

JSON:
{"product_summary":"...","hook_type":"visual|pain|effect","main_points":["版型…","面料…","适用人群…"],"logic":["钩子","版型上身","面料细节","适用人群","体验收束"],"keep":[{"id":"c00012","t0_ms":0,"t1_ms":1,"text":"...","why":"...","point":"版型|面料|人群|细节|体验","complete":true}],"drop_ids":["c00001"],"notes":"..."}
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
    # strip think / reasoning wrappers some Qwen3 builds emit
    t = re.sub(r"<think>[\s\S]*?</think>", "", t, flags=re.I)
    t = re.sub(r"<thinking>[\s\S]*?</thinking>", "", t, flags=re.I)
    # strip ```json fences
    t = re.sub(r"^```(?:json)?\s*", "", t.strip())
    t = re.sub(r"\s*```$", "", t)
    t = t.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Prefer object that contains plan keys if multiple JSON blobs appear
    candidates = re.findall(r"\{[\s\S]*?\}", t)
    # greedy fallback for nested keep arrays
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        candidates.append(m.group(0))
    best: dict[str, Any] | None = None
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if "keep" in obj or "main_points" in obj or "product_summary" in obj:
            return obj
        if best is None:
            best = obj
    if best is not None:
        return best
    raise ValueError("no json object in llm content")


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


# Latency-first caps (SiliconFlow lightweight path)
LIGHT_MAX_CLAUSES = 80
CLAUSE_TEXT_MAX = 80
PLAN_MAX_TOKENS = 1536
PLAN_TIMEOUT_S = 55

_CONTROL_MARKERS = (
    "家人们", "扣1", "点关注", "晚上好", "欢迎", "公屏", "调试", "对焦", "链接", "小黄车", "加购",
)
_SIZE_MARKERS = ("尺码", "M码", "L码", "m码", "胸围", "腰围", "偏大", "偏小", "建议穿")
# 价格/发货/物流：一律剔除（含开场福利话术、ASR 谐音/繁体）
_PRICE_SHIP_MARKERS = (
    # 价格/促销（简繁 + 口语）
    "价格", "價錢", "價錢", "定价", "定價", "价钱", "多少钱", "多少錢", "块钱", "塊錢",
    "元", "券后", "券後", "只要", "包邮", "包郵", "秒杀", "秒殺", "活动价", "活動價",
    "原价", "原價", "现价", "現價", "折扣", "满减", "滿減", "优惠", "優惠", "特价", "特價",
    "划算", "便宜", "性价比", "性價比", "到手价", "到手價", "直播价", "直播價", "专属价", "專屬價",
    "拨分", "撥分", "块", "塊", "贵呀", "貴呀", "贵啊", "貴啊", "太贵", "太貴", "卖点价",
    "人民币", "人民幣",
    # 发货/物流（简繁）
    "发货", "發貨", "发貨", "现货", "現貨", "预售", "預售", "几天发", "幾天發", "今日发", "今日發",
    "次日达", "次日達", "物流", "快递", "快遞", "顺丰", "順豐", "补货", "補貨", "断码", "斷碼",
    "拍下", "下单", "下單", "付款", "发货时间", "發貨時間", "到货", "到貨", "邮费", "郵費",
)
# 成片要凸显的服装卖点（版型 / 面料 / 适用人群）
_FIT_MARKERS = (
    "版型", "显瘦", "收腰", "修身", "遮肉", "高腰", "不显胯", "上身", "穿上", "长短",
    "领口", "腰线", "肩线",
)
_FABRIC_MARKERS = (
    "面料", "布料", "材质", "软", "超软", "垂感", "透气", "不闷", "冰凉", "不透",
    "亲肤", "抗皱", "不起球", "弹力", "天丝", "醋酸", "雪纺", "纯棉", "凉感",
)
_AUDIENCE_MARKERS = (
    "适用", "适合", "人群", "微胖", "梨形", "小个子", "大码", "胖妹妹", "通勤",
    "日常", "上班", "夏天", "秋冬", "显白",
)
_VALUE_MARKERS = _FIT_MARKERS + _FABRIC_MARKERS + _AUDIENCE_MARKERS + (
    "细节", "蕾丝", "刺绣", "拼接", "对比", "舒服", "好穿",
)


def _is_control(text: str) -> bool:
    return any(x in text for x in _CONTROL_MARKERS)


def _is_size(text: str) -> bool:
    return any(x in text for x in _SIZE_MARKERS)


def _is_price_or_shipping(text: str) -> bool:
    t = text or ""
    if any(x in t for x in _PRICE_SHIP_MARKERS):
        return True
    # bare / colloquial prices: 199、599拨分、¥59、99块、1000多
    if re.search(r"(¥|￥)\s*\d+", t):
        return True
    if re.search(r"\d+\s*(块|塊|元|块钱|塊錢|多块|多塊|多)", t):
        return True
    if re.search(r"\d{2,5}\s*(拨分|撥分)", t):
        return True
    # 纯数字价感 + 价格语境词
    if re.search(r"\d{2,5}", t) and any(k in t for k in ("定", "价", "價", "卖", "賣", "套装", "套裝", "到手")):
        return True
    # 发货语境（含繁体 ASR）
    if re.search(r"(发|發).{0,4}(货|貨)", t) or re.search(r"(货|貨).{0,4}(时间|時間|慢)", t):
        return True
    return False


def _value_score(text: str) -> int:
    t = text or ""
    s = 0
    # 三类核心卖点加权（版型/面料/人群）
    if any(k in t for k in _FIT_MARKERS):
        s += 6
    if any(k in t for k in _FABRIC_MARKERS):
        s += 6
    if any(k in t for k in _AUDIENCE_MARKERS):
        s += 6
    for k in _VALUE_MARKERS:
        if k in t:
            s += 2
    if 4 <= len(t) <= 40:
        s += 1
    if _is_control(t) or _is_size(t) or _is_price_or_shipping(t):
        s -= 80
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
        "dropped_price_ship": 0,
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
        if _is_price_or_shipping(text):
            stats["dropped_price_ship"] += 1
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
            if _is_control(text) or _is_size(text) or _is_price_or_shipping(text):
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


def _learning_hints(limit: int = 4) -> dict[str, Any]:
    """Tiny preference hints only — keep payload small for latency."""
    try:
        st = learning_status()
        keep = (st.get("top_hook") or [])[:limit]
        drop = (st.get("top_drop") or [])[:limit]
        if not keep and not drop:
            return {}
        return {"keep": keep, "drop": drop}
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

    # Full ASR -> 小句 units, then select a light subset for the LLM
    clauses_all = expand_lines_to_clauses(lines, max_clauses=420)
    if not clauses_all:
        raise RuntimeError("empty_transcript")
    clauses, trim_stats = select_clauses_for_llm(clauses_all, max_clauses=LIGHT_MAX_CLAUSES)

    # Minimal clause fields → fewer input tokens
    compact = [
        {
            "id": u["id"],
            "t0": u["t0_ms"],
            "t1": u["t1_ms"],
            "text": str(u["text"])[:CLAUSE_TEXT_MAX],
        }
        for u in clauses
    ]
    # 成片观感约 60s：源片按倍速反推；给模型明确目标窗
    final_lo, final_hi = 55, 65
    user_payload: dict[str, Any] = {
        "target_s": target_seconds,
        "final_window_s": [final_lo, final_hi],
        "speed": sp,
        "target_src_ms": target_source_ms,
        "n": len(compact),
        "must_cover": ["版型上身效果", "面料", "适用人群"],
        "rules": (
            "像短视频不像直播;"
            "必含版型+面料+适用人群卖点;"
            "去控场/尺码/价格/发货;"
            f"成片约{target_seconds}s(可{final_lo}-{final_hi});源片贴近target_src_ms;"
            "顺序钩子→版型→面料细节→适用人群→体验收束;"
            "只用输入id;完整句;禁静音尾巴"
        ),
        "clauses": compact,
    }
    hints = _learning_hints()
    if hints:
        user_payload["hints"] = hints

    user_text = (
        "已筛选口播小句。提取 main_points 并选/排 keep，只输出JSON：\n"
        + json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_LIGHT},
        {"role": "user", "content": user_text},
    ]

    try:
        out = chat_completions(
            messages=messages,
            model=model,
            base_url=base,
            api_key=key,
            temperature=0.2,
            max_tokens=PLAN_MAX_TOKENS,
            force_json=True,
            timeout=PLAN_TIMEOUT_S,
            cfg=cfg,
            fast=True,  # prefer last_route; tight retries for speed
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
        "input_clauses_raw": trim_stats.get("clauses_raw"),
        "input_clauses": len(clauses),
        "clauses_sent": trim_stats.get("clauses_sent"),
        "trim_stats": trim_stats,
        "submit_mode": "light_asr_selected_clauses",
        "compat": {
            "auth_variant": out.get("auth_variant"),
            "payload_variant": out.get("payload_variant"),
            "endpoint": out.get("endpoint"),
        },
        "auth_source": "user_ui",
        "client": "openai_compat_full",
    }
    # selected clauses for id resolution; raw for optional neighbor completion
    obj["_clauses"] = clauses
    obj["_clauses_raw"] = clauses_all
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
        # hard drop price / shipping even if LLM kept them
        if _is_price_or_shipping(text):
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
        # Prefer shorter complete cut over padding into silence/black after speech.
        warnings.append(f"short_but_complete_ms={tot}")

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
