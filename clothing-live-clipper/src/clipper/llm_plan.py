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
from clipper.learning import learned_text_score, learning_status, split_clauses
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
一、开场钩子（前 3 秒，只留 1 句最吸睛）
====================
优先直接上「最吸睛」画面对应的口播（禁止问候/价格）：
1) 上身效果冲击：全身显瘦/收腰/遮肉/比例（首选）
2) 面料特写冲击：软、垂、不透、凉感、肌理（次选）
不要用痛点说教、福利价开场。

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
三、硬性剔除（始终保留，优先于任何结构优化）
====================
一律 drop，不可因凑时长/覆盖回填：
- 尺码顾问：报码、偏大偏小、围度、建议穿、胸大胸小
- 价格/发货/物流/促销/小黄车链接
- 直播控场与无关闲聊：打招呼、扣1、点关注、准备一下/321、调试、喝水卡顿
- 人设/标签灌鸡汤等无服装卖点内容

====================
四、内容优先级与成片顺序（结构优化，叠在硬规则之上）
====================
在已通过硬剔除的卖点中排序：
全身效果 ＞ 细节做工 ＞ 穿搭场景
1) 0–3s：最吸睛上身效果 或 面料特写（一句打满）
2) 全身效果：版型/上身/显瘦遮肉
3) 细节做工：面料/工艺细点
4) 穿搭场景：适用人群 + 场景/体验收束
目标成片观感约 55–65 秒（默认 60 秒@倍速后），像短视频不像直播。

口播画面类型提示（辅助选句，不覆盖硬删）：
- 全身/全景：版型、长短、显瘦、适配人群
- 半身/近景：领口、肩线、腰线
- 细节特写：面料肌理、刺绣、走线
- 场景：通勤、上班、日常、夏天、好搭

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
五-B、同主题小句必须成块连排（禁止主题打散）
====================
1) 同一逻辑块内的相关小句必须相邻成组，禁止「面料→版型→又面料→又版型」来回跳
2) 主题块建议整块排放（块内可按原口播时间或信息递进，但不要跨块穿插）：
   - 版型/上身效果块：显瘦、收腰、遮肉、长短、肩线腰线、上身比例
   - 面料/体感块：软、垂、透气、不透、凉感、亲肤、抗皱
   - 细节/做工块：蕾丝、拼接、走线、口袋、领口袖口工艺
   - 人群/场景块：适合谁、通勤、夏天、日常、微胖梨形
3) 反例（禁止）：正在讲“不透/凉感”，中间突然插“显瘦收腰”，后面再回来讲面料
4) 正例：开场钩子1句 → 整块上身效果2–4句连着讲完 → 整块面料体验连着讲完 → 细节/场景 → 收束
5) 若两句讲同一卖点（同 point / 同主题），keep 数组里必须紧挨着，中间不得夹其它主题
6) 完整模块语义优先于“按原直播时间碎拼”

====================
六、技术硬规则
====================
1) 输入是全量小句；先提炼 main_points，再从中选 keep 并重排
2) 只能使用输入小句 id；优先整句采用该小句完整 t0~t1，不要随意砍半句
3) 总源片时长尽量接近 target_source_ms；若无法完整讲完，宁可少 5–8 秒，也要完整
4) 硬删保留：尺码建议、任何价格/发货/物流、直播控场、人设鸡汤、与服装无关、幻觉垃圾（对对对、xy）
5) 结构优化不取消硬删：keep 按 3s吸睛→全身效果→细节做工→穿搭场景；最后 1–2 条收束完整
6) 只输出严格 JSON，不要 markdown

输出 JSON schema:
{
  "product_summary": "一句话主卖点",
  "hook_type": "visual|pain|effect",
  "main_points": ["主卖点1","版型点","面料/适用人群","细节点"],
  "logic": ["3s吸睛钩子","全身效果","细节做工","穿搭场景","收束"],
  "keep": [
    {"id":"c00012","t0_ms":12300,"t1_ms":15800,"text":"...","why":"3s上身效果/面料特写","point":"显瘦","complete":true}
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

【硬性 DROP（始终保留，不可削弱）】
- 尺码顾问：M/L/S/XL、偏大偏小、围度报数、建议穿、胸大胸小、报码
- 价格/发货：定价拨分、多少钱、券后包邮秒杀、发货物流现货预售、小黄车链接加购
- 直播控场：家人们/宝宝/老铁、扣1点关注、公屏福袋、欢迎、准备一下/321、调试对焦
- 人设灌鸡汤：标签定义、甄姐标签等无服装信息话术
- 与服装无关：闲聊、喝水、卡顿、回答无关弹幕、空泛情绪复读

【必须 KEEP 的卖点（在 DROP 之后的优先级）】
1) 版型/上身：显瘦、收腰、修身、长短、遮肉、不显胯、肩线领口腰线
2) 面料/做工：软、垂感、透气、不闷、冰凉、不透、亲肤、抗皱、蕾丝拼接细节
3) 适用人群/场景：微胖、梨形、小个子、通勤、日常、季节
4) 对比/体验：穿上前后、两色、好穿不难受

【结构优化（叠在硬规则之上，不是替换）】
顺序：0–3s 最吸睛(上身效果/面料特写) → 全身效果 → 细节做工 → 穿搭场景(含人群) → 自然收束
信息密度要高；完整表达优先于硬凑时长（可短 3–8 秒，禁静音尾巴）

【同主题连排 · 强制】
- 同一主题小句必须连在一起成块，禁止版型/面料/细节来回穿插
- 例：显瘦收腰句全部连排后再进入面料软垂不透块，勿「显瘦→不透→又收腰」
- keep 数组相邻项的 point/主题应尽量相同；换主题时整块切换

JSON（id 必须原样复制输入里的 id，如 c12 / c00012，禁止乱写长串0）：
{"product_summary":"...","hook_type":"visual|effect","main_points":["全身效果","细节做工","穿搭场景"],"logic":["3s吸睛","全身效果","细节做工","穿搭场景","收束"],"keep":[{"id":"c12","t0_ms":0,"t1_ms":1000,"text":"...","why":"...","point":"版型","complete":true}],"drop_ids":["c1"],"notes":"..."}
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
        if any(
            k in obj
            for k in (
                "keep",
                "ids",
                "keep_ids",
                "sel",
                "selected",
                "main_points",
                "product_summary",
            )
        ):
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


# Stability-first caps (SiliconFlow Qwen2.5-7B path)
# Root cause of ~10% cloud success: full keep objects (id+t0+t1+text+why)
# timeout / echo / garbage. Id-only schema finishes in ~1–3s in probes.
LIGHT_MAX_CLAUSES = 20
CLAUSE_TEXT_MAX = 28
PLAN_MAX_TOKENS = 160
PLAN_TIMEOUT_S = 45
# Second attempt: even lighter + shorter timeout budget
LIGHT_MAX_CLAUSES_RETRY = 12
CLAUSE_TEXT_MAX_RETRY = 22
PLAN_MAX_TOKENS_RETRY = 100
PLAN_TIMEOUT_S_RETRY = 28

