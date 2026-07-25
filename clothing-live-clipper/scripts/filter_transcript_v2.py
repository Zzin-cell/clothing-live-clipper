"""Clothing-ONLY transcript filter. Main product = 衣服/服装."""
from __future__ import annotations

import re
from typing import Any

SIZE_WORDS = (
    "尺码", "选码", "偏大", "偏小", "胸围", "腰围", "臀围", "身高", "穿M", "穿S",
    "穿L", "穿XL", "穿XXL", "均码", "加大码", "码数", "建议穿", "斤穿",
    "体重", "肩宽", "袖长", "衣长", "裤长", "试码", "报尺码", "该穿", "能穿",
    "S码", "M码", "L码", "XL码", "XXL码", "选码表",
)
SENTIMENT_WORDS = (
    "做了五年", "不容易", "感谢陪伴", "创业", "初心", "故事是这样", "一路走来",
    "谢谢支持我", "喜欢我的人",
)
# Livestream-feel markers: remove so cut feels like a product short, not a live room
FILLER_WORDS = (
    "家人们", "老铁们", "宝宝们", "姐妹们", "宝贝们", "友友们",
    "听得到吗", "扣1", "扣一", "扣个1", "点点关注", "双击", "点关注",
    "晚上好啊", "早上好", "下午好", "来了吗", "在吗", "在不在",
    "过一下", "过一遍", "带过", "先过", "往下过", "咱们过", "带大家过",
    "看一看", "说一下", "讲一下", "介绍一下", "跟大家说", "跟你们说",
    "给大家看", "给你们看", "来看一下", "注意看", "看公屏",
    "一会儿", "待会", "等会", "马上", "接下来", "稍等", "等一下",
    "铃铃铃", "有没有人", "刚进来", "欢迎", "欢迎进来", "新进来的",
    "感谢", "谢谢老板", "谢谢姐妹", "谢谢家人们", "谢谢支持",
    "公屏", "弹幕", "刷波", "刷礼物", "刷一波", "扣波",
    "直播间", "今天直播", "在直播", "开播", "下播",
    "连麦", "福袋", "抽奖", "倒计时", "手慢无",
    "左上角", "右上角", "右下角", "点小黄车", "上车", "挂车",
    "一二三上链接", "上链接", "看链接", "点链接", "戳链接",
    "声音可以吗", "卡吗", "卡不卡", "清楚吗", "听清吗",
    # 开场避雷 / 无效过程
    "大家好", "哈喽", "hello", "调试", "对一下", "对焦", "喝口水", "整理一下",
    "卡了", "卡顿", "重进", "刷新",
)
# Non-clothing product / scene noise
OFFTOPIC_WORDS = (
    "零食", "好吃", "水果", "开心果", "坚果", "包装太大", "浪费了", "安利的",
    "烟花", "烟火", "无人机", "音响", "喇叭", "广场", "楼顶", "屋顶",
    "死亡漩涡", "螺旋", "骨头", "愈合", "干扰", "群山",
    "青蛙", "哭泽", "名版人", "名外",
)

# Must look like garment talk
GARMENT_NOUNS = (
    "衣服", "服装", "上衣", "裤子", "裙子", "外套", "内搭", "大衣", "风衣",
    "牛仔裤", "牛仔", "半身裙", "连衣裙", "西装", "针织", "卫衣", "衬衫",
    "T恤", "t恤", "毛衣", "羽绒服", "棉服", "马甲", "开衫",
    "面料", "布料", "材质", "天丝", "醋酸", "雪纺", "纯棉", "羊毛", "混纺",
    "蕾丝", "雷丝", "拼接", "破洞", "洗水", "马洗",
    "领口", "袖口", "下摆", "开叉", "口袋", "拉链", "扣子",
    "版型", "收腰", "修身", "高腰", "宽松", "直筒", "廓形", "oversize",
)

STRONG = (
    "面料", "布料", "材质", "牛仔", "牛仔裤", "显瘦", "遮肉", "收腰", "修身",
    "版型", "不透", "柔软", "超软", "软的", "软到", "蕾丝", "雷丝", "垂感", "弹力",
    "醋酸", "凉感", "雪纺", "纯棉", "洗水", "上衣", "裙子", "外套", "内搭",
    "遮胯", "高腰", "梨形", "闭眼入", "显白", "开叉", "领口", "袖口",
    "破洞", "拼接", "小雷丝", "马洗", "超级软", "天丝", "小破洞",
    "衣服", "服装", "连衣裙", "半身裙", "风衣", "大衣", "衬衫", "毛衣",
    "不爱穿牛仔", "穿牛仔", "下天的面料", "两块的面料",
    # 穿着体验（可保留）
    "舒服", "舒适", "贴肤", "亲肤", "冰冰的", "凉凉的", "不闷", "不闷汗",
    "透气", "凉快", "轻盈", "松弛", "好穿", "穿着舒服", "上身舒服",
    "一整个夏天", "一整天", "不勒肉", "不磨", "软软的", "遮盖",
)

