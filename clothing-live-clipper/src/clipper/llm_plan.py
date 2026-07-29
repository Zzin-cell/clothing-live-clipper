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

JSON（id 必须原样复制输入里的 id，如 c12 / c00012，禁止乱写长串0）：
{"product_summary":"...","hook_type":"visual|pain|effect","main_points":["版型","面料","适用人群"],"logic":["钩子","版型","面料","人群","收束"],"keep":[{"id":"c12","t0_ms":0,"t1_ms":1000,"text":"...","why":"...","point":"版型","complete":true}],"drop_ids":["c1"],"notes":"..."}
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
    Expand ASR into planning units.

    Important: do NOT hard-split every comma into ~2s crumbs — that makes it
    impossible to hit ~60s final even with many keep items. Keep parent windows
    when reasonably sized; only split very long utterances into chunky pieces.
    """
    parents = _normalize_lines(raw_lines)
    out: list[dict[str, Any]] = []
    cid = 0
    for p in parents:
        if cid >= max_clauses:
            break
        text = str(p.get("text") or "").strip()
        t0 = int(p["t0_ms"])
        t1 = max(t0 + 300, int(p["t1_ms"]))
        span = t1 - t0
        # Keep medium windows intact for duration + sell density
        if span <= 9000 or len(text) <= 42:
            out.append(
                {
                    "id": f"c{cid:05d}",
                    "utt_id": f"c{cid:05d}",
                    "parent_id": p["id"],
                    "text": text,
                    "t0_ms": t0,
                    "t1_ms": t1,
                }
            )
            cid += 1
            continue
        parts = split_clauses(text) or [text]
        # merge tiny split parts into larger chunks (~8–12 chars min)
        merged_parts: list[str] = []
        buf = ""
        for part in parts:
            if not buf:
                buf = part
            elif len(buf) < 10:
                buf = f"{buf}{part}"
            else:
                merged_parts.append(buf)
                buf = part
        if buf:
            merged_parts.append(buf)
        if not merged_parts:
            merged_parts = [text]
        n = max(1, len(merged_parts))
        # each piece at least ~1.2s when possible
        step = max(1200, span // n)
        cur = t0
        for j, ctext in enumerate(merged_parts):
            if cid >= max_clauses:
                return out
            if j == n - 1:
                ct1 = t1
            else:
                ct1 = min(t1, cur + step)
            if ct1 <= cur:
                ct1 = min(t1, cur + 1200)
            out.append(
                {
                    "id": f"c{cid:05d}",
                    "utt_id": f"c{cid:05d}",
                    "parent_id": p["id"],
                    "text": ctext,
                    "t0_ms": cur,
                    "t1_ms": max(cur + 500, ct1),
                }
            )
            cid += 1
            cur = ct1
    return out


# Latency-first caps (SiliconFlow lightweight path)
# Stability-first: small payload → fewer SiliconFlow timeouts
LIGHT_MAX_CLAUSES = 28
CLAUSE_TEXT_MAX = 48
PLAN_MAX_TOKENS = 700
PLAN_TIMEOUT_S = 100
# Second attempt uses an even smaller payload if first times out
LIGHT_MAX_CLAUSES_RETRY = 18
CLAUSE_TEXT_MAX_RETRY = 40
PLAN_MAX_TOKENS_RETRY = 500

_CONTROL_MARKERS = (
    # 称呼/互动
    "家人们", "老铁", "宝宝", "姐妹们", "宝贝们", "扣1", "扣一", "点关注", "双击",
    "晚上好", "大家好", "早上好", "欢迎", "公屏", "弹幕", "福袋", "连麦",
    # 设备/调试
    "调试", "对焦", "收音", "听得到", "在不在", "来了吗",
    # 交易入口
    "链接", "小黄车", "加购", "上链接", "购物车",
    # 导播/准备口令（截图：准备一下 / 321 / 里面去拍）
    "准备一下", "来准备", "先准备", "準備一下", "來準備", "备一下", "備一下",
    "里面去拍", "裏面去拍", "里面拍", "出去拍", "换个机位", "转个机位",
    "321", "3 2 1", "三二一", "倒计时", "倒數", "倒数",
    "过一下", "过一遍", "先上", "上脚", "上裤", "来凳", "凳子", "板凳",
    "用鞋把", "卡一下", "固定一下", "摆一下", "站好", "转一圈给你看一下哦等下",
)
_SIZE_MARKERS = (
    "尺码", "M码", "L码", "m码", "S码", "s码", "XL", "胸围", "腰围", "偏大", "偏小", "建议穿",
    "斤穿", "斤", "码穿",
)
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
    "吸湿", "干爽", "不热", "凉快", "丝丝", "冰冰", "薄", "保护款", "高密", "高质",
)
_AUDIENCE_MARKERS = (
    "适用", "适合", "人群", "微胖", "梨形", "小个子", "大码", "胖妹妹", "通勤",
    "日常", "上班", "夏天", "秋冬", "显白", "黄黑皮", "黑皮", "白皮", "皮肤",
    "姐妹可以穿", "谁穿", "什么人", "胯宽", "肚子", "比例",
)
_VALUE_MARKERS = _FIT_MARKERS + _FABRIC_MARKERS + _AUDIENCE_MARKERS + (
    "细节", "蕾丝", "刺绣", "拼接", "对比", "舒服", "好穿",
)


def _is_control(text: str) -> bool:
    t = text or ""
    if any(x in t for x in _CONTROL_MARKERS):
        return True
    # 3 2 1 / 321 倒计时口令
    if re.search(r"(?<!\d)3\s*2\s*1(?!\d)", t):
        return True
    if re.search(r"(准备|準備|备一下|備一下).{0,6}(一下|下)", t):
        return True
    if re.search(r"(里面|裏面|外头|外面).{0,4}(拍|去拍)", t):
        return True
    # 纯控场短句：几乎没有服装卖点
    if re.search(r"(好不好|来吧|走起|开始了|开始播)", t) and not any(
        k in t for k in ("面料", "版型", "显瘦", "上身", "适合", "遮肉", "垂感", "不透")
    ):
        # 含准备/拍摄口令时更坚决
        if any(k in t for k in ("准备", "準備", "拍", "321", "倒计")):
            return True
    return False


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


def _build_plan_messages(
    clauses: list[dict[str, Any]],
    *,
    target_seconds: int,
    sp: float,
    target_source_ms: int,
    text_max: int,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Build compact chat messages + short-id map + compact clause list."""
    id_map: dict[str, str] = {}
    compact: list[dict[str, Any]] = []
    for u in clauses:
        full = str(u["id"])
        m = re.search(r"(\d+)$", full)
        short = f"c{int(m.group(1))}" if m else full
        if short in id_map and id_map[short] != full:
            short = full
        id_map[short] = full
        compact.append(
            {
                "id": short,
                "t0": int(u["t0_ms"]),
                "t1": int(u["t1_ms"]),
                "text": str(u["text"])[:text_max],
            }
        )
    final_lo, final_hi = 55, 65
    user_payload: dict[str, Any] = {
        "target_s": target_seconds,
        "final_window_s": [final_lo, final_hi],
        "speed": sp,
        "target_src_ms": target_source_ms,
        "n": len(compact),
        "must_cover": ["版型", "面料", "适用人群"],
        "rules": (
            "去直播感/尺码/价格/发货;"
            "必含版型+面料+适用人群;"
            f"成片约{target_seconds}s;"
            "钩子→版型→面料→人群→收束;只用输入id"
        ),
        "clauses": compact,
    }
    user_text = (
        "已筛选口播小句。只输出JSON keep（id必须原样复制）：\n"
        + json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))
    )
    # Ultra-short system for speed/stability; full light prompt still available but heavy.
    system = (
        "服装短视频剪辑。输入小句id/t0/t1/text。"
        "选keep重排：钩子→版型→面料→适用人群→收束。"
        "删直播控场/尺码/价格/发货。只用输入id，完整句，禁编造。"
        "只输出JSON:"
        '{"product_summary":"...","hook_type":"visual|pain|effect","main_points":["版型","面料","人群"],'
        '"keep":[{"id":"c12","t0_ms":0,"t1_ms":1,"text":"...","why":"...","point":"版型","complete":true}],'
        '"drop_ids":[],"notes":"..."}'
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    return messages, id_map, compact


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

    attempts = [
        {
            "max_clauses": LIGHT_MAX_CLAUSES,
            "text_max": CLAUSE_TEXT_MAX,
            "max_tokens": PLAN_MAX_TOKENS,
            "timeout": PLAN_TIMEOUT_S,
            "label": "primary",
        },
        {
            "max_clauses": LIGHT_MAX_CLAUSES_RETRY,
            "text_max": CLAUSE_TEXT_MAX_RETRY,
            "max_tokens": PLAN_MAX_TOKENS_RETRY,
            "timeout": min(70, PLAN_TIMEOUT_S),
            "label": "retry_light",
        },
    ]

    last_err: Exception | None = None
    out: dict[str, Any] | None = None
    clauses: list[dict[str, Any]] = []
    trim_stats: dict[str, Any] = {}
    id_map: dict[str, str] = {}
    attempt_label = "primary"

    for att in attempts:
        clauses, trim_stats = select_clauses_for_llm(
            clauses_all, max_clauses=int(att["max_clauses"])
        )
        messages, id_map, _compact = _build_plan_messages(
            clauses,
            target_seconds=target_seconds,
            sp=sp,
            target_source_ms=target_source_ms,
            text_max=int(att["text_max"]),
        )
        try:
            out = chat_completions(
                messages=messages,
                model=model,
                base_url=base,
                api_key=key,
                temperature=0.1,
                max_tokens=int(att["max_tokens"]),
                force_json=True,
                timeout=int(att["timeout"]),
                cfg=cfg,
                fast=True,  # last_route + single payload, no multi-timeout burn
            )
            attempt_label = str(att["label"])
            last_err = None
            break
        except OpenAICompatError as e:
            last_err = e
            msg = str(e).lower()
            # Only retry on timeout/network-ish failures with lighter payload
            if "timeout" not in msg and "timed out" not in msg and "10054" not in msg:
                raise RuntimeError(f"llm_request_failed:{e}") from e
            continue

    if out is None:
        raise RuntimeError(f"llm_request_failed:{last_err}")

    content = out.get("content") or ""
    obj = _extract_json_obj(content)
    # Expand short ids (c12) back to canonical c00012 before repair/timeline
    keep = obj.get("keep") if isinstance(obj.get("keep"), list) else []
    for item in keep:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "").strip()
        if raw_id in id_map:
            item["id"] = id_map[raw_id]
        else:
            m = re.search(r"c?0*(\d{1,5})", raw_id, flags=re.I)
            if m:
                full = f"c{int(m.group(1)):05d}"
                if full in {c["id"] for c in clauses}:
                    item["id"] = full
    obj = _repair_keep_ids(obj, clauses)
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
        "submit_mode": "stable_light_asr_selected_clauses",
        "attempt": attempt_label,
        "compat": {
            "auth_variant": out.get("auth_variant"),
            "payload_variant": out.get("payload_variant"),
            "endpoint": out.get("endpoint"),
        },
        "auth_source": "user_ui",
        "client": "openai_compat_full",
        "latency_ms": out.get("latency_ms"),
        "timeout_s": PLAN_TIMEOUT_S,
    }
    # selected clauses for id resolution; raw for optional neighbor completion
    obj["_clauses"] = clauses
    obj["_clauses_raw"] = clauses_all
    return obj


