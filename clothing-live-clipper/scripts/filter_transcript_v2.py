"""Clothing-ONLY transcript filter. Main product = 衣服/服装."""
from __future__ import annotations

import re
from typing import Any

SIZE_WORDS = (
    "尺码", "选码", "偏大", "偏小", "胸围", "腰围", "臀围", "身高", "穿M", "穿S",
    "穿L", "穿XL", "均码", "加大码", "码数", "建议穿", "斤穿",
)
SENTIMENT_WORDS = (
    "做了五年", "不容易", "感谢陪伴", "创业", "初心", "故事是这样", "一路走来",
    "谢谢支持我", "喜欢我的人",
)
FILLER_WORDS = (
    "家人们", "老铁们", "听得到吗", "扣1", "扣一", "点点关注", "双击", "晚上好啊", "来了吗",
    "过一下", "过一遍", "带过", "先过", "往下过", "咱们过",
    "看一看", "说一下", "讲一下", "介绍一下",
    "给大家看", "给你们看", "来看一下", "注意看",
    "一会儿", "待会", "等会", "马上", "接下来",
    "铃铃铃", "有没有人", "在不在", "刚进来", "欢迎",
    "感谢", "谢谢老板", "谢谢姐妹", "公屏", "弹幕", "刷波",
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
)

# Only valid as secondary WHEN garment noun/feature also present
MEDIUM = (
    "这件", "这套", "这一套", "穿上", "上身", "试穿", "廓形", "宽松", "直筒",
    "耐造", "好穿", "百搭", "通勤", "口袋", "拉链", "扣子", "下摆", "细节",
    "推荐", "破洞牛", "小破洞牛", "一整套", "搭配牛仔裤", "搭配裙子",
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
    if _has_any(t, ("显瘦", "遮肉", "遮胯", "不透", "显白", "闭眼入", "梨形")):
        return True
    return False


def classify(text: str) -> str:
    t = text.strip()
    if not t or len(t) < 2:
        return "drop"

    strong = _has_any(t, STRONG)
    medium = _has_any(t, MEDIUM)
    price = _has_any(t, PRICE)
    size = _has_any(t, SIZE_WORDS)
    sent = _has_any(t, SENTIMENT_WORDS)
    filler = _has_any(t, FILLER_WORDS)
    off = _has_any(t, OFFTOPIC_WORDS)
    garment = is_garment_line(t)

    # never keep price
    if price:
        return "drop"
    # non-clothing topics
    if off and not strong:
        return "drop"
    if not garment:
        # pure 好看/白色/客户 等不够
        return "drop"
    if size and not strong:
        return "drop"
    if sent and not strong:
        return "drop"
    if ("过一下" in t or "过一遍" in t) and not strong:
        return "drop"
    if filler and not (strong or (medium and garment)):
        return "drop"
    # try-on filler without real feature
    if re.search(r"(穿一下|打一下).{0,8}牛仔", t) and not _has_any(
        t, ("面料", "显瘦", "遮肉", "版型", "不透", "柔软", "软", "弹力")
    ):
        return "drop"
    if re.search(r"不爱穿牛仔|穿牛仔很快|牛仔本身就是", t) and not _has_any(
        t, ("面料", "显瘦", "版型", "弹力", "不透", "软")
    ):
        return "drop"
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
    if not items:
        return []
    items = sorted(items, key=lambda u: int(u.get("t0_ms", 0)))
    out: list[dict] = []
    cur = dict(items[0])
    texts = [cur.get("text", "")]
    for u in items[1:]:
        gap = int(u.get("t0_ms", 0)) - int(cur.get("t1_ms", 0))
        span = int(u.get("t1_ms", 0)) - int(cur.get("t0_ms", 0))
        if gap <= max_gap_ms and span <= max_span_ms:
            cur["t1_ms"] = int(u.get("t1_ms", cur["t1_ms"]))
            texts.append(u.get("text") or "")
        else:
            cur["text"] = "，".join(t for t in texts if t)
            out.append(cur)
            cur = dict(u)
            texts = [cur.get("text", "")]
    cur["text"] = "，".join(t for t in texts if t)
    out.append(cur)
    for i, u in enumerate(out):
        u["utt_id"] = f"m{i:04d}"
    return out


def filter_for_duration(
    raw: list[dict[str, Any]],
    *,
    target_ms: int = 78_000,
    min_ms: int = 72_000,
    max_ms: int = 85_000,
) -> list[dict[str, Any]]:
    """Keep clothing-only lines; fill toward duration without non-garment talk."""
    labeled: list[tuple[str, dict]] = []
    for u in raw:
        text = (u.get("text") or "").strip()
        if not text:
            continue
        labeled.append((classify(text), dict(u)))

    strong = [u for g, u in labeled if g == "strong"]
    medium = [u for g, u in labeled if g == "medium"]

    strong_m = merge_nearby(strong)
    medium_m = merge_nearby(medium)

    def total(xs: list[dict]) -> int:
        return sum(_dur(u) for u in xs)

    chosen: list[dict] = list(strong_m)
    medium_sorted = sorted(medium_m, key=_dur, reverse=True)
    for u in medium_sorted:
        if total(chosen) >= min_ms:
            break
        if all(u.get("t0_ms") != c.get("t0_ms") for c in chosen):
            chosen.append(u)

    if total(chosen) < min_ms:
        for u in medium_sorted:
            if total(chosen) >= max_ms:
                break
            if all(u.get("t0_ms") != c.get("t0_ms") for c in chosen):
                chosen.append(u)

    if total(chosen) < min_ms:
        for u in sorted(medium + strong, key=_dur, reverse=True):
            if total(chosen) >= min_ms:
                break
            if all(abs(int(u.get("t0_ms", 0)) - int(c.get("t0_ms", 0))) > 200 for c in chosen):
                chosen.append(dict(u))

    chosen.sort(key=lambda u: int(u.get("t0_ms", 0)))
    chosen = merge_nearby(chosen, max_gap_ms=1500, max_span_ms=15000)

    while total(chosen) > max_ms and len(chosen) > 3:
        chosen = chosen[:-1]

    # final safety: drop any non-garment that slipped in
    chosen = [u for u in chosen if is_garment_line(str(u.get("text") or ""))]
    return chosen