# secondary clothing talk (must still pass is_garment_line)
MEDIUM = (
    "这件", "这套", "这一套", "穿上", "上身", "试穿", "廓形", "宽松", "直筒",
    "耐造", "百搭", "通勤", "口袋", "拉链", "扣子", "下摆", "细节",
    "推荐", "破洞牛", "小破洞牛", "一整套", "搭配牛仔裤", "搭配裙子",
    "白色", "黑色", "颜色", "上身效果", "版型好", "垂感好", "弹力好",
    "打一下牛仔", "穿一下牛仔", "天丝白", "玻璃",  # ASR noise around clothing demos
    "穿着", "体感", "上身感", "质感", "手感",
)

PRICE = (
    "券后", "直播价", "专属价", "秒杀", "包邮", "块钱", "只要", "原价", "到手",
    "20块", "长20", "便宜", "多少钱", "加购", "下单", "小黄车", "购物车",
    "号链接", "弹窗", "福袋", "拍下", "库存", "满减", "凑单",
)


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def is_garment_line(text: str) -> bool:
    """True only if clearly about clothing garments/features."""
    t = text.strip()
    if not t:
        return False
    if _has_any(t, GARMENT_NOUNS) or _has_any(t, STRONG):
        return True
    # 显瘦/遮肉 etc without noun still clothing feature
    if _has_any(
        t,
        (
            "显瘦", "遮肉", "遮胯", "不透", "显白", "闭眼入", "梨形", "百搭", "垂感", "弹力",
            "舒服", "舒适", "贴肤", "亲肤", "透气", "凉快", "不闷", "轻盈", "好穿",
        ),
    ):
        return True
    # ASR often says 牛仔/面料 variants
    if "牛仔" in t or "面料" in t or "雷丝" in t or "蕾丝" in t or "天丝" in t:
        return True
    return False


def _live_room_score(text: str) -> int:
    """Higher = more livestream-room feel (should drop)."""
    t = text.strip()
    n = 0
    for w in FILLER_WORDS:
        if w in t:
            n += 1
    # interactive / room commands
    if re.search(r"(扣|点|刷).{0,2}(1|一|关注|赞)", t):
        n += 2
    if re.search(r"(直播间|家人们|老铁|宝宝们|姐妹们)", t):
        n += 2
    if re.search(r"(有没有人|在不在|来了吗|听得到)", t):
        n += 2
    return n


def classify(text: str) -> str:
    t = text.strip()
    if not t or len(t) < 2:
        return "drop"
    # ASR hallucination spam
    try:
        from asr_enhance import is_garbage_asr_text  # type: ignore
    except Exception:
        try:
            from scripts.asr_enhance import is_garbage_asr_text  # type: ignore
        except Exception:
            is_garbage_asr_text = None  # type: ignore
    if is_garbage_asr_text and is_garbage_asr_text(t):
        return "drop"
    # local quick checks too
    core = re.sub(r"[\s,，、。.!！?？]+", "", t)
    if core and len(set(core)) <= 2 and len(core) >= 8:
        return "drop"
    if core.count("对") >= 8 and core.count("对") / max(1, len(core)) >= 0.5:
        return "drop"

    strong = _has_any(t, STRONG)
    medium = _has_any(t, MEDIUM)
    price = _has_any(t, PRICE)
    size = _has_any(t, SIZE_WORDS)
    sent = _has_any(t, SENTIMENT_WORDS)
    filler = _has_any(t, FILLER_WORDS)
    off = _has_any(t, OFFTOPIC_WORDS)
    garment = is_garment_line(t)
    live = _live_room_score(t)

    # never keep price / size (global product policy)
    if price:
        return "drop"
    if size:
        return "drop"
    # kill pure livestream feel
    if live >= 2 and not strong:
        return "drop"
    if filler and not strong:
        return "drop"
    # non-clothing topics
    if off and not strong:
        return "drop"
    if not garment:
        # pure 好看/白色/客户 等不够
        return "drop"
    if sent and not strong:
        return "drop"
    if ("过一下" in t or "过一遍" in t) and not strong:
        return "drop"
    # even with garment words, heavy room-control still drop
    if live >= 3:
        return "drop"
    # pure try-on without any garment noun already dropped by is_garment_line
    # keep 牛仔/面料 demo lines as medium/strong for clothing continuity
    # 只有「好看」类形容词，没有衣服词
    if re.fullmatch(r"[好看太超很也挺真的,，。！!？?\s]+", t):
        return "drop"
    if t in {"好看", "太好看了", "超好看", "你好看", "也好看", "又好看"}:
        return "drop"

    if strong:
        return "strong"
    if medium and garment:
        return "medium"
    if garment:
        return "medium"
    return "drop"


