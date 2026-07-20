"""Clothing-focused transcript filter with merge + 55–60s fill."""
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
OFFTOPIC_WORDS = (
    "零食", "好吃", "水果", "开心果", "坚果", "包装太大", "浪费了", "安利的",
)

STRONG = (
    "面料", "布料", "材质", "牛仔", "牛仔裤", "显瘦", "遮肉", "收腰", "修身",
    "版型", "不透", "柔软", "超软", "软的", "软到", "蕾丝", "雷丝", "垂感", "弹力",
    "醋酸", "凉感", "雪纺", "纯棉", "洗水", "上衣", "裙子", "外套", "内搭",
    "遮胯", "高腰", "梨形", "闭眼入", "显白", "开叉", "领口", "袖口",
    "破洞", "拼接", "小雷丝", "马洗", "超级软", "天丝", "小破洞",
)

MEDIUM = (
    "裤子", "半身裙", "西装", "针织", "这件", "这套", "这一套", "穿上", "上身",
    "试穿", "廓形", "宽松", "直筒", "颜色", "黑色", "白色", "天丝",
    "客户", "姐妹", "不爱穿", "爱穿", "耐造", "好穿", "百搭", "通勤",
    "口袋", "拉链", "扣子", "下摆", "细节", "好看", "推荐", "牛在", "牛肉",
    "一整套", "暗通", "破洞牛", "小破洞牛", "打白色", "白色的", "好看到",
    "太好看", "超好看", "你好看", "也好看", "又好看", "小肥", "玻璃",
)

# Price talk is excluded from cuts entirely (product policy)
PRICE = (
    "券后", "直播价", "专属价", "秒杀", "包邮", "块钱", "只要", "原价", "到手",
    "20块", "长20", "便宜", "多少钱", "加购", "下单", "小黄车", "购物车",
    "号链接", "弹窗", "福袋", "拍下", "库存", "满减", "凑单",
)


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


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

    # never keep price/deal talk
    if price:
        return "drop"
    if off and not strong:
        return "drop"
    if size and not (strong or medium):
        return "drop"
    if sent and not strong:
        return "drop"
    if ("过一下" in t or "过一遍" in t) and not strong:
        return "drop"
    if filler and not (strong or medium):
        return "drop"
    if re.search(r"(穿一下|打一下).{0,8}牛仔", t) and not strong:
        return "drop"
    if re.search(r"不爱穿牛仔|穿牛仔很快|牛仔本身就是", t) and not strong:
        return "drop"

    if strong:
        return "strong"
    if medium:
        return "medium"
    return "drop"


def _dur(u: dict) -> int:
    return max(0, int(u.get("t1_ms", 0)) - int(u.get("t0_ms", 0)))


def merge_nearby(items: list[dict], *, max_gap_ms: int = 1200, max_span_ms: int = 12000) -> list[dict]:
    """Merge consecutive kept utterances into longer clips for duration."""
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
    # re-id
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
    # Defaults assume final 1.3x → need ~78s source for ~60s output
    labeled: list[tuple[str, dict]] = []
    for u in raw:
        text = (u.get("text") or "").strip()
        if not text:
            continue
        labeled.append((classify(text), dict(u)))

    strong = [u for g, u in labeled if g == "strong"]
    medium = [u for g, u in labeled if g == "medium"]
    # price intentionally ignored (no price talk in cuts)

    # Merge first to create longer usable segments
    strong_m = merge_nearby(strong)
    medium_m = merge_nearby(medium)

    def total(xs: list[dict]) -> int:
        return sum(_dur(u) for u in xs)

    chosen: list[dict] = list(strong_m)

    # fill with medium until min_ms
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

    # If still short: allow more medium from unmerged pool (individual lines)
    if total(chosen) < min_ms:
        for u in sorted(medium + strong, key=_dur, reverse=True):
            if total(chosen) >= min_ms:
                break
            if all(abs(int(u.get("t0_ms", 0)) - int(c.get("t0_ms", 0))) > 200 for c in chosen):
                chosen.append(dict(u))

    chosen.sort(key=lambda u: int(u.get("t0_ms", 0)))
    # final merge pass for adjacent chosen
    chosen = merge_nearby(chosen, max_gap_ms=1500, max_span_ms=15000)

    # trim if over max by dropping lowest priority from end of medium-only if needed
    while total(chosen) > max_ms and len(chosen) > 3:
        # drop shortest non-strong-looking tail piece
        # keep simple: drop last
        chosen = chosen[:-1]

    return chosen