_CONTROL_MARKERS = (
    # 称呼/互动
    "家人们", "老铁", "宝宝", "姐妹们", "宝贝们", "扣1", "扣一", "点关注", "双击",
    "晚上好", "大家好", "早上好", "欢迎", "公屏", "弹幕", "福袋", "连麦",
    # 设备/调试
    "调试", "对焦", "收音", "听得到", "在不在", "来了吗",
    # 交易入口 / 直播催单（加单 + 挂车链接 + n号）
    "链接", "鏈接", "小黄车", "小黃車", "加购", "加購", "上链接", "上鏈接", "购物车", "購物車",
    "加一单", "再加一单", "赶紧加", "赶快加", "抓紧加", "全部加", "一起加", "全场加", "全部拍",
    "有货的加", "想要的加", "喜欢的加", "看上的加", "闭眼加", "闭眼拍",
    "拍一单", "再拍一单", "下单", "拍下", "库存告急", "手慢无",
    "号链接", "號鏈接", "号鏈接", "幾號鏈接", "几号链接", "几号鏈接",
    "1号链接", "2号链接", "3号链接", "4号链接", "5号链接", "6号链接",
    "一号链接", "二号链接", "三号链接", "四号链接", "五号链接",
    "点链接", "戳链接", "拍链接", "放链接", "挂链接", "给链接", "弹链接", "开链接",
    "左下角", "右下角", "上方链接", "下方链接", "挂上车", "上车了", "加车",
    # 导播/准备口令（截图：准备一下 / 321 / 里面去拍）
    "准备一下", "来准备", "先准备", "準備一下", "來準備", "备一下", "備一下",
    "里面去拍", "裏面去拍", "里面拍", "出去拍", "换个机位", "转个机位",
    "321", "3 2 1", "三二一", "倒计时", "倒數", "倒数",
    "过一下", "过一遍", "先上", "上脚", "上裤", "来凳", "凳子", "板凳",
    "用鞋把", "卡一下", "固定一下", "摆一下", "站好", "转一圈给你看一下哦等下",
    # 人设/标签灌鸡汤（非服装卖点）
    "定义我的标签", "不要随便定义", "甄姐的标签", "标签不是随意", "摸不着拆不透",
    "我的标签", "定义标签", "随便定义",
)

# Douyin / short-video compliance risk lines (not clothing value) — hard drop
_POLICY_RISK_MARKERS = (
    # 绝对化/极限承诺（平台易判定夸大）
    "最好", "最佳", "第一", "顶级", "国家级", "全网最低", "史上最低", "永久",
    "根治", "包治", "特效", "神奇", "神器", "保证瘦", "一定瘦", "三天瘦",
    "一穿就瘦", "瞬间瘦", "永久显瘦", "百分百", "100%", "绝对",
    # 医疗/效果承诺（穿搭非医疗器械）
    "治疗", "疗效", "处方", "医院同款", "医用", "防癌", "消炎",
    # 引流站外/导流（违规高发）
    "加微信", "加我微信", "薇信", "vx", "v信", "威信", "私信领", "私聊发",
    "扫码进群", "扫码加", "外部链接", "复制口令", "淘口令", "去淘宝",
    "点头像", "主页链接", "主页买", "评论区扣", "扣链接",
    # 比价引战/假货暗示
    "假货", "高仿", "专柜代购假", "走私", "水货",
)
_SIZE_MARKERS = (
    # 真·尺码顾问 / 报码（成片一律不要）——不含「不显肚子/不走光」等上身效果
    "尺码", "尺碼", "选码", "選碼", "报尺码", "報尺碼", "码数", "碼數", "试码", "試碼",
    "建议穿", "建議穿", "该穿什么", "該穿什麼", "推荐穿什么码", "推薦穿什麼碼",
    "偏大一码", "偏小一码", "大一码", "小一码", "大一號", "小一號",
    "加大码", "加大碼", "均码", "均碼", "中码", "中碼", "小码", "小碼", "大码穿", "大碼穿",
    # 字母码（含 ASR）
    "M码", "L码", "S码", "m码", "l码", "s码", "XL码", "XXL码", "xl码", "xxl码",
    "2XL", "3XL", "穿M", "穿S", "穿L", "穿XL", "穿XXL", "穿m", "穿s", "穿l",
    "S/M", "M/L", "L/XL", "SM码", "ML码",
    # 围度表（量体报数）
    "胸围", "腰围", "臀围", "肩宽", "袖长", "衣长", "裤长", "裙长", "胸圍", "腰圍", "臀圍",
    # 体重报码口语
    "斤穿", "斤的穿", "多少斤", "体重选", "身高选",
    "码穿", "號穿", "哪个码", "哪個碼", "什么码", "什麼碼", "几码", "幾碼",
    # 报罩杯/量体
    "罩杯", "卡满", "网袋胸", "钢圈", "杯型", "下围", "下圍",
)

# 上身/穿着效果（可进成片，优先开场服装特点）
_ONBODY_EFFECT_MARKERS = (
    "不走光", "不漏光", "走光", "漏光", "防走光", "遮走光",
    "不显肚子", "不显肚", "遮肚子", "遮肚", "藏肚子", "藏肚", "肚腩", "小肚子",
    "胃包", "拜拜肉", "蝴蝶袖", "副乳", "遮副乳", "收副乳",
    "不显胯", "遮胯", "提臀", "收腹", "显腿长", "显高", "显比例",
    "不透底", "不透光", "安全感", "坐着也", "弯腰也不",
)
# 价格/发货/物流/挂车加单：一律剔除（含开场福利、链接表达、n号、ASR 谐音/繁体）
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
    # 直播成交口令 / 加单 / ASR 谐音
    "加一单", "加一單", "加一捕", "再加一单", "再加一單", "赶紧加一单",
    "拍一单", "拍一單", "再拍一单", "补一单", "补一單", "上一单", "上一單",
    "加两单", "加俩单", "加几单", "拍两单", "拍几单", "再来一单", "再来单",
    "加一件", "拍一件", "来一单", "来一單", "整单拍", "整单加",
    "加一波", "冲一波", "秒了", "秒它", "锁单", "锁住", "锁一下",
    # 挂车 / 链接入口 / n号表达
    "链接", "鏈接", "小黄车", "小黃車", "购物车", "購物車", "加购", "加購",
    "上链接", "上鏈接", "点链接", "戳链接", "拍链接", "放链接", "挂链接", "开链接", "给链接",
    "号链接", "號鏈接", "几号链接", "幾號鏈接", "1号链接", "2号链接", "3号链接",
    "一号链接", "二号链接", "三号链接", "上方链接", "下方链接",
    "挂上车", "上车了", "加车", "加車", "上车", "上車", "弹窗", "彈窗", "领券",
)
# 成片要凸显的服装卖点（版型 / 上身效果 / 面料 / 适用人群）
_FIT_MARKERS = (
    "版型", "显瘦", "收腰", "修身", "遮肉", "高腰", "不显胯", "上身", "穿上", "长短",
    "领口", "腰线", "肩线",
) + _ONBODY_EFFECT_MARKERS
_FABRIC_MARKERS = (
    "面料", "布料", "材质", "软", "超软", "垂感", "透气", "不闷", "冰凉", "不透",
    "亲肤", "抗皱", "不起球", "弹力", "天丝", "醋酸", "雪纺", "纯棉", "凉感",
    "吸湿", "干爽", "不热", "凉快", "丝丝", "冰冰", "薄", "保护款", "高密", "高质",
)
_AUDIENCE_MARKERS = (
    "适用", "适合", "人群", "微胖", "梨形", "小个子", "大码友好", "胖妹妹", "通勤",
    "日常", "上班", "夏天", "秋冬", "显白", "黄黑皮", "黑皮", "白皮", "皮肤",
    "姐妹可以穿", "谁穿", "什么人", "胯宽", "比例",
)
_VALUE_MARKERS = _FIT_MARKERS + _FABRIC_MARKERS + _AUDIENCE_MARKERS + (
    "细节", "蕾丝", "刺绣", "拼接", "对比", "舒服", "好穿",
)