def _dur(u: dict) -> int:
    return max(0, int(u.get("t1_ms", 0)) - int(u.get("t0_ms", 0)))


def merge_nearby(items: list[dict], *, max_gap_ms: int = 1200, max_span_ms: int = 12000) -> list[dict]:
    """
    Merge nearby crumbs into natural modules.
    Prefer merging when previous text looks unfinished (no sentence end),
    avoid creating one mega-block.
    """
    if not items:
        return []
    items = sorted(items, key=lambda u: int(u.get("t0_ms", 0)))
    out: list[dict] = []
    cur = dict(items[0])
    texts = [str(cur.get("text") or "")]
    for u in items[1:]:
        gap = int(u.get("t0_ms", 0)) - int(cur.get("t1_ms", 0))
        span = int(u.get("t1_ms", 0)) - int(cur.get("t0_ms", 0))
        prev = (texts[-1] if texts else "").strip()
        unfinished = bool(prev) and not prev.endswith(("。", "！", "？", "!", "?", "…"))
        # merge short crumbs / unfinished speech more eagerly; completed sentences less so
        gap_lim = max_gap_ms + (350 if unfinished else 0)
        if gap <= gap_lim and span <= max_span_ms and (unfinished or _dur(cur) < 3200 or _dur(u) < 2800):
            cur["t1_ms"] = int(u.get("t1_ms", cur["t1_ms"]))
            texts.append(u.get("text") or "")
        else:
            cur["text"] = "，".join(t for t in texts if t)
            # natural tail pad for completed clauses
            if str(cur.get("text") or "").endswith(("。", "！", "？", "!", "?", "…")):
                cur["t1_ms"] = int(cur.get("t1_ms") or 0) + 160
            out.append(cur)
            cur = dict(u)
            texts = [str(cur.get("text") or "")]
    cur["text"] = "，".join(t for t in texts if t)
    if str(cur.get("text") or "").endswith(("。", "！", "？", "!", "?", "…")):
        cur["t1_ms"] = int(cur.get("t1_ms") or 0) + 160
    out.append(cur)
    for i, u in enumerate(out):
        u["utt_id"] = f"m{i:04d}"
    return out


def _learned_keep_score(text: str) -> float:
    """Optional human-learning score (0 if learning unavailable)."""
    try:
        # scripts run with src on path in worker
        from clipper.learning import learned_text_score  # type: ignore

        # stronger so learned reclip choices affect which lines survive filtering
        return float(learned_text_score(text, for_hook=True)) * 1.6
    except Exception:
        return 0.0