def _repair_keep_ids(obj: dict[str, Any], clauses: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Small models often invent broken keep ids (e.g. c0000000...1).
    Remap by text/time; if still empty, build a safe keep from top clothing clauses
    so we don't fall back to pure rules with a "connected" LLM.
    """
    if not isinstance(obj, dict):
        obj = {}
    keep = obj.get("keep")
    if not isinstance(keep, list):
        keep = []
    by_id = {str(c.get("id")): c for c in clauses}
    fixed: list[dict[str, Any]] = []
    used: set[str] = set()

    def match_clause(item: dict[str, Any]) -> dict[str, Any] | None:
        uid = str(item.get("id") or item.get("utt_id") or "").strip()
        if uid in by_id:
            return by_id[uid]
        m = re.search(r"c0*(\d{1,5})", uid, flags=re.I)
        if m:
            cand = f"c{int(m.group(1)):05d}"
            if cand in by_id:
                return by_id[cand]
        text_i = re.sub(r"\s+", "", str(item.get("text") or ""))
        if text_i:
            for n in (14, 10, 8, 6):
                if len(text_i) < n:
                    continue
                needle = text_i[:n]
                for c in clauses:
                    ut = re.sub(r"\s+", "", str(c.get("text") or ""))
                    if needle in ut or ut[:n] in text_i:
                        return c
        try:
            t0 = int(item.get("t0_ms") or item.get("t0") or -1)
            t1 = int(item.get("t1_ms") or item.get("t1") or -1)
        except Exception:
            t0, t1 = -1, -1
        if t0 >= 0 and t1 > t0:
            best, best_ov = None, 0
            for c in clauses:
                u0, u1 = int(c["t0_ms"]), int(c["t1_ms"])
                ov = max(0, min(t1, u1) - max(t0, u0))
                if ov > best_ov:
                    best, best_ov = c, ov
            if best is not None and best_ov >= 250:
                return best
        return None

    for item in keep:
        if not isinstance(item, dict):
            continue
        src = match_clause(item)
        if not src:
            continue
        sid = str(src.get("id"))
        if sid in used:
            continue
        if _is_price_or_shipping(str(src.get("text") or "")) or _is_size(str(src.get("text") or "")) or _is_control(
            str(src.get("text") or "")
        ):
            continue
        used.add(sid)
        fixed.append(
            {
                "id": sid,
                "t0_ms": int(src["t0_ms"]),
                "t1_ms": int(src["t1_ms"]),
                "text": str(src.get("text") or ""),
                "why": str(item.get("why") or "remap_keep"),
                "point": str(item.get("point") or ""),
                "complete": True,
            }
        )

    repaired = False

    def _bucket(tx: str) -> str:
        if any(k in tx for k in _FIT_MARKERS):
            return "fit"
        if any(k in tx for k in _FABRIC_MARKERS):
            return "fabric"
        if any(k in tx for k in _AUDIENCE_MARKERS):
            return "audience"
        return "other"

    def _total_ms() -> int:
        return sum(max(0, int(x.get("t1_ms") or 0) - int(x.get("t0_ms") or 0)) for x in fixed)

    def _coverage() -> dict[str, bool]:
        blob = " ".join(str(x.get("text") or "") for x in fixed)
        return {
            "fit": any(k in blob for k in _FIT_MARKERS),
            "fabric": any(k in blob for k in _FABRIC_MARKERS),
            "audience": any(k in blob for k in _AUDIENCE_MARKERS),
        }

    def _add_clause(c: dict[str, Any], *, why: str, point: str) -> bool:
        nonlocal repaired
        sid = str(c.get("id"))
        if sid in used:
            return False
        tx = str(c.get("text") or "")
        if not tx or _is_control(tx) or _is_size(tx) or _is_price_or_shipping(tx):
            return False
        used.add(sid)
        fixed.append(
            {
                "id": sid,
                "t0_ms": int(c["t0_ms"]),
                "t1_ms": int(c["t1_ms"]),
                "text": tx,
                "why": why,
                "point": point,
                "complete": True,
            }
        )
        repaired = True
        return True

    # 1) Force-cover fit / fabric / audience whenever ASR has them
    for need, markers, point in (
        ("fit", _FIT_MARKERS, "版型"),
        ("fabric", _FABRIC_MARKERS, "面料"),
        ("audience", _AUDIENCE_MARKERS, "适用人群"),
    ):
        cov = _coverage()
        if cov.get(need):
            continue
        cands = [
            c
            for c in clauses
            if str(c.get("id")) not in used
            and any(k in str(c.get("text") or "") for k in markers)
            and not _is_control(str(c.get("text") or ""))
            and not _is_size(str(c.get("text") or ""))
            and not _is_price_or_shipping(str(c.get("text") or ""))
        ]
        cands.sort(key=lambda c: _value_score(str(c.get("text") or "")), reverse=True)
        for c in cands[:2]:
            _add_clause(c, why=f"force_cover_{need}", point=point)

    # 2) Fill duration toward ~60s final (source ≈ 75–84s @1.4x)
    # Prefer clauses that close coverage gaps; then high-value others.
    def _fill_duration(min_n: int, target_ms: int) -> None:
        def rank_key(c: dict[str, Any]) -> tuple:
            tx = str(c.get("text") or "")
            b = _bucket(tx)
            cov = _coverage()
            gap = 0
            if b == "fit" and not cov["fit"]:
                gap = 3
            elif b == "fabric" and not cov["fabric"]:
                gap = 3
            elif b == "audience" and not cov["audience"]:
                gap = 3
            return (gap, _value_score(tx))

        ranked = sorted(clauses, key=rank_key, reverse=True)
        for c in ranked:
            if len(fixed) >= max(min_n, 24) and _total_ms() >= target_ms:
                break
            tx = str(c.get("text") or "")
            if _value_score(tx) < 2 and _bucket(tx) == "other":
                continue
            _add_clause(c, why="duration_fill", point=_bucket(tx) if _bucket(tx) != "other" else "卖点")

    # Always top-up hard toward ~60s final source budget
    # 1.4x playback → need ~78–90s source for ~56–64s final
    _fill_duration(min_n=12, target_ms=90_000)
    # Second pass: if still short, accept medium-value clothing lines too
    if _total_ms() < 78_000:
        for c in sorted(clauses, key=lambda x: int(x.get("t0_ms") or 0)):
            if _total_ms() >= 88_000 or len(fixed) >= 28:
                break
            tx = str(c.get("text") or "")
            if _value_score(tx) < 1:
                continue
            if any(k in tx for k in ("面料", "版型", "显瘦", "上身", "适合", "软", "垂", "透气", "遮肉", "穿")):
                _add_clause(c, why="duration_fill_2", point=_bucket(tx) if _bucket(tx) != "other" else "卖点")

    # chronological for natural watch order
    fixed.sort(key=lambda x: int(x.get("t0_ms") or 0))
    obj["keep"] = fixed
    cov_final = _coverage()
    obj["_coverage"] = cov_final
    if repaired or len(fixed) != len(keep):
        obj["_keep_repaired"] = True
        obj["_keep_raw_n"] = len(keep)
        obj["_keep_fixed_n"] = len(fixed)
        obj["_keep_total_ms"] = _total_ms()
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

    def _norm_id(raw: str) -> str:
        """Normalize model-mangled ids: c1 / C00001 / c0001 → c00001 when possible."""
        s = str(raw or "").strip()
        if not s:
            return ""
        if s in by_id or s in parents:
            return s
        m = re.search(r"c0*(\d{1,5})", s, flags=re.I)
        if m:
            cand = f"c{int(m.group(1)):05d}"
            if cand in by_id or cand in parents:
                return cand
        # pure digits
        if s.isdigit():
            cand = f"c{int(s):05d}"
            if cand in by_id or cand in parents:
                return cand
        return s

    def _resolve_src(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        uid = _norm_id(str(item.get("id") or item.get("utt_id") or ""))
        src = by_id.get(uid) or parents.get(uid)
        if src:
            return str(src.get("id") or uid), src
        text_i = re.sub(r"\s+", "", str(item.get("text") or "").strip())
        if text_i:
            # longer prefix match first; tolerate short model paraphrases
            for n in (16, 12, 10, 8, 6):
                if len(text_i) < n:
                    continue
                needle = text_i[:n]
                for u in by_id.values():
                    ut = re.sub(r"\s+", "", str(u.get("text") or ""))
                    if needle and (needle in ut or ut[:n] in text_i):
                        return str(u.get("id")), u
            # token overlap fallback (Chinese 2-grams)
            if len(text_i) >= 6:
                grams = {text_i[i : i + 2] for i in range(0, min(len(text_i) - 1, 24))}
                best_u = None
                best_hit = 0
                for u in by_id.values():
                    ut = re.sub(r"\s+", "", str(u.get("text") or ""))
                    hit = sum(1 for g in grams if g in ut)
                    if hit > best_hit:
                        best_hit, best_u = hit, u
                if best_u is not None and best_hit >= 3:
                    return str(best_u.get("id")), best_u
        # time-window fallback if model invents ids but keeps t0/t1
        try:
            t0 = int(item.get("t0_ms") or item.get("t0") or -1)
            t1 = int(item.get("t1_ms") or item.get("t1") or -1)
        except Exception:
            t0, t1 = -1, -1
        if t0 >= 0 and t1 > t0:
            best_u = None
            best_overlap = 0
            for u in by_id.values():
                u0, u1 = int(u["t0_ms"]), int(u["t1_ms"])
                overlap = max(0, min(t1, u1) - max(t0, u0))
                if overlap > best_overlap:
                    best_overlap, best_u = overlap, u
            if best_u is not None and best_overlap >= 300:
                return str(best_u.get("id")), best_u
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
        # hard drop price / shipping / live-control even if LLM kept them
        if _is_price_or_shipping(text) or _is_control(text):
            return
        # always take full clause window first (avoid mid-clause cutoff)
        t0 = int(src["t0_ms"])
        t1 = max(t0 + 500, int(src["t1_ms"]))
        # do NOT pad tails (padding caused black/silent gaps)
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

    # duration trim toward target source length for ~60s final after speed
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    aim = int(round(target_seconds * 1000 * sp))
    # Prefer filling near aim; only allow mildly short when content truly insufficient
    min_ms = int(aim * 0.90)
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
    # If still short, pull more sell clauses (no silent pad)
    guard = 0
    while total_ms() < min_ms and guard < 30:
        guard += 1
        added = False
        for u in ordered:
            uid = str(u.get("id"))
            if uid in used_ids:
                continue
            tx = str(u.get("text") or "")
            if _is_control(tx) or _is_size(tx) or _is_price_or_shipping(tx):
                continue
            if _value_score(tx) < 2:
                continue
            before = total_ms()
            _append_from_src(uid, u, why="timeline_duration_fill", score=35)
            if total_ms() > before:
                added = True
                break
        if not added:
            break
    if total_ms() < min_ms:
        warnings.append(f"short_but_complete_ms={total_ms()}")
    if llm_obj.get("_coverage"):
        warnings.append(f"coverage:{llm_obj.get('_coverage')}")

    # final guard: never end with incomplete text
    if slots and _looks_incomplete_text(slots[-1].text) and len(slots) > 1:
        slots.pop()
        warnings.append("dropped_incomplete_tail")

    # Keep rough narrative order preference: fit/fabric/audience mixed but opener first already from keep
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


def plan_from_local_clauses(
    lines: list[dict[str, Any]],
    *,
    target_seconds: int = 60,
    playback_speed: float = 1.4,
) -> tuple[TimelinePlan, dict[str, Any]]:
    """Offline clothing-taste plan (no network). Used when cloud LLM times out."""
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    clauses_all = expand_lines_to_clauses(lines, max_clauses=420)
    clauses, trim_stats = select_clauses_for_llm(clauses_all, max_clauses=LIGHT_MAX_CLAUSES)
    obj = _repair_keep_ids(
        {
            "product_summary": "本地卖点排片（云端LLM不可用时）",
            "hook_type": "effect",
            "main_points": ["版型", "面料", "适用人群"],
            "logic": ["钩子", "版型", "面料", "人群", "收束"],
            "keep": [],
            "drop_ids": [],
            "notes": "cloud_timeout_local_fill",
        },
        clauses,
    )
    obj["_clauses"] = clauses
    obj["_clauses_raw"] = clauses_all
    obj["_meta"] = {
        "model": "local_clause_rank",
        "submit_mode": "local_after_llm_fail",
        "target_source_ms": int(round(target_seconds * 1000 * sp)),
        "input_clauses_raw": trim_stats.get("clauses_raw"),
        "clauses_sent": trim_stats.get("clauses_sent"),
        "trim_stats": trim_stats,
    }
    plan = llm_obj_to_timeline(
        obj,
        lines,
        target_seconds=target_seconds,
        playback_speed=sp,
    )
    if not plan.golden:
        raise RuntimeError("local_clause_plan_empty")
    plan.warnings = list(plan.warnings or []) + ["policy:local_fill_after_llm_fail"]
    return plan, obj


def plan_from_asr_with_llm(
    lines: list[dict[str, Any]],
    *,
    target_seconds: int = 60,
    playback_speed: float = 1.4,
    settings: Settings | None = None,
) -> tuple[TimelinePlan, dict[str, Any]]:
    """
    Returns (plan, debug_obj).

    Priority:
    1) cloud LLM keep (repaired)
    2) if too short / missing fit-fabric-audience and rules can do better duration → hybrid
    3) local clause fill
    """
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    aim_src = int(round(target_seconds * 1000 * sp))  # ~84s for 60s@1.4x
    min_ok_src = int(aim_src * 0.72)  # ~60s source => ~43s final; still short but better floor

    cloud_err: str | None = None
    plan: TimelinePlan | None = None
    obj: dict[str, Any] = {}
    try:
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
            clauses = obj.get("_clauses") if isinstance(obj.get("_clauses"), list) else []
            if clauses:
                obj2 = _repair_keep_ids({"keep": []}, clauses)
                obj2["_clauses"] = clauses
                plan = llm_obj_to_timeline(
                    obj2,
                    lines,
                    target_seconds=target_seconds,
                    playback_speed=playback_speed,
                )
                obj = obj2
        if not plan.golden:
            cloud_err = "llm_plan_has_no_slots"
            plan = None
    except Exception as e:
        cloud_err = str(e)[:300]
        plan = None

    # Always have a local clothing plan as baseline
    local_plan, local_obj = plan_from_local_clauses(
        lines, target_seconds=target_seconds, playback_speed=playback_speed
    )

    # Rules plan for duration when ASR is sparse on keywords
    rules_plan = None
    try:
        from clipper.config import Settings as _Settings
        from clipper.models import TranscriptUtterance
        from clipper.extract import extract_claims, split_long_utterance, utterances_to_clips
        from clipper.rank import build_timeline_plan, score_all

        utts: list[TranscriptUtterance] = []
        for i, u in enumerate(lines or []):
            if not isinstance(u, dict):
                continue
            tx = str(u.get("text") or "").strip()
            if not tx:
                continue
            t0 = int(u.get("t0_ms") or 0)
            t1 = max(t0 + 300, int(u.get("t1_ms") or 0))
            utts.append(
                TranscriptUtterance(
                    utt_id=str(u.get("utt_id") or u.get("id") or f"u{i}"),
                    text=tx,
                    t0_ms=t0,
                    t1_ms=t1,
                )
            )
        transcript: list[TranscriptUtterance] = []
        for u in utts:
            transcript.extend(split_long_utterance(u))
        claims = extract_claims(transcript)
        clips = score_all(
            utterances_to_clips(
                transcript,
                claims=claims,
                min_clip_ms=500,
                max_clip_ms=15_000,
            )
        )
        st = settings if isinstance(settings, _Settings) else _Settings(
            target_duration_s=target_seconds,
            playback_speed=sp,
        )
        rules_plan = build_timeline_plan(clips, st)
    except Exception:
        rules_plan = None

    def _cov(p: TimelinePlan) -> dict[str, bool]:
        blob = " ".join(s.text or "" for s in (p.golden or []))
        return {
            "fit": any(k in blob for k in _FIT_MARKERS),
            "fabric": any(k in blob for k in _FABRIC_MARKERS),
            "audience": any(k in blob for k in _AUDIENCE_MARKERS),
        }

    def _score(p: TimelinePlan | None) -> tuple:
        if p is None or not p.golden:
            return (-1, -1, -1, -1)
        cov = _cov(p)
        cov_n = int(cov["fit"]) + int(cov["fabric"]) + int(cov["audience"])
        # prefer near-target duration without being insanely short
        dur = int(p.total_duration_ms or 0)
        dur_score = min(dur, aim_src) - max(0, min_ok_src - dur) * 2
        return (cov_n, dur_score, len(p.golden), dur)

    candidates: list[tuple[str, TimelinePlan, dict[str, Any]]] = []
    if plan is not None and plan.golden:
        candidates.append(("cloud_or_repaired", plan, obj))
    if local_plan.golden:
        lo = dict(local_obj)
        meta = dict(lo.get("_meta") or {})
        meta["cloud_error"] = cloud_err
        lo["_meta"] = meta
        candidates.append(("local_clause_rank", local_plan, lo))
    if rules_plan is not None and rules_plan.golden:
        candidates.append(
            (
                "rules_duration",
                rules_plan,
                {
                    "product_summary": "规则时长兜底",
                    "main_points": ["版型", "面料", "适用人群"],
                    "keep": [],
                    "_meta": {
                        "model": "rules_duration_fallback",
                        "submit_mode": "rules_after_short_llm",
                        "cloud_error": cloud_err,
                    },
                },
            )
        )

    if not candidates:
        raise RuntimeError(cloud_err or "llm_plan_has_no_slots")

    best_name, best_plan, best_obj = max(candidates, key=lambda x: _score(x[1]))
    meta = dict(best_obj.get("_meta") or {})
    meta["chosen_path"] = best_name
    meta["cloud_error"] = cloud_err
    meta["candidate_scores"] = {
        name: {
            "slots": len(p.golden or []),
            "ms": int(p.total_duration_ms or 0),
            "cov": _cov(p),
            "score": _score(p),
        }
        for name, p, _o in candidates
    }
    best_obj["_meta"] = meta
    # ensure warnings mark path
    best_plan.warnings = list(best_plan.warnings or []) + [f"policy:plan_path:{best_name}"]
    if cloud_err:
        best_plan.warnings.append(f"cloud_error:{cloud_err[:120]}")
    return best_plan, best_obj