def _is_control(text: str) -> bool:
    t = text or ""
    if any(x in t for x in _CONTROL_MARKERS):
        return True
    # 挂车/加单也当控场硬删（与成交过滤双保险）
    if _is_link_or_slot_talk(t) or _is_deal_call(t):
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


def _is_onbody_effect(text: str) -> bool:
    """Wearing-effect lines users want kept (not size chart)."""
    t = text or ""
    return any(k in t for k in _ONBODY_EFFECT_MARKERS)


def _is_size(text: str) -> bool:
    """Hard ban size-advice / chart / letter-size talk from all cut paths.

    Keep on-body effect talk (不走光/不显肚子/拜拜肉/胃包) — those are fit selling points.
    """
    t = text or ""
    if not t:
        return False
    # Explicit allow: clothing wearing effects first
    if _is_onbody_effect(t) and not re.search(
        r"(码|碼|號|号|M码|L码|S码|XL|XXL|建议穿|該穿|该穿|几斤|幾斤|\d+\s*斤)", t, flags=re.I
    ):
        # e.g. 不显肚子、防走光 — not size
        return False
    if any(x in t for x in _SIZE_MARKERS):
        return True
    # 胸大/胸小 + 选码语境才删；纯「不显胸」上身效果不走这条
    if re.search(r"胸\s*(大|小)", t) and any(
        k in t for k in ("码", "碼", "号", "號", "罩杯", "卡", "网袋", "推荐来", "来三", "穿")
    ):
        return True
    # 字母码：M/L/S/XL + 码/穿/号
    if re.search(r"(?<![A-Za-z0-9])(XS|S|M|L|XL|XXL|2XL|3XL)(?![A-Za-z0-9])", t, flags=re.I):
        if re.search(r"(码|碼|號|号)", t) or re.search(
            r"(穿|选|選|要|拍|加|来|來)\s*(XS|S|M|L|XL|XXL)", t, flags=re.I
        ):
            return True
        if re.search(r"(XS|S|M|L|XL|XXL)\s*(码|碼|號|号|/)", t, flags=re.I):
            return True
    # 「100斤 / 110 斤穿」体重选码
    if re.search(r"\d+\s*(斤|公斤|kg)", t, flags=re.I) and any(
        k in t for k in ("穿", "码", "碼", "号", "號", "建议", "建議", "推荐", "推薦", "适合穿", "適合穿")
    ):
        return True
    # 「大一码 / 小一码 / 正常码」真报码
    if re.search(r"(偏?[大小]|正常)\s*一?\s*(码|碼|號|号)", t):
        return True
    # 「来个 M / 拍 L」
    if re.search(r"(来|來|拍|加|选|選|要)\s*[一个個]?\s*(XS|S|M|L|XL|XXL)\b", t, flags=re.I):
        return True
    return False


def _is_persona_or_hype(text: str) -> bool:
    """品牌人设/情绪灌鸡汤，无服装信息。"""
    t = text or ""
    if any(
        k in t
        for k in (
            "定义我的标签",
            "甄姐的标签",
            "标签不是随意",
            "摸不着拆不透",
            "不要随便定义",
            "随便定义我",
        )
    ):
        return True
    # 纯人设：有“标签”但没有版型/面料/穿着信息
    if "标签" in t and not any(
        k in t for k in ("面料", "版型", "显瘦", "上身", "遮肉", "适合", "垂感", "不透", "收腰")
    ):
        return True
    return False


def _is_policy_risk(text: str) -> bool:
    """Douyin-risk hype / medical claims / off-platform diversion."""
    t = text or ""
    if any(k in t for k in _POLICY_RISK_MARKERS):
        return True
    # “第X”绝对排名
    if re.search(r"第\s*[一二三1-3]\s*(名|名品牌|品牌)?", t) and any(
        k in t for k in ("全国", "全网", "行业", "销量", "品质")
    ):
        return True
    # wx / QQ 引流变体
    if re.search(r"(加|加下|加我).{0,4}(微信|vx|v信|薇信|威信|扣扣|qq|QQ)", t, flags=re.I):
        return True
    return False


def _is_link_or_slot_talk(text: str) -> bool:
    """挂车链接 / n号链接 / 点链接 等直播入口话术（短视频一律剔除）。"""
    t = text or ""
    if not t:
        return False
    if any(
        k in t
        for k in (
            "链接", "鏈接", "小黄车", "小黃車", "购物车", "購物車",
            "加购", "加購", "弹窗", "彈窗", "挂车", "挂上车",
        )
    ):
        return True
    # n号 / 几号 / 1号链接 / 二号 / N号（挂车位）
    if re.search(
        r"(?:[0-9０-９一二三四五六七八九十两俩几幾nN]\s*)+(?:号|號)\s*(?:链接|鏈接|小黄车|小黃車|购物车|購物車|位|窗)?",
        t,
    ):
        return True
    # 上/点/戳/拍/放/开 链接
    if re.search(r"(上|点|點|戳|拍|放|挂|掛|开|開|给|給|看|有)\s*(?:个|個)?\s*(链接|鏈接)", t):
        return True
    # ASR: “连接/连结/ Lianjie” 误识别（与购物语境）
    if re.search(r"(连接|連結|连结)", t) and any(
        k in t for k in ("号", "號", "点", "點", "上", "拍", "加", "车", "車", "左", "右", "下角")
    ):
        return True
    # 左/右 角 + 链接/车
    if re.search(r"(左|右|上|下)\s*(上|下)?\s*角", t) and any(
        k in t for k in ("链接", "鏈接", "车", "車", "拍", "点", "點", "加")
    ):
        return True
    return False


def _is_deal_call(text: str) -> bool:
    """直播加单 / 催拍口令（含 ASR 变体）。"""
    t = text or ""
    if not t:
        return False
    # 加/拍/下/锁 + 一/两/几 + 单/波/件
    if re.search(
        r"(加|拍|下|锁|鎖|来|來)\s*(?:个|個|了)?\s*(一|1|两|倆|俩|二|几|幾|俩)\s*(?:个|個)?\s*(单|單|波|件|单子|單子)",
        t,
    ):
        return True
    # 再加/再拍/赶紧加/闭眼加
    if re.search(
        r"(再|赶紧|赶快|抓紧|全部|一起|全场|全場|有货|想要|喜欢|喜歡|看上|闭眼|閉眼|马上|趕緊)\s*.{0,6}(加|拍|下单|下單|锁|鎖)",
        t,
    ):
        return True
    # 加购/上车/加车
    if re.search(r"(加购|加購|加车|加車|上车|上車|挂车|掛車)", t):
        return True
    # 「拍下 / 下单了吗 / 库存告急」
    if re.search(r"(拍下|下单|下單|锁单|鎖單|库存告急|手慢无|秒了|秒它)", t):
        return True
    # 纯「加单」「加单了」
    if re.search(r"(加\s*单|加\s*單|拍\s*单|拍\s*單)", t):
        return True
    return False