def filter_for_duration(
    raw: list[dict[str, Any]],
    *,
    target_ms: int = 78_000,
    min_ms: int = 72_000,
    max_ms: int = 85_000,
) -> list[dict[str, Any]]:
    """Keep clothing-only lines; fill toward duration without non-garment talk.

    Learning-aware: among candidate clothing lines, prefer those matching
    human/bootstrap preferences so front-loaded ranking has better material.
    """
    labeled: list[tuple[str, dict, float]] = []
    for u in raw:
        text = (u.get("text") or "").strip()
        if not text:
            continue
        g = classify(text)
        learn = _learned_keep_score(text)
        # hard drop still wins for price/size/live, but learning can demote medium
        if g == "drop":
            # allow rescue only for garment lines with very strong learned hook score
            if is_garment_line(text) and learn >= 40 and not _has_any(text, PRICE + SIZE_WORDS):
                labeled.append(("medium", dict(u), learn))
            continue
        labeled.append((g, dict(u), learn))

    strong = [u for g, u, _ in labeled if g == "strong"]
    medium = [u for g, u, _ in labeled if g == "medium"]
    learn_map = {id(u): sc for g, u, sc in labeled}

    strong_m = merge_nearby(strong)
    medium_m = merge_nearby(medium)

    def total(xs: list[dict]) -> int:
        return sum(_dur(u) for u in xs)

    def sort_key(u: dict) -> tuple:
        # prefer high learned score, then longer clips
        text = str(u.get("text") or "")
        return (_learned_keep_score(text), _dur(u))

    # seed with high-learning strong lines first
    strong_sorted = sorted(strong_m, key=sort_key, reverse=True)
    chosen: list[dict] = []
    for u in strong_sorted:
        chosen.append(u)
        if total(chosen) >= min_ms:
            break

    medium_sorted = sorted(medium_m, key=sort_key, reverse=True)
    for u in medium_sorted:
        if total(chosen) >= min_ms:
            break
        if all(u.get("t0_ms") != c.get("t0_ms") for c in chosen):
            # skip medium with strong negative learning
            if _learned_keep_score(str(u.get("text") or "")) <= -20:
                continue
            chosen.append(u)

    if total(chosen) < min_ms:
        for u in medium_sorted:
            if total(chosen) >= max_ms:
                break
            if all(u.get("t0_ms") != c.get("t0_ms") for c in chosen):
                if _learned_keep_score(str(u.get("text") or "")) <= -30:
                    continue
                chosen.append(u)

    if total(chosen) < min_ms:
        pool = sorted(medium + strong, key=sort_key, reverse=True)
        for u in pool:
            if total(chosen) >= min_ms:
                break
            if all(abs(int(u.get("t0_ms", 0)) - int(c.get("t0_ms", 0))) > 200 for c in chosen):
                chosen.append(dict(u))

    # DO NOT glue many lines into one mega-clip (that collapses UI to 1 segment).
    # Only merge very close crumbs for ASR continuity.
    chosen.sort(key=lambda u: int(u.get("t0_ms", 0)))
    chosen = merge_nearby(chosen, max_gap_ms=450, max_span_ms=7000)

    # split overlong merged lines so ranking can place multi sections
    split_out: list[dict] = []
    for u in chosen:
        text = str(u.get("text") or "")
        d = _dur(u)
        if d <= 10000 or "，" not in text:
            split_out.append(u)
            continue
        # split by Chinese comma into ~equal time slices
        parts = [p.strip() for p in re.split(r"[，,。！？!?]", text) if p.strip()]
        if len(parts) <= 1:
            split_out.append(u)
            continue
        t0 = int(u.get("t0_ms") or 0)
        t1 = int(u.get("t1_ms") or (t0 + d))
        step = max(1200, d // len(parts))
        cur = t0
        for i, p in enumerate(parts):
            nxt = t1 if i == len(parts) - 1 else min(t1, cur + step)
            if nxt <= cur:
                nxt = cur + 800
            split_out.append(
                {
                    "utt_id": f"{u.get('utt_id', 'm')}_s{i}",
                    "text": p,
                    "t0_ms": cur,
                    "t1_ms": nxt,
                }
            )
            cur = nxt
    chosen = split_out

    # if too long, drop lowest learning / shortest first
    while total(chosen) > max_ms and len(chosen) > 4:
        worst_i = min(
            range(len(chosen)),
            key=lambda i: (_learned_keep_score(str(chosen[i].get("text") or "")), _dur(chosen[i])),
        )
        chosen.pop(worst_i)

    # final safety: drop any non-garment that slipped in
    chosen = [u for u in chosen if is_garment_line(str(u.get("text") or ""))]
    # keep enough segments for structure (golden/trust/cta)
    if len(chosen) < 3:
        # rescue more medium lines from labeled even if duration already ok
        extra = sorted(medium + strong, key=lambda u: (_learned_keep_score(str(u.get("text") or "")), _dur(u)), reverse=True)
        for u in extra:
            if len(chosen) >= 6:
                break
            if any(abs(int(u.get("t0_ms", 0)) - int(c.get("t0_ms", 0))) <= 300 for c in chosen):
                continue
            if not is_garment_line(str(u.get("text") or "")):
                continue
            if _learned_keep_score(str(u.get("text") or "")) <= -40:
                continue
            chosen.append(dict(u))
        chosen.sort(key=lambda u: int(u.get("t0_ms", 0)))

    # demote leftover negative-learned lines if alternatives exist
    if len(chosen) > 4:
        chosen = [
            u
            for u in chosen
            if _learned_keep_score(str(u.get("text") or "")) > -40
        ] or chosen
    return chosen