def _is_price_or_shipping(text: str) -> bool:
    t = text or ""
    if any(x in t for x in _PRICE_SHIP_MARKERS):
        return True
    # 链接 / n号 / 挂车入口（强化）
    if _is_link_or_slot_talk(t):
        return True
    # 直播加单口令（强化）
    if _is_deal_call(t):
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


def _learned_delta(text: str, *, for_hook: bool = False) -> float:
    """Safe wrapper: reverse-cut learning score for local + cloud candidate ranking."""
    try:
        return float(learned_text_score(text or "", for_hook=for_hook) or 0.0)
    except Exception:
        return 0.0


def _value_score(text: str, *, for_hook: bool = False) -> float:
    """Base clothing value + human reverse-cut preferences (all planning paths)."""
    t = text or ""
    s = 0.0
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
    if (
        _is_control(t) or _is_size(t) or _is_price_or_shipping(t) or _is_persona_or_hype(t) or _is_policy_risk(t)
        or _is_policy_risk(t)
    ):
        s -= 80
    if len(t) < 2:
        s -= 20
    # Human-in-the-loop: keep / drop / hook preferences from 保存并重剪
    learn = _learned_delta(t, for_hook=for_hook)
    # Scale so typical learn |x|<5 becomes meaningful but cannot override hard bans
    if learn > 0:
        s += min(12.0, learn * 1.6)
    elif learn < 0:
        s += max(-18.0, learn * 2.0)
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
        if _is_persona_or_hype(text):
            stats["dropped_control"] += 1
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

    # score and take best (base value + reverse-cut learning), preserve chrono of chosen set
    ranked = sorted(
        kept,
        key=lambda c: (
            _value_score(str(c.get("text") or "")),
            _learned_delta(str(c.get("text") or ""), for_hook=True),
        ),
        reverse=True,
    )
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
            if (
                _is_control(text) or _is_size(text) or _is_price_or_shipping(text) or _is_persona_or_hype(text) or _is_policy_risk(text)
            ):
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
        # prefer human reverse-cut tops when available
        keep = list(st.get("top_keep") or st.get("top_hook") or [])[:limit]
        hook = list(st.get("top_hook") or [])[:limit]
        drop = list(st.get("top_drop") or [])[:limit]
        if not keep and not drop and not hook:
            return {}
        return {"keep": keep, "hook": hook, "drop": drop, "events": int((st.get("stats") or {}).get("events") or 0)}
    except Exception:
        return {}


def _format_learning_prompt_bits(limit: int = 4) -> str:
    """Compact reverse-cut taste line for cloud LLM (≤~120 chars typical)."""
    h = _learning_hints(limit=limit)
    if not h:
        return ""
    bits: list[str] = []
    if h.get("hook"):
        bits.append("开场偏好:" + "/".join(str(x)[:8] for x in h["hook"][:3]))
    if h.get("keep"):
        bits.append("多留:" + "/".join(str(x)[:8] for x in h["keep"][:3]))
    if h.get("drop"):
        bits.append("少留:" + "/".join(str(x)[:8] for x in h["drop"][:3]))
    if not bits:
        return ""
    return "【反剪学习】" + "；".join(bits) + "。在硬删前提下优先贴合这些口味。"


def _build_plan_messages(
    clauses: list[dict[str, Any]],
    *,
    target_seconds: int,
    sp: float,
    target_source_ms: int,
    text_max: int,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Build id-only chat messages + short-id map + compact clause list.

    Intentionally ask the model for keep *ids only* (not full keep objects).
    Full objects were the main source of SiliconFlow read-timeouts and JSON garbage.
    Local code expands ids → clauses and runs coverage/duration repair.
    """
    del sp, target_source_ms  # kept in signature for call-site compatibility
    id_map: dict[str, str] = {}
    compact: list[dict[str, Any]] = []
    lines: list[str] = []
    for u in clauses:
        full = str(u["id"])
        m = re.search(r"(\d+)$", full)
        short = f"c{int(m.group(1))}" if m else full
        if short in id_map and id_map[short] != full:
            short = full
        id_map[short] = full
        text = str(u["text"])[:text_max]
        compact.append({"id": short, "text": text})
        lines.append(f"{short}|{text}")
    learn_bits = _format_learning_prompt_bits(limit=4)
    # Line format beats nested JSON for small models (less echo / faster).
    # Hard DROP rules stay first; narrative order is additive optimization.
    user_text = (
        f"目标约{int(target_seconds)}s。从候选选保留id并按播放顺序排列（约8-16个）。"
        "【硬删·绝对禁止】任何尺码内容：M/L/S/XL、偏大偏小、胸围腰围、建议穿、几斤穿、什么码；"
        "价格拨分包邮、发货物流、加一单/拍一单催单、n号链接/小黄车/点链接、直播控场(家人们/扣1/321)、人设鸡汤、无关闲聊。"
        "【结构】开头1句服装特点（版型/上身/面料）→整块全身效果→整块面料体感→细节做工→穿搭场景→收束。"
        "【同主题连排·强制】同一卖点/主题的小句必须相邻成块，禁止「面料→版型→又面料→又版型」来回跳；"
        "例如先把显瘦/收腰讲完，再整块讲软/垂/不透，勿中途穿插别的模块。"
        "禁止选尺码句与任何加单/链接句，即使候选里有也不要。"
        + (learn_bits if learn_bits else "")
        + "\n候选:\n"
        + "\n".join(lines)
        + '\n只输出JSON:{"ids":["c2","c3"],"hook":"visual"}'
    )
    system = (
        "你是服装短视频剪辑助手。只输出一个JSON对象。"
        "ids必须来自候选且原样复制。"
        "硬规则：禁止尺码(字母码/围度/建议穿/几斤)；禁止价格发货、加一单、n号链接、小黄车、直播控场。"
        "顺序：服装特点钩子→全身效果块→面料块→细节→场景；同主题ids必须连排，禁止主题打散穿插。"
        + ("参考用户反剪学习口味，但不突破硬删。" if learn_bits else "")
        + '格式:{"ids":["c2","c3","c4"],"hook":"visual"}'
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
            "timeout": PLAN_TIMEOUT_S_RETRY,
            "label": "retry_light",
        },
    ]

    last_err: Exception | None = None
    out: dict[str, Any] | None = None
    clauses: list[dict[str, Any]] = []
    trim_stats: dict[str, Any] = {}
    id_map: dict[str, str] = {}
    attempt_label = "primary"
    import time as _time

    for idx, att in enumerate(attempts):
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
                temperature=0.0,
                max_tokens=int(att["max_tokens"]),
                force_json=True,
                timeout=int(att["timeout"]),
                cfg=cfg,
                fast=True,  # last_route + few payloads
            )
            attempt_label = str(att["label"])
            last_err = None
            break
        except OpenAICompatError as e:
            last_err = e
            msg = str(e).lower()
            # Only retry on timeout/network-ish failures with lighter payload
            retriable = any(
                k in msg
                for k in ("timeout", "timed out", "10054", "10060", "temporarily", "503", "502", "429")
            )
            if not retriable:
                raise RuntimeError(f"llm_request_failed:{e}") from e
            if idx + 1 < len(attempts):
                _time.sleep(0.8)  # brief backoff before lighter retry
            continue

    if out is None:
        raise RuntimeError(f"llm_request_failed:{last_err}")

    content = out.get("content") or ""
    obj = _extract_json_obj(content)
    obj = _normalize_llm_keep_obj(obj, clauses, id_map)
    obj = _repair_keep_ids(obj, clauses)
    learn_h = _learning_hints(limit=4)
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
        "submit_mode": "stable_ids_only_asr_selected_clauses",
        "attempt": attempt_label,
        "learning_applied": bool(learn_h),
        "learning_hints": learn_h or None,
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


def _resolve_short_id(raw: str, id_map: dict[str, str], by_id: dict[str, dict[str, Any]]) -> str | None:
    """Map model short ids (c2 / C02 / 2) onto canonical clause ids."""
    s = str(raw or "").strip()
    if not s:
        return None
    if s in by_id:
        return s
    if s in id_map and id_map[s] in by_id:
        return id_map[s]
    m = re.search(r"c?0*(\d{1,5})", s, flags=re.I)
    if m:
        short = f"c{int(m.group(1))}"
        if short in id_map and id_map[short] in by_id:
            return id_map[short]
        cand = f"c{int(m.group(1)):05d}"
        if cand in by_id:
            return cand
    if s.isdigit():
        short = f"c{int(s)}"
        if short in id_map and id_map[short] in by_id:
            return id_map[short]
        cand = f"c{int(s):05d}"
        if cand in by_id:
            return cand
    return None


def _normalize_llm_keep_obj(
    obj: dict[str, Any],
    clauses: list[dict[str, Any]],
    id_map: dict[str, str],
) -> dict[str, Any]:
    """Accept ids-only / keep / sel schemas and expand into keep[{id,text,...}]."""
    if not isinstance(obj, dict):
        obj = {}
    by_id = {str(c.get("id")): c for c in clauses}
    ordered_ids: list[str] = []

    def _push(raw: Any) -> None:
        if isinstance(raw, dict):
            raw = raw.get("id") or raw.get("utt_id") or raw.get("cid")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            raw = str(int(raw))
        sid = _resolve_short_id(str(raw or ""), id_map, by_id)
        if sid and sid not in ordered_ids:
            ordered_ids.append(sid)

    # Preferred lightweight schema
    for key in ("ids", "keep_ids", "sel", "selected", "order", "keep_order"):
        arr = obj.get(key)
        if isinstance(arr, list) and arr:
            for x in arr:
                _push(x)
            break

    # Legacy full keep objects / mixed list
    if not ordered_ids:
        keep = obj.get("keep")
        if isinstance(keep, list):
            for item in keep:
                _push(item)

    # Fallback: scan whole JSON text for cN tokens that exist in map
    if not ordered_ids:
        blob = json.dumps(obj, ensure_ascii=False)
        for m in re.finditer(r"\bc0*(\d{1,5})\b", blob, flags=re.I):
            _push(f"c{int(m.group(1))}")

    keep_out: list[dict[str, Any]] = []
    for sid in ordered_ids:
        src = by_id.get(sid)
        if not src:
            continue
        keep_out.append(
            {
                "id": sid,
                "t0_ms": int(src["t0_ms"]),
                "t1_ms": int(src["t1_ms"]),
                "text": str(src.get("text") or ""),
                "why": "llm_id",
                "point": "",
                "complete": True,
            }
        )
    out = dict(obj)
    out["keep"] = keep_out
    if "hook_type" not in out and obj.get("hook"):
        out["hook_type"] = obj.get("hook")
    if "main_points" not in out:
        out["main_points"] = ["版型", "面料", "适用人群"]
    out["_ids_only"] = True
    out["_keep_ids_n"] = len(keep_out)
    return out


def _repair_keep_ids(obj: dict[str, Any], clauses: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Small models often invent broken keep ids (e.g. c0000000...1).
    Remap by text/time; if still empty, build a safe keep from top clothing clauses
    so we don't fall back to pure rules with a "connected" LLM.
    """
    if not isinstance(obj, dict):
        obj = {}
    # Expand compact schemas before repair when caller skipped normalize
    if not isinstance(obj.get("keep"), list) or not obj.get("keep"):
        if any(k in obj for k in ("ids", "keep_ids", "sel", "selected")):
            obj = _normalize_llm_keep_obj(obj, clauses, {})
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
            # short form c2 when clauses use c00002
            short = f"c{int(m.group(1))}"
            for kid, c in by_id.items():
                mm = re.search(r"(\d+)$", kid)
                if mm and int(mm.group(1)) == int(m.group(1)):
                    return c
            del short
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
            # bare id string inside keep
            if isinstance(item, str) or isinstance(item, (int, float)):
                item = {"id": item}
            else:
                continue
        src = match_clause(item)
        if not src:
            continue
        sid = str(src.get("id"))
        if sid in used:
            continue
        _tx = str(src.get("text") or "")
        if (
            _is_price_or_shipping(_tx)
            or _is_size(_tx)
            or _is_control(_tx)
            or _is_persona_or_hype(_tx)
            or _is_policy_risk(_tx)
        ):
            continue
        used.add(sid)
        fixed.append(
            {
                "id": sid,
                "t0_ms": int(src["t0_ms"]),
                "t1_ms": int(src["t1_ms"]),
                "text": _tx,
                "why": str(item.get("why") or "remap_keep"),
                "point": str(item.get("point") or ""),
                "complete": True,
            }
        )

    repaired = False

    def _bucket(tx: str) -> str:
        """Narrative buckets for reorder: body > craft > scene."""
        t = tx or ""
        # Detail/craft markers (before pure fabric so 蕾丝/拼接归细节做工)
        craft_markers = (
            "细节", "蕾丝", "刺绣", "拼接", "走线", "扣子", "拉链", "领口", "腰线",
            "肩线", "肌理", "做工", "车线", "滚边",
        )
        if any(k in t for k in craft_markers):
            return "craft"
        if any(k in t for k in _FIT_MARKERS) or any(
            k in t for k in ("上身", "显瘦", "全身", "遮肉", "比例", "版型", "收腰", "修身")
        ):
            return "body"
        if any(k in t for k in _FABRIC_MARKERS):
            # Fabric can open (特写) or sit in craft; body reorder treats fabric as craft stage
            return "fabric"
        if any(k in t for k in _AUDIENCE_MARKERS) or any(
            k in t for k in ("通勤", "日常", "上班", "夏天", "秋冬", "场景", "搭配", "好搭")
        ):
            return "scene"
        return "other"

    def _hook_attract_score(tx: str) -> float:
        """Higher = better opener: put clothing product features first."""
        t = tx or ""
        if (
            _is_control(t) or _is_size(t) or _is_price_or_shipping(t) or _is_persona_or_hype(t) or _is_policy_risk(t)
        ):
            return -100.0
        s = 0.0
        # Highest: concrete clothing features (what the garment is good at)
        if any(k in t for k in ("版型", "面料", "布料", "材质", "显瘦", "收腰", "遮肉", "修身", "上身")):
            s += 56.0
        # On-body effect: 不走光/不显肚子/拜拜肉/胃包 — strong openers
        if _is_onbody_effect(t):
            s += 54.0
        if any(k in t for k in ("超软", "软", "垂感", "不透", "凉感", "冰丝", "亲肤", "透气", "不起球", "抗皱")):
            s += 48.0
        if any(k in t for k in ("细节", "蕾丝", "拼接", "做工", "走线", "领口", "腰线")):
            s += 36.0
        if any(k in t for k in ("全身", "比例", "高腰", "对比", "两色", "穿上就")):
            s += 22.0
        # base value already embeds reverse-cut learning; avoid double-count blow-up
        s += min(24.0, float(_value_score(t, for_hook=False)) * 2.0)
        s += min(18.0, max(-12.0, _learned_delta(t, for_hook=True) * 2.4))
        # Scene/audience alone is weaker as first line
        if any(k in t for k in ("适合", "通勤", "日常", "小个子", "梨形", "微胖")) and s < 40:
            s += 6.0
        return s

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
        if (
            not tx
            or _is_control(tx) or _is_size(tx) or _is_price_or_shipping(tx) or _is_persona_or_hype(tx) or _is_policy_risk(tx)
        ):
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

    # 1) Force-cover body / fabric-craft / scene whenever ASR has them
    for need, markers, point in (
        ("fit", _FIT_MARKERS, "全身效果"),
        ("fabric", _FABRIC_MARKERS, "细节做工"),
        ("audience", _AUDIENCE_MARKERS, "穿搭场景"),
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
            and not _is_persona_or_hype(str(c.get("text") or ""))
            and not _is_policy_risk(str(c.get("text") or ""))
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
            if b in ("body", "fit") and not cov["fit"]:
                gap = 3
            elif b in ("fabric", "craft") and not cov["fabric"]:
                gap = 3
            elif b in ("scene", "audience") and not cov["audience"]:
                gap = 3
            # value already includes reverse-cut learning
            return (gap, _value_score(tx), _learned_delta(tx))

        ranked = sorted(clauses, key=rank_key, reverse=True)
        for c in ranked:
            if len(fixed) >= max(min_n, 24) and _total_ms() >= target_ms:
                break
            tx = str(c.get("text") or "")
            if _value_score(tx) < 2 and _bucket(tx) == "other":
                continue
            b = _bucket(tx)
            point = {
                "body": "全身效果",
                "fabric": "细节做工",
                "craft": "细节做工",
                "scene": "穿搭场景",
            }.get(b, "卖点")
            _add_clause(c, why="duration_fill", point=point)

    # Always top-up hard toward ~60s final (source ≈ 70–90s @1.4x).
    # Floor: final >= 50s → source >= ~70s @1.4x.
    _fill_duration(min_n=12, target_ms=90_000)
    # Second pass: if under 50s-final source floor, accept medium-value clothing lines
    if _total_ms() < 70_000:
        for c in sorted(clauses, key=lambda x: int(x.get("t0_ms") or 0)):
            if _total_ms() >= 88_000 or len(fixed) >= 28:
                break
            tx = str(c.get("text") or "")
            if _value_score(tx) < 1:
                continue
            if any(k in tx for k in ("面料", "版型", "显瘦", "上身", "适合", "软", "垂", "透气", "遮肉", "穿")):
                b = _bucket(tx)
                point = {
                    "body": "全身效果",
                    "fabric": "细节做工",
                    "craft": "细节做工",
                    "scene": "穿搭场景",
                }.get(b, "卖点")
                _add_clause(c, why="duration_fill_2", point=point)

    # Narrative order (not source chronology):
    # clothing features first (best product points) → body → craft/fabric → scene
    if fixed:
        # Put top 1–2 feature lines first, then remaining by stage
        ranked_idx = sorted(
            range(len(fixed)),
            key=lambda i: -_hook_attract_score(str(fixed[i].get("text") or "")),
        )
        openers: list[dict[str, Any]] = []
        used_open: set[int] = set()
        for i in ranked_idx:
            if len(openers) >= 2:
                break
            tx = str(fixed[i].get("text") or "")
            if _hook_attract_score(tx) < 30:
                break
            item = dict(fixed[i])
            item["why"] = "open_clothing_feature"
            if not item.get("point"):
                item["point"] = "服装特点"
            openers.append(item)
            used_open.add(i)
        rest_src = [fixed[i] for i in range(len(fixed)) if i not in used_open]
        # After feature openers: more body/fit, then craft, then scene
        stage_rank = {"body": 0, "fit": 0, "fabric": 1, "craft": 1, "scene": 2, "audience": 2, "other": 3}
        rest = sorted(
            rest_src,
            key=lambda x: (
                stage_rank.get(_bucket(str(x.get("text") or "")), 3),
                int(x.get("t0_ms") or 0),
                -_value_score(str(x.get("text") or "")),
            ),
        )
        # Within each stage already contiguous; further force topic blocks so
        # fabric/fit never interleave when stage mapping is ambiguous.
        fixed_list = [*openers, *rest] if openers else rest
        try:
            pseudo_slots = [
                PlanSlot(
                    clip_id=str(x.get("id") or f"k{i}"),
                    role="story",
                    t0_ms=int(x.get("t0_ms") or 0),
                    t1_ms=int(x.get("t1_ms") or 0),
                    text=str(x.get("text") or ""),
                    score=float(_value_score(str(x.get("text") or ""))),
                )
                for i, x in enumerate(fixed_list)
            ]
            clustered = _cluster_slots_by_topic(pseudo_slots)
            by_cid = {str(x.get("id") or f"k{i}"): x for i, x in enumerate(fixed_list)}
            fixed = []
            for s in clustered:
                item = by_cid.get(s.clip_id)
                if item is not None:
                    fixed.append(item)
            if len(fixed) != len(fixed_list):
                fixed = fixed_list
        except Exception:
            fixed = fixed_list
    obj["keep"] = fixed
    cov_final = _coverage()
    obj["_coverage"] = cov_final
    obj["_narrative"] = "clothing_features_first"
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


def _topic_of_text(text: str) -> str:
    """Coarse topic bucket for clustering related clauses together.

    Priority for mixed lines (e.g. "收腰面料软"):
      detail craft markers → fit/on-body → fabric feel → audience → close → other
    Detail first so 蕾丝/拼接 don't get swallowed into fabric just because of "软".
    """
    t = text or ""
    # Craft/detail first (more specific product modules)
    if any(
        k in t
        for k in (
            "蕾丝", "拼接", "走线", "工艺", "细节", "口袋", "领口", "袖口",
            "开叉", "纽扣", "车线", "花边", "刺绣", "滚边", "肌理",
        )
    ):
        return "detail"
    # On-body / fit block
    if _is_onbody_effect(t) or any(
        k in t
        for k in (
            "显瘦", "收腰", "修身", "遮肉", "遮肚子", "遮胯", "肩线", "腰线",
            "版型", "长短", "上身", "比例", "显腿", "不挑人", "包裹", "高腰",
            "全身",
        )
    ):
        return "fit"
    # Fabric / hand-feel block
    if any(
        k in t
        for k in (
            "面料", "布料", "材质", "手感", "垂感", "透气", "不透", "凉感",
            "亲肤", "柔软", "软", "冰凉", "抗皱", "不起球", "闷",
            "醋酸", "天丝", "雪纺", "纯棉", "罗马布", "针织", "冰丝", "干爽",
        )
    ):
        return "fabric"
    # Audience / scene
    if any(
        k in t
        for k in (
            "适合", "微胖", "梨形", "小个子", "通勤", "日常", "夏天", "上班",
            "场景", "季节", "人群", "谁穿", "年龄", "搭配", "好搭", "显白",
        )
    ):
        return "audience"
    # Soft closing recap only when not a concrete feature line
    if any(k in t for k in ("推荐", "闭眼入", "真的好穿", "必须安排", "就这些", "就这样")):
        return "close"
    return "other"


def _cluster_slots_by_topic(slots: list[PlanSlot]) -> list[PlanSlot]:
    """
    Reorder story slots so same-topic clauses form contiguous blocks.

    Macro order (fixed product narrative):
      hook(other/first) stays first if present → fit → fabric → detail → audience → close → other
    Within a topic, keep relative order by original source time (t0_ms),
    so "one module" sounds continuous rather than theme-hopping.
    """
    if not slots or len(slots) <= 2:
        return slots

    # Preserve first hook-like slot position: the current first item is treated as opener.
    opener = slots[0]
    rest = list(slots[1:])

    # group remaining by topic, preserve first-seen order of topics with priority
    topic_priority = {
        "fit": 0,
        "fabric": 1,
        "detail": 2,
        "audience": 3,
        "close": 4,
        "other": 5,
    }
    buckets: dict[str, list[PlanSlot]] = {k: [] for k in topic_priority}
    for s in rest:
        buckets[_topic_of_text(s.text or "")].append(s)

    for k in buckets:
        buckets[k].sort(key=lambda s: (int(s.t0_ms or 0), int(s.t1_ms or 0)))

    # If opener itself is fit/fabric, still keep it first (hook), remaining of same topic follow.
    out: list[PlanSlot] = [opener]
    opener_topic = _topic_of_text(opener.text or "")
    if opener_topic in buckets and buckets[opener_topic]:
        out.extend(buckets[opener_topic])
        buckets[opener_topic] = []

    for topic, _prio in sorted(topic_priority.items(), key=lambda kv: kv[1]):
        if buckets.get(topic):
            out.extend(buckets[topic])

    # safety: no drop
    if len(out) != len(slots):
        return slots
    return out


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
    # Prefer selected clauses for keep resolve; full raw pool for duration fill.
    clause_units = llm_obj.get("_clauses")
    if not isinstance(clause_units, list) or not clause_units:
        clause_units = expand_lines_to_clauses(lines)
    raw_units = llm_obj.get("_clauses_raw")
    if not isinstance(raw_units, list) or not raw_units:
        raw_units = expand_lines_to_clauses(lines, max_clauses=420) if lines else list(clause_units)
    by_id = {str(u.get("utt_id") or u.get("id")): u for u in clause_units}
    # Merge raw into by_id so fill can append ids not in the light subset
    for u in raw_units:
        if not isinstance(u, dict):
            continue
        uid = str(u.get("utt_id") or u.get("id") or "")
        if uid and uid not in by_id:
            by_id[uid] = u
    # ordered list for neighbor completion / duration fill (prefer raw chronology)
    ordered = list(raw_units) if raw_units else list(clause_units)
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
        # hard drop size / price / shipping / live-control / persona even if LLM kept them
        if (
            _is_size(text)
            or _is_price_or_shipping(text)
            or _is_control(text)
            or _is_persona_or_hype(text)
            or _is_policy_risk(text)
        ):
            return
        if any(x in text for x in ("加购", "小黄车", "上链接", "点链接")):
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
    # Product floor: final >= 50s → source >= 50s * playback_speed (+tiny join slack)
    floor_src_ms = int(round(50_000 * sp * 1.02))
    min_ms = max(floor_src_ms, int(aim * 0.90))
    max_ms = int(aim * 1.15)

    def total_ms() -> int:
        return sum(max(0, s.t1_ms - s.t0_ms) for s in slots)

    def _banned_content(tx: str) -> bool:
        """Hard product bans — never use for duration fill or path padding."""
        t = tx or ""
        return (
            _is_control(t) or _is_size(t) or _is_price_or_shipping(t) or _is_persona_or_hype(t) or _is_policy_risk(t)
        )

    def _fillable_tx(tx: str, *, relax: bool) -> bool:
        """Only real clothing sell lines may pad duration. Never live/price/size."""
        if not tx or len(tx.strip()) < 2:
            return False
        if _banned_content(tx):
            return False
        # Positive clothing signal required (even when relaxing score threshold)
        clothing_hit = any(
            k in tx
            for k in (
                *_FIT_MARKERS,
                *_FABRIC_MARKERS,
                *_AUDIENCE_MARKERS,
                "上身", "穿上", "显瘦", "遮肉", "面料", "布料", "版型", "垂感",
                "透气", "不透", "亲肤", "软", "凉感", "细节", "蕾丝", "拼接",
                "通勤", "日常", "适合", "好看", "好穿", "舒服", "气质",
            )
        )
        if not clothing_hit:
            return False
        if relax:
            # still clothing-only; just allow slightly weaker scores
            return _value_score(tx) >= 1
        return _value_score(tx) >= 2

    # Proactive fill BEFORE trim — must clear 50s-final floor when material exists
    for relax in (False, True):
        guard_f = 0
        while total_ms() < min_ms and guard_f < 80:
            guard_f += 1
            added = False
            # prefer higher value first, then chronological
            cands = sorted(
                ordered,
                key=lambda u: (
                    -_value_score(str(u.get("text") or "")),
                    -_learned_delta(str(u.get("text") or "")),
                    int(u.get("t0_ms") or 0),
                ),
            )
            for u in cands:
                uid = str(u.get("id") or u.get("utt_id") or "")
                if not uid or uid in used_ids:
                    continue
                tx = str(u.get("text") or "")
                if not _fillable_tx(tx, relax=relax):
                    continue
                before = total_ms()
                _append_from_src(uid, u, why="duration_floor_fill", score=32 if not relax else 20)
                if total_ms() > before:
                    added = True
                    break
            if not added:
                break

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
    # Only same-topic: never glue 面料+版型 into one card (kills topic clustering).
    merged: list[PlanSlot] = []
    for s in slots:
        if not merged:
            merged.append(s)
            continue
        prev = merged[-1]
        gap = s.t0_ms - prev.t1_ms
        same_topic = _topic_of_text(prev.text or "") == _topic_of_text(s.text or "")
        if (
            same_topic
            and 0 <= gap <= 500
            and (prev.t1_ms - prev.t0_ms) + (s.t1_ms - s.t0_ms) <= 10000
        ):
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
        "policy:topic_blocks_together",
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
    # Second fill pass after merge/drop incomplete — hard push to >=50s final source
    for relax in (False, True):
        guard = 0
        while total_ms() < min_ms and guard < 80:
            guard += 1
            added = False
            cands = sorted(
                ordered,
                key=lambda u: (
                    -_value_score(str(u.get("text") or "")),
                    -_learned_delta(str(u.get("text") or "")),
                    int(u.get("t0_ms") or 0),
                ),
            )
            for u in cands:
                uid = str(u.get("id") or u.get("utt_id") or "")
                if not uid or uid in used_ids:
                    continue
                tx = str(u.get("text") or "")
                if not _fillable_tx(tx, relax=relax):
                    continue
                before = total_ms()
                _append_from_src(uid, u, why="timeline_duration_fill", score=30 if not relax else 18)
                if total_ms() > before:
                    added = True
                    break
            if not added:
                break
    final_ms = total_ms()
    final_out_s = final_ms / 1000.0 / sp if sp > 0 else final_ms / 1000.0
    if final_out_s + 0.05 < 50.0:
        warnings.append(f"short_under_50s_final={final_out_s:.1f}")
        warnings.append(f"short_but_complete_ms={final_ms}")
    if llm_obj.get("_coverage"):
        warnings.append(f"coverage:{llm_obj.get('_coverage')}")

    # final guard: never end with incomplete text (if still short, prefer keep length)
    if slots and _looks_incomplete_text(slots[-1].text) and len(slots) > 1:
        if total_ms() - max(0, slots[-1].t1_ms - slots[-1].t0_ms) >= min_ms:
            slots.pop()
            warnings.append("dropped_incomplete_tail")
        else:
            warnings.append("kept_incomplete_tail_for_duration")

    # Final safety net: strip any residual size/price/live lines before publish plan
    cleaned: list[PlanSlot] = []
    dropped_size_n = 0
    for s in slots:
        tx = str(s.text or "")
        if _is_size(tx) or _is_price_or_shipping(tx) or _is_control(tx) or _is_persona_or_hype(tx) or _is_policy_risk(tx):
            if _is_size(tx):
                dropped_size_n += 1
            continue
        cleaned.append(s)
    if dropped_size_n:
        warnings.append(f"policy:size_stripped_n={dropped_size_n}")
    slots = cleaned

    # MUST run last: duration fill / strip above can re-scatter topics.
    # Bundle same-topic clauses so one selling point is never interleaved.
    slots = _cluster_slots_by_topic(slots)
    warnings.append("policy:reverse_cut_learning")

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
    learn_h = _learning_hints(limit=4)
    obj["_meta"] = {
        "model": "local_clause_rank",
        "submit_mode": "local_after_llm_fail",
        "target_source_ms": int(round(target_seconds * 1000 * sp)),
        "input_clauses_raw": trim_stats.get("clauses_raw"),
        "clauses_sent": trim_stats.get("clauses_sent"),
        "trim_stats": trim_stats,
        "narrative": "clothing_features_first",
        "learning_applied": True,  # local path always ranks via _value_score(+learn)
        "learning_hints": learn_h or None,
    }
    plan = llm_obj_to_timeline(
        obj,
        lines,
        target_seconds=target_seconds,
        playback_speed=sp,
    )
    if not plan.golden:
        raise RuntimeError("local_clause_plan_empty")
    plan.warnings = list(plan.warnings or []) + [
        "policy:local_fill_after_llm_fail",
        "policy:reverse_cut_learning",
    ]
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
    1) cloud LLM keep (repaired) when available
    2) local clause fill when cloud fails
    3) rules_duration ONLY when the preferred path is still under ~40s final
       (too short for publish), and rules actually has more usable duration
    """
    sp = playback_speed if playback_speed and playback_speed > 0 else 1.4
    aim_src = int(round(target_seconds * 1000 * sp))  # ~84s for 60s@1.4x
    # Soft target around 55–60s final; rules fallback only triggers under 40s final
    min_ok_src = max(int(round(40_000 * sp)), int(aim_src * 0.70))
    rules_only_if_final_under_s = 40.0

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
                if isinstance(obj.get("_clauses_raw"), list):
                    obj2["_clauses_raw"] = obj.get("_clauses_raw")
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

    # Rules plan only used if primary path is critically short (<40s final)
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

    def _final_s(p: TimelinePlan | None) -> float:
        if p is None:
            return 0.0
        return float(p.total_duration_ms or 0) / 1000.0 / sp

    def _score(p: TimelinePlan | None) -> tuple:
        if p is None or not p.golden:
            return (-1, -1, -1, -1)
        cov = _cov(p)
        cov_n = int(cov["fit"]) + int(cov["fabric"]) + int(cov["audience"])
        dur = int(p.total_duration_ms or 0)
        # Prefer coverage + closer-to-target duration; no hard 50s path-switch
        dur_score = min(dur, aim_src) - max(0, min_ok_src - dur)
        return (cov_n, dur_score, len(p.golden), dur)

    candidates: list[tuple[str, TimelinePlan, dict[str, Any]]] = []
    if plan is not None and plan.golden:
        cloud_obj = dict(obj)
        cmeta = dict(cloud_obj.get("_meta") or {})
        cmeta["cloud_error"] = None
        cmeta["model"] = cmeta.get("model") or "cloud_llm"
        cloud_obj["_meta"] = cmeta
        candidates.append(("cloud_or_repaired", plan, cloud_obj))
    if local_plan.golden:
        lo = dict(local_obj)
        meta = dict(lo.get("_meta") or {})
        meta["cloud_error"] = cloud_err
        lo["_meta"] = meta
        candidates.append(("local_clause_rank", local_plan, lo))

    # Prefer cloud > local first (quality path), ignore rules for now
    primary: list[tuple[str, TimelinePlan, dict[str, Any]]] = [
        c for c in candidates if c[0] in {"cloud_or_repaired", "local_clause_rank"}
    ]
    if primary:
        def _primary_pick(item: tuple[str, TimelinePlan, dict[str, Any]]) -> tuple:
            name, p, _o = item
            base = _score(p)
            # Always prefer cloud when present and non-empty
            path_bonus = 3 if name == "cloud_or_repaired" else 1
            return (*base, path_bonus)

        best_name, best_plan, best_obj = max(primary, key=_primary_pick)
    elif candidates:
        best_name, best_plan, best_obj = max(candidates, key=lambda x: _score(x[1]))
    else:
        best_name = best_plan = best_obj = None  # type: ignore[assignment]

    # Rules only when preferred path is critically short (<40s final)
    if (
        rules_plan is not None
        and rules_plan.golden
        and (
            best_plan is None
            or not best_plan.golden
            or _final_s(best_plan) + 0.05 < rules_only_if_final_under_s
        )
    ):
        rules_candidate = (
            "rules_duration",
            rules_plan,
            {
                "product_summary": "规则时长兜底",
                "main_points": ["版型", "面料", "适用人群"],
                "keep": [],
                "_meta": {
                    "model": "rules_duration_fallback",
                    "submit_mode": "rules_only_if_under_40s",
                    "cloud_error": cloud_err,
                    "rules_trigger_final_s": round(_final_s(best_plan), 2) if best_plan else None,
                },
            },
        )
        candidates.append(rules_candidate)
        if best_plan is None or not best_plan.golden:
            best_name, best_plan, best_obj = rules_candidate
        else:
            # Take rules only if it is meaningfully longer
            if int(rules_plan.total_duration_ms or 0) > int(best_plan.total_duration_ms or 0) + 2000:
                best_name, best_plan, best_obj = rules_candidate
            # else keep cloud/local even if short material is limited

    if best_plan is None or not best_plan.golden:
        raise RuntimeError(cloud_err or "llm_plan_has_no_slots")

    meta = dict(best_obj.get("_meta") or {})
    meta["chosen_path"] = best_name
    meta["cloud_error"] = cloud_err
    meta["rules_threshold_final_s"] = rules_only_if_final_under_s
    meta["candidate_scores"] = {
        name: {
            "slots": len(p.golden or []),
            "ms": int(p.total_duration_ms or 0),
            "final_s": round(_final_s(p), 2),
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
    if best_name == "rules_duration":
        best_plan.warnings.append("policy:rules_only_if_under_40s")
    return best_plan, best_obj
