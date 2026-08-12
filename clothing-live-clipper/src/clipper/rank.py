from __future__ import annotations

import re
from collections.abc import Iterable

from clipper.config import Settings
from clipper.extract import is_chitchat_text
from clipper.learning import learned_text_score
from clipper.models import ClaimType, Clip, PlanSlot, TimelinePlan

# Simplified weights for MVP
SCORE_WEIGHTS = {
    ClaimType.SELLING_POINT: 40.0,
    ClaimType.FIT: 20.0,
    ClaimType.FABRIC: 20.0,
    ClaimType.PRICE: 0.0,  # product policy: no price talk in cuts
    ClaimType.DETAIL: 8.0,
    ClaimType.SCENE: 8.0,
    ClaimType.SIZE: 8.0,
    ClaimType.OUTFIT: 6.0,
    ClaimType.CHITCHAT: 0.0,
}

_PRICE_TEXT = (
    "券后", "券後", "只要", "原价", "原價", "现价", "現價", "秒杀", "秒殺", "限时", "限時",
    "包邮", "包郵", "拍下", "链接", "鏈接", "库存", "庫存",
    "凑单", "湊單", "满减", "滿減", "到手", "块钱", "塊錢", "多少钱", "多少錢", "便宜",
    "加一捕", "加购", "加購", "下单", "下單",
    "小黄车", "小黃車", "购物车", "購物車", "号链接", "號鏈接", "弹窗", "彈窗", "福袋",
    "直播价", "直播價", "专属价", "專屬價", "到手价", "到手價",
    # 口语 / ASR 变体（你截图里的「定价/拨分/发货」）
    "定价", "定價", "价钱", "價錢", "价格", "價格", "拨分", "撥分",
    "发货", "發貨", "发貨", "现货", "現貨", "预售", "預售", "物流", "快递", "快遞",
    "顺丰", "順豐", "几天发", "幾天發", "今日发", "今日發", "补货", "補貨",
    "付款", "包邮", "邮费", "郵費", "太贵", "太貴", "贵呀", "貴呀",
    # 直播催单 / 成交口令
    "加一单", "加一單", "再加一单", "再加一單", "拍一单", "拍一單", "补一单", "补一單",
    "加两单", "加几单", "拍两单", "来一单", "再来一单", "加一件", "拍一件",
    "加一波", "冲一波", "赶紧加", "赶快加", "抓紧加", "闭眼加", "有货的加",
    "想要的加", "喜欢的加", "看上的加", "秒了", "秒它", "锁单", "锁住",
    # 挂车 / n号 / 其它链接表达
    "上链接", "上鏈接", "点链接", "戳链接", "拍链接", "放链接", "挂链接", "开链接", "给链接",
    "几号链接", "幾號鏈接", "1号链接", "2号链接", "3号链接", "一号链接", "二号链接", "三号链接",
    "上方链接", "下方链接", "挂上车", "上车了", "加车", "加車", "上车", "上車", "领券",
)

# Hard size advice — never keep in final cut
_SIZE_TEXT = (
    "尺码", "尺碼", "选码", "選碼", "偏大", "偏小", "腰围", "胸围", "臀围", "肩宽", "均码", "均碼",
    "加大码", "加大碼", "码数", "碼數", "建议穿", "建議穿", "该穿", "該穿", "推荐穿", "推薦穿",
    "斤穿", "身高", "体重", "試碼", "试码", "报尺码", "報尺碼",
    "穿M", "穿S", "穿L", "穿XL", "穿XXL", "S码", "M码", "L码", "XL码", "XXL码",
    "袖长", "衣长", "裤长", "裙长", "能穿吗", "能不能穿", "哪个码", "什麼碼", "什么码", "几码", "幾碼",
    "胸大", "胸小", "卡满", "网袋胸", "罩杯", "下围", "大一码", "小一码", "中码", "小码",
)

# Douyin compliance: absolute claims / medical / off-platform diversion
_POLICY_RISK_TEXT = (
    "最好", "最佳", "第一", "顶级", "国家级", "全网最低", "史上最低", "永久",
    "根治", "包治", "特效", "神奇", "神器", "保证瘦", "一定瘦", "三天瘦",
    "一穿就瘦", "瞬间瘦", "永久显瘦", "百分百", "100%", "绝对",
    "治疗", "疗效", "处方", "医院同款", "医用", "防癌", "消炎",
    "加微信", "加我微信", "薇信", "vx", "v信", "威信", "私信领", "私聊发",
    "扫码进群", "扫码加", "外部链接", "复制口令", "淘口令", "去淘宝",
    "点头像", "主页链接", "主页买", "评论区扣", "扣链接",
    "假货", "高仿", "走私", "水货",
)

# Unique / rare product claims — rank to front of golden 20s
_UNIQUE_FEATURE_WORDS = (
    "独家", "独创", "专利", "首创", "限定", "限量", "仅此", "独一无二",
    "只有我们", "市面少见", "很少见", "别处没有", "买不到", "独家面料",
    "独家版型", "自研", "私模", "独家工艺", "独家设计", "独家配方",
    "全网首发", "首发", "仅此一家", "稀缺", "紧俏", "断码前",
    "三防", "防晒", "防水", "防风", "凉感", "冰丝", "醋酸", "真丝",
    "羊绒", "桑蚕丝", "四面弹", "360度", "不勒", "不卷边", "不起球",
    "不缩水", "不掉色", "免烫", "可机洗", "抗皱",
)


_CLOTHING_TEXT_HINTS = (
    "面料", "布料", "材质", "牛仔", "蕾丝", "雷丝", "不透", "柔软", "软到", "超软",
    "洗水", "破洞", "天丝", "醋酸", "显瘦", "遮肉", "版型", "收腰", "上衣", "裙子",
    "裤子", "外套", "内搭", "连衣裙", "衣服", "服装", "衬衫", "毛衣", "大衣", "风衣",
    "口袋", "穿上", "上身", "这件", "这套", "推荐", "软", "弹", "拼接", "领口", "袖口",
    "开叉", "高腰", "梨形", "闭眼入", "显白", "垂感", "透气",
)


def score_clip(clip: Clip) -> Clip:
    types = set(clip.claim_types)
    breakdown: dict[str, float] = {}
    raw = 0.0
    text = clip.text or ""

    # Hard policy: never put price / deal / shipping talk into final cut
    if ClaimType.PRICE in types or any(p in text for p in _PRICE_TEXT):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"price_excluded": 0.0, "raw": 0.0}
        return clip
    # numeric / slang price patterns (599拨分、1000多、¥59、发货時間…)
    if re.search(r"(¥|￥)\s*\d+", text) or re.search(r"\d+\s*(块|塊|元|块钱|塊錢|拨分|撥分)", text):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"price_excluded": 0.0, "raw": 0.0}
        return clip
    if re.search(r"(发|發).{0,4}(货|貨)", text):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"shipping_excluded": 0.0, "raw": 0.0}
        return clip
    # 直播成交口令：加一单 / 拍一单 / 赶紧加…
    if re.search(
        r"(加|拍|下|锁|鎖|来|來)\s*(?:个|個|了)?\s*(一|1|两|倆|俩|二|几|幾)\s*(?:个|個)?\s*(单|單|波|件)",
        text,
    ):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"deal_call_excluded": 0.0, "raw": 0.0}
        return clip
    if re.search(
        r"(赶紧|赶快|抓紧|全部|一起|全场|有货|想要|喜欢|看上|闭眼|马上).{0,6}(加|拍|下单|下單|锁|鎖)",
        text,
    ):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"deal_call_excluded": 0.0, "raw": 0.0}
        return clip
    # n号链接 / 点链接 / 挂车入口
    if re.search(
        r"(?:[0-9０-９一二三四五六七八九十两俩几幾nN]\s*)+(?:号|號)\s*(?:链接|鏈接|小黄车|小黃車|购物车|購物車|位|窗)?",
        text,
    ) or re.search(r"(上|点|點|戳|拍|放|挂|掛|开|開|给|給|看)\s*(?:个|個)?\s*(链接|鏈接)", text):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"link_slot_excluded": 0.0, "raw": 0.0}
        return clip
    if re.search(r"(加购|加購|加车|加車|上车|上車|挂车|掛車)", text):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"deal_call_excluded": 0.0, "raw": 0.0}
        return clip

    # Hard policy: never put size chart / sizing advice into final cut
    if ClaimType.SIZE in types or any(p in text for p in _SIZE_TEXT):
        # pure size always out; mixed size+feature still out (user: 去除尺码)
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_excluded": 0.0, "raw": 0.0}
        return clip
    # letter size / weight pick-size slang
    if re.search(r"(?<![A-Za-z0-9])(XS|S|M|L|XL|XXL|2XL|3XL)(?![A-Za-z0-9])", text, flags=re.I) and re.search(
        r"(码|碼|號|号|穿|选|選|拍|加|来|來)", text
    ):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_excluded": 0.0, "raw": 0.0}
        return clip
    if re.search(r"\d+\s*(斤|公斤|kg)", text, flags=re.I) and any(
        k in text for k in ("穿", "码", "碼", "号", "號", "适合", "適合", "建议", "建議")
    ):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_excluded": 0.0, "raw": 0.0}
        return clip

    # Douyin risk: absolute/medical/off-platform — never keep in publish cut
    if any(p in text for p in _POLICY_RISK_TEXT) or re.search(
        r"(加|加下|加我).{0,4}(微信|vx|v信|薇信|威信|扣扣|qq|QQ)", text, flags=re.I
    ):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"policy_risk_excluded": 0.0, "raw": 0.0}
        return clip

    if ClaimType.CHITCHAT in types and len(types) == 1:
        if not any(h in text for h in _CLOTHING_TEXT_HINTS):
            clip.score = 0.0
            clip.weight = 0.0
            clip.score_breakdown = {"chitchat": 0.0, "raw": 0.0}
            return clip

    if is_chitchat_text(clip.text) and not (
        types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC, ClaimType.PRICE}
    ) and not any(h in text for h in _CLOTHING_TEXT_HINTS):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"chitchat": 0.0, "raw": 0.0}
        return clip

    content_types = {
        t
        for t in types
        if t not in {ClaimType.CHITCHAT, ClaimType.SIZE, ClaimType.PRICE}
    }
    if not content_types:
        if any(h in text for h in _CLOTHING_TEXT_HINTS):
            raw = 14.0
            if len(text) >= 10:
                raw += 3.0
            if clip.duration_ms >= 2000:
                raw += 3.0
            breakdown["text_hint_rescue"] = raw
            breakdown["raw"] = raw
            clip.score = raw
            clip.score_breakdown = breakdown
            return clip
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"no_clothing_claim": 0.0, "raw": 0.0}
        return clip

    if content_types <= {ClaimType.SIZE}:
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"size_only": 0.0, "raw": 0.0}
        return clip

    if content_types <= {ClaimType.OUTFIT} and not any(h in text for h in _CLOTHING_TEXT_HINTS):
        clip.score = 0.0
        clip.weight = 0.0
        clip.score_breakdown = {"outfit_only": 0.0, "raw": 0.0}
        return clip

    for t in content_types:
        w = SCORE_WEIGHTS.get(t, 0.0)
        breakdown[t.value] = w
        raw += w

    combo = 0.0
    if ClaimType.SELLING_POINT in content_types and (
        ClaimType.FIT in content_types or ClaimType.FABRIC in content_types
    ):
        combo = 10.0
        raw += combo
    breakdown["combo_bonus"] = combo

    if any(h in text for h in _CLOTHING_TEXT_HINTS):
        raw += 6.0
        breakdown["clothing_hint"] = 6.0

    spec = 0.0
    if any(ch.isdigit() for ch in clip.text):
        spec += 5.0
    if len(clip.text) >= 12:
        spec += 3.0
    if len(clip.text) >= 24:
        spec += 4.0
    raw += spec
    breakdown["specificity"] = spec

    dur = clip.duration_ms
    if 1500 <= dur <= 15000:
        raw += 4.0
        breakdown["duration_bonus"] = 4.0
    elif dur > 0:
        breakdown["duration_bonus"] = 1.0
        raw += 1.0
    else:
        breakdown["duration_bonus"] = 0.0

    # Plan D learning boost for general ranking (non-hook too)
    try:
        learned = learned_text_score(text, for_hook=False)
        if abs(learned) > 0.01:
            # amplify so it can override weak keyword ties
            learned_adj = learned * 1.8
            raw += learned_adj
            breakdown["learned"] = learned_adj
    except Exception:
        pass

    breakdown["raw"] = raw
    clip.score = raw
    clip.score_breakdown = breakdown
    return clip


def score_all(clips: list[Clip]) -> list[Clip]:
    scored = [score_clip(c.model_copy(deep=True)) for c in clips]
    positives = [c.score for c in scored if c.score > 0]
    if not positives:
        for c in scored:
            c.weight = 0.0
        return scored
    lo, hi = min(positives), max(positives)
    span = hi - lo if hi > lo else 1.0
    for c in scored:
        c.weight = 0.0 if c.score <= 0 else (c.score - lo) / span
    return scored


def _norm_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    # collapse repeated punctuation/chars noise from ASR
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)
    return t


def _token_set(text: str) -> set[str]:
    # rough CJK bigrams + alnum words
    t = _norm_text(text)
    if not t:
        return set()
    toks: set[str] = set()
    for m in re.finditer(r"[a-z0-9]+|[\u4e00-\u9fff]{1,2}", t):
        toks.add(m.group(0))
    # also add overlapping bigrams for CJK
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", t)
    for i in range(len(cjk) - 1):
        toks.add(cjk[i : i + 2])
    return toks


def _similarity(a: str, b: str) -> float:
    """0–1 soft similarity; high means near-duplicate."""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb) or 1
    jacc = inter / union
    # boost if share long common substring
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    substr = 0.0
    if len(shorter) >= 4:
        for n in (6, 5, 4):
            if len(shorter) < n:
                continue
            for i in range(0, len(shorter) - n + 1, max(1, n // 2)):
                if shorter[i : i + n] in longer:
                    substr = max(substr, n / max(len(longer), 1))
                    break
    return max(jacc, substr)


_LIVE_ROOM_MARKERS = (
    "家人们", "老铁", "宝宝们", "姐妹们", "宝贝们", "直播间", "扣1", "扣一",
    "点关注", "双击", "刷波", "公屏", "弹幕", "福袋", "连麦", "上链接",
    "小黄车", "欢迎进来", "新进来", "听得到", "在不在", "来了吗", "过一下",
    # 导播/准备口令（用户截图：准备一下 / 321 / 里面去拍）
    "准备一下", "来准备", "先准备", "準備一下", "备一下", "備一下",
    "里面去拍", "裏面去拍", "里面拍", "出去拍", "换机位", "转个机位",
    "倒计时", "倒數", "三二一", "上脚", "上裤", "来凳", "凳子",
    # 人设/标签灌鸡汤
    "定义我的标签", "甄姐的标签", "标签不是随意", "摸不着拆不透", "不要随便定义",
)


def _looks_like_live_room(text: str) -> bool:
    t = text or ""
    hits = sum(1 for w in _LIVE_ROOM_MARKERS if w in t)
    if hits >= 1 and not any(h in t for h in _CLOTHING_TEXT_HINTS):
        return True
    if hits >= 2:
        return True
    if re.search(r"(扣|点|刷).{0,2}(1|一|关注)", t):
        return True
    # 3 2 1 countdown / 准备口令
    if re.search(r"(?<!\d)3\s*2\s*1(?!\d)", t):
        return True
    if re.search(r"(准备|準備|备一下|備一下).{0,6}(一下|下)", t):
        return True
    if re.search(r"(里面|裏面|外头|外面).{0,4}(拍|去拍)", t):
        return True
    # 人设标签鸡汤
    if any(k in t for k in ("定义我的标签", "甄姐的标签", "标签不是随意", "摸不着拆不透")):
        return True
    if "标签" in t and not any(h in t for h in _CLOTHING_TEXT_HINTS):
        return True
    # 胸大/胸小报码
    if ("胸大" in t or "胸小" in t or "卡满" in t) and not any(
        h in t for h in ("版型", "显瘦", "面料", "垂感")
    ):
        return True
    return False


def _is_pure_filler(c: Clip) -> bool:
    if c.score <= 0:
        return True
    text = c.text or ""
    types = set(c.claim_types)
    if _looks_like_live_room(text) and not (
        types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC}
    ):
        return True
    if ClaimType.CHITCHAT in types and len(types) == 1:
        if not any(h in text for h in _CLOTHING_TEXT_HINTS):
            return True
    if is_chitchat_text(c.text) and not any(h in text for h in _CLOTHING_TEXT_HINTS):
        if not (types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC, ClaimType.PRICE}):
            return True
    return False


# True product FEATURES for first ~20s only (not outfit / try-on / change clothes)
_HOOK_FEATURE_WORDS = (
    "显瘦", "遮肉", "遮胯", "不透", "柔软", "超软", "软到", "软的", "超级软",
    "闭眼入", "垂感", "弹力", "不起球", "透气", "显白", "收腰", "修身",
    "面料", "布料", "材质", "天丝", "醋酸", "凉感", "雪纺", "纯棉",
    "版型", "高腰", "梨形", "显腿长", "不挑人", "好打理", "可机洗",
    # on-body wearing effects (keep in cut — not size chart)
    "不走光", "不漏光", "防走光", "不显肚子", "不显肚", "遮肚子", "遮肚",
    "胃包", "拜拜肉", "蝴蝶袖", "副乳", "遮副乳", "不显胯", "收腹", "安全感",
    # wear experience
    "舒服", "舒适", "贴肤", "亲肤", "冰冰的", "凉凉的", "不闷", "不闷汗",
    "凉快", "轻盈", "松弛", "好穿", "穿着舒服", "上身舒服",
)

# Outfit / change-look / try-on → keep for later body, NOT golden 20s
_OUTFIT_CHANGE_WORDS = (
    "搭配", "换装", "换上", "换件", "换一个", "下一件", "再穿", "套装",
    "一整套", "穿一下", "打一下", "试穿", "上身看看", "搭个", "配个",
    "牛仔裤", "小白鞋", "内搭", "外套怎么", "怎么搭", "破洞牛仔", "破洞牛",
    "小破洞", "你的衣服里", "衣服人",
)

# Wear experience phrases — ALLOW in final cut (often trust, sometimes golden)
_WEAR_EXPERIENCE_WORDS = (
    "舒服", "舒适", "贴肤", "亲肤", "冰冰的", "凉凉的", "不闷", "不闷汗",
    "透气", "凉快", "轻盈", "松弛", "好穿", "穿着舒服", "上身舒服",
    "一整个夏天", "一整天", "不勒肉", "不磨", "软软的", "遮盖", "体感",
    "上身感", "手感", "质感",
)


def _is_wear_experience(c: Clip) -> bool:
    text = c.text or ""
    return any(w in text for w in _WEAR_EXPERIENCE_WORDS)


def _is_outfit_or_change(c: Clip) -> bool:
    """Outfit / try-on / change-clothes talk should not lead the first 20s.

    Wear-experience talk (舒服/贴肤/不闷) is NOT treated as pure outfit ban —
    it can stay in final cut (usually trust section).
    """
    text = c.text or ""
    types = set(c.claim_types)
    wear = _is_wear_experience(c)

    if ClaimType.OUTFIT in types or ClaimType.SCENE in types:
        # still allow if mainly feature / wear experience
        if ClaimType.SELLING_POINT in types or ClaimType.FABRIC in types or ClaimType.FIT in types:
            if any(w in text for w in _HOOK_FEATURE_WORDS) or wear:
                return False
        if wear:
            return False
        return True
    if any(w in text for w in _OUTFIT_CHANGE_WORDS):
        # pure try-on / change / match without clear product feature or wear feel
        if any(w in text for w in _HOOK_FEATURE_WORDS) or wear:
            # "穿一下牛仔裤" style without feature → still outfit
            if re.search(r"(穿一下|打一下|试穿|换装|换上).{0,8}(牛仔|裤子|裙子|外套|上衣)", text) and not wear:
                return True
            if ("搭配" in text or "搭个" in text or "配个" in text) and not any(
                w in text for w in ("显瘦", "遮肉", "不透", "面料", "版型", "柔软", "软到", "舒服", "透气", "凉快")
            ):
                return True
            return False
        return True
    return False


def _is_true_feature(c: Clip) -> bool:
    """True clothing features for golden 20s (includes wear experience)."""
    if _is_outfit_or_change(c) and not _is_wear_experience(c):
        return False
    types = set(c.claim_types)
    text = c.text or ""
    if types & {ClaimType.SELLING_POINT, ClaimType.FIT, ClaimType.FABRIC}:
        return True
    if any(w in text for w in _HOOK_FEATURE_WORDS):
        return True
    if _is_wear_experience(c):
        return True
    # detail alone is weaker; allow only with feature word
    if ClaimType.DETAIL in types and any(w in text for w in ("蕾丝", "雷丝", "拼接", "面料", "不透")):
        return True
    return False


def _unique_feature_boost(text: str) -> float:
    """Unique / scarce product claims go first among features."""
    t = text or ""
    hits = [w for w in _UNIQUE_FEATURE_WORDS if w in t]
    if not hits:
        return 0.0
    # stronger boost for exclusivity words
    exclusivity = ("独家", "独创", "专利", "首创", "独一无二", "只有我们", "别处没有", "全网首发", "限量")
    bonus = 0.0
    for w in hits:
        bonus += 28.0 if any(e in w or w in e for e in exclusivity) else 16.0
    return min(90.0, bonus)


def _hook_strength(c: Clip) -> float:
    """Front 20s score: attractive product claims only; no live-room feel."""
    types = set(c.claim_types)
    text = c.text or ""
    # hard ban outfit/change / size / live-room from front 20s ranking
    if _looks_like_live_room(text):
        return -150.0
    if _is_outfit_or_change(c):
        return -100.0
    if ClaimType.SIZE in types or any(p in text for p in _SIZE_TEXT):
        return -120.0
    if ClaimType.PRICE in types or any(p in text for p in _PRICE_TEXT):
        return -120.0

    s = 0.0
    if ClaimType.SELLING_POINT in types:
        s += 60.0
    if ClaimType.FIT in types:
        s += 34.0
    if ClaimType.FABRIC in types:
        s += 36.0
    if ClaimType.DETAIL in types:
        s += 8.0  # detail secondary even in golden

    hits = sum(1 for w in _HOOK_FEATURE_WORDS if w in text)
    s += min(45.0, hits * 10.0)

    # UNIQUE features float to the very front (吸引力核心)
    uniq = _unique_feature_boost(text)
    s += uniq * 1.25

    if ClaimType.SELLING_POINT in types and (ClaimType.FIT in types or ClaimType.FABRIC in types):
        s += 28.0
    # high-attraction concrete benefits
    if any(w in text for w in ("不透", "显瘦", "遮肉", "软到", "超级软", "面料", "版型", "收腰", "闭眼入", "梨形")):
        s += 22.0
    if any(w in text for w in ("独家", "专利", "限定", "首创", "凉感", "不起球", "可机洗", "抗皱")):
        s += 26.0
    # wear experience can support golden when paired with product talk
    if _is_wear_experience(c):
        s += 14.0
        if any(w in text for w in ("面料", "版型", "显瘦", "不透", "软", "透气", "凉")):
            s += 10.0

    # demote vague praise / demo filler
    if "好看" in text and hits == 0 and ClaimType.SELLING_POINT not in types:
        s -= 55.0
    if re.search(r"穿一下|打一下|试穿|换装", text):
        s -= 45.0
    if not _is_true_feature(c):
        s -= 70.0

    # Plan D: human feedback memory (what you kept/dropped/hooked)
    try:
        # stronger on hook path: this decides front 20s order
        s += learned_text_score(text, for_hook=True) * 2.2
    except Exception:
        pass

    s += c.score * 0.28
    return s


def _primary_stage(c: Clip) -> int:
    """Narrative stage: 0=feature hook, then fit/fabric, detail, outfit last."""
    types = set(c.claim_types)
    text = c.text or ""
    # outfit / change always late body
    if _is_outfit_or_change(c):
        return 4
    if ClaimType.SELLING_POINT in types and (ClaimType.FABRIC in types or ClaimType.FIT in types):
        return 0
    if ClaimType.SELLING_POINT in types or any(
        w in text for w in ("显瘦", "遮肉", "不透", "软到", "超级软", "闭眼入")
    ):
        return 0
    if ClaimType.FIT in types or any(w in text for w in ("收腰", "修身", "版型", "高腰", "梨形")):
        return 1
    if ClaimType.FABRIC in types or any(
        k in text for k in ("面料", "材质", "布料", "天丝", "醋酸", "柔软", "软", "蕾丝", "雷丝")
    ):
        return 2
    if ClaimType.DETAIL in types or any(k in text for k in ("细节", "拼接", "口袋", "开叉", "领口")):
        return 3
    if ClaimType.OUTFIT in types or ClaimType.SCENE in types or "搭配" in text:
        return 4
    if ClaimType.PRICE in types:
        return 5
    return 3


def _naturalize_bounds(c: Clip) -> tuple[int, int]:
    """
    Soften hard ASR cut points so modules don't end mid-breath.
    - Prefer slightly longer tails for natural sentence close
    - Avoid ultra-short fragments
    """
    t0 = max(0, int(c.t0_ms or 0))
    t1 = max(t0 + 280, int(c.t1_ms or 0))
    dur = t1 - t0
    text = (c.text or "").strip()

    # open a little earlier if clip is not already long
    if dur < 12000:
        t0 = max(0, t0 - 80)
    # prefer closing after a short breath, especially if text ends with punctuation
    if text.endswith(("。", "！", "？", "!", "?", "…")):
        t1 = t1 + 220
    elif text.endswith(("，", ",", "、")):
        # incomplete clause: give a bit less tail, still not hard-stop
        t1 = t1 + 120
    else:
        t1 = t1 + 180

    # keep reasonable module lengths
    if t1 - t0 < 900:
        t1 = t0 + 900
    if t1 - t0 > 18000:
        # too long modules feel abrupt when forced to end later; leave to splitter
        pass
    return t0, t1


def _to_slot(c: Clip, role: str) -> PlanSlot:
    t0, t1 = _naturalize_bounds(c)
    return PlanSlot(
        clip_id=c.clip_id,
        role=role,
        t0_ms=t0,
        t1_ms=t1,
        text=c.text,
        score=c.score,
    )


def _pick_logical(
    candidates: list[Clip],
    budget_ms: int,
    used: set[str],
    role: str,
    *,
    prefer_types: set[ClaimType] | None = None,
    prefer_stages: Iterable[int] | None = None,
    dedupe_threshold: float = 0.72,
    logic_over_dedupe: bool = True,
    chronological_bias: float = 0.35,
    feature_first: bool = False,
    time_chain: bool = False,
) -> list[PlanSlot]:
    """
    Pick clips with:
    1) narrative stage / preferred types (logic)
    2) feature-first boost for golden 20s
    3) score
    4) soft near-duplicate penalty (logic > hard non-repeat)
    5) stronger chronological continuity to reduce jump-cut feel
    """
    slots: list[PlanSlot] = []
    remaining = budget_ms
    pool = [c for c in candidates if c.clip_id not in used and not _is_pure_filler(c)]
    if not pool or remaining <= 200:
        return slots

    stage_pref = set(prefer_stages or [])
    selected_texts: list[str] = []
    last_t0: int | None = None
    last_t1: int | None = None

    while remaining > 200 and pool:
        best: Clip | None = None
        best_key: tuple | None = None

        for c in pool:
            if c.duration_ms > remaining + 8000 and slots:
                continue

            types = set(c.claim_types)
            stage = _primary_stage(c)
            type_boost = 120.0 if prefer_types and (types & prefer_types) else 0.0
            stage_boost = 45.0 if stage in stage_pref else 0.0
            feature_boost = _hook_strength(c) if (feature_first or role == "hook") else 0.0

            # soft dedupe
            sim = 0.0
            for prev in selected_texts:
                sim = max(sim, _similarity(c.text, prev))
            if sim >= 0.95:
                if not logic_over_dedupe or remaining < 8000:
                    continue
                dedupe_pen = 80.0
            elif sim >= dedupe_threshold:
                dedupe_pen = 25.0 + 40.0 * sim
            else:
                dedupe_pen = sim * 12.0

            # chronological continuity (stronger when time_chain)
            chrono = 0.0
            bias = chronological_bias * (1.7 if time_chain else 1.0)
            if last_t1 is not None:
                gap = c.t0_ms - last_t1
                # prefer next nearby segment (like continuous live talk)
                if 0 <= gap <= 8000:
                    chrono = bias * 55.0
                elif 0 <= gap <= 25000:
                    chrono = bias * 35.0 * (1.0 - gap / 25000.0)
                elif 0 <= gap <= 60000:
                    chrono = bias * 12.0 * (1.0 - gap / 60000.0)
                elif gap < 0:
                    # jumping backward feels edited
                    chrono = -22.0 if time_chain else -10.0
                else:
                    chrono = -6.0 if time_chain else -2.0
            elif last_t0 is not None:
                delta = c.t0_ms - last_t0
                if 0 <= delta <= 45000:
                    chrono = bias * (1.0 - min(delta, 45000) / 45000.0) * 30.0
                elif delta < 0:
                    chrono = -10.0

            if role == "hook":
                # features MUST dominate first 20s
                stage_boost += 35.0 if stage <= 2 else -25.0
                feature_boost *= 1.35
            if role == "trust" and 1 <= stage <= 4:
                stage_boost += 15.0
            if role == "cta" and stage <= 2:
                stage_boost += 20.0  # recap features, not price

            key = (
                feature_boost + type_boost + stage_boost + c.score + chrono - dedupe_pen,
                -stage if role != "cta" else stage,
                c.weight,
                -abs((last_t1 or last_t0 or c.t0_ms) - c.t0_ms),
            )
            if best is None or key > best_key:  # type: ignore[operator]
                best = c
                best_key = key

        if best is None:
            break

        if selected_texts and _similarity(best.text, selected_texts[-1]) >= 0.88:
            alt = None
            alt_key = None
            for c in pool:
                if c.clip_id == best.clip_id:
                    continue
                if c.duration_ms > remaining + 8000 and slots:
                    continue
                if _similarity(c.text, selected_texts[-1]) >= 0.88:
                    continue
                # keep time continuity when replacing
                if last_t1 is not None and time_chain:
                    gap = c.t0_ms - last_t1
                    if gap < -5000 or gap > 90000:
                        continue
                types = set(c.claim_types)
                type_boost = 100.0 if prefer_types and (types & prefer_types) else 0.0
                k = (
                    (_hook_strength(c) if feature_first or role == "hook" else 0.0)
                    + type_boost
                    + c.score,
                    c.weight,
                )
                if alt is None or k > alt_key:  # type: ignore[operator]
                    alt, alt_key = c, k
            if alt is not None and logic_over_dedupe and alt.score >= best.score * 0.50:
                best = alt

        slots.append(_to_slot(best, role))
        used.add(best.clip_id)
        selected_texts.append(best.text)
        last_t0 = best.t0_ms
        last_t1 = best.t1_ms
        remaining -= best.duration_ms
        pool = [c for c in pool if c.clip_id not in used]

    return slots


def _reorder_section_logical(slots: list[PlanSlot], by_id: dict[str, Clip], role: str) -> list[PlanSlot]:
    """
    After selection, reorder within section for narrative flow.
    Logic > pure score order. Soft time continuity inside same stage.
    """
    if len(slots) <= 1:
        return slots

    def stage_of(s: PlanSlot) -> int:
        c = by_id.get(s.clip_id)
        return _primary_stage(c) if c else 3

    if role == "cta":
        # closing recap: selling/fabric first (no price)
        return sorted(
            slots,
            key=lambda s: (
                0
                if by_id.get(s.clip_id)
                and (
                    ClaimType.SELLING_POINT in by_id[s.clip_id].claim_types
                    or ClaimType.FABRIC in by_id[s.clip_id].claim_types
                )
                else 1,
                stage_of(s),
                s.t0_ms,
            ),
        )

    if role == "hook":
        # strongest features first in front 20s; within same strength keep time order
        return sorted(
            slots,
            key=lambda s: (
                -(_hook_strength(by_id[s.clip_id]) if s.clip_id in by_id else s.score),
                stage_of(s),
                s.t0_ms,
            ),
        )

    # trust/body: stage then chronological (less jump-cut)
    return sorted(slots, key=lambda s: (stage_of(s), s.t0_ms))


def _eligible(c: Clip) -> bool:
    """Global keep rules for story plan (fast-paced clothing short)."""
    if c.score <= 0 or _is_pure_filler(c):
        return False
    text = c.text or ""
    # size always out
    if ClaimType.SIZE in c.claim_types or any(p in text for p in _SIZE_TEXT):
        return False
    # price mostly out; welfare phrase may remain as optional opener candidate
    if ClaimType.PRICE in c.claim_types or any(p in text for p in _PRICE_TEXT):
        if not any(w in text for w in _WELFARE_HOOK_WORDS):
            return False
    if _looks_like_live_room(text) and not _is_true_feature(c) and not _is_wear_experience(c):
        return False
    if any(w in text for w in ("调试", "对一下焦", "喝口水", "稍等一下", "卡了", "卡顿")):
        return False
    return True


_PAIN_HOOK_WORDS = (
    "微胖", "显壮", "小个子", "压身高", "显矮", "显廉价", "显土", "闷汗", "出汗",
    "遮肉", "遮肚", "遮胯", "梨形", "胯宽", "显腿粗", "不挑人",
)
_WELFARE_HOOK_WORDS = (
    "清仓", "限时", "限量", "现货", "平替", "专柜", "只要", "直播价", "秒杀", "福利",
)
# First 3s: only high-impact on-body / fabric close-up language
_VISUAL_HOOK_WORDS = (
    "全身", "上身效果", "上身", "显瘦", "收腰", "遮肉", "修身", "版型",
    "面料", "特写", "超软", "垂感", "不透", "凉感", "冰丝", "亲肤",
    "对比", "两色", "成品", "穿上就", "比例", "高腰",
)
_CRAFT_DETAIL_WORDS = (
    "细节", "做工", "蕾丝", "刺绣", "拼接", "走线", "扣子", "拉链", "领口", "腰线",
    "肩线", "肌理", "车线", "滚边", "工艺",
)
_SCENE_WORDS = (
    "适合", "适用", "人群", "微胖", "梨形", "小个子", "大码", "通勤", "日常", "上班",
    "显白", "黄黑皮", "场景", "搭配", "好搭", "夏天", "秋冬", "季节",
)
_OPENING_BAN_WORDS = (
    "大家好", "晚上好", "早上好", "欢迎", "家人们", "老铁", "调试", "对一下", "听得到",
    "准备一下", "来准备", "321", "三二一", "里面去拍", "裏面去拍", "过一下",
)


def _hook_open_score(c: Clip) -> float:
    """Higher = better opener: clothing product features first."""
    text = c.text or ""
    s = 0.0
    # Put garment selling points first (fit / fabric / craft / on-body effect)
    if any(w in text for w in ("版型", "面料", "布料", "材质", "显瘦", "收腰", "遮肉", "修身", "上身")):
        s += 52.0
    if any(
        w in text
        for w in (
            "不走光", "不漏光", "防走光", "不显肚子", "不显肚", "遮肚子", "遮肚",
            "胃包", "拜拜肉", "蝴蝶袖", "副乳", "遮副乳", "不显胯", "收腹",
        )
    ):
        s += 50.0
    if any(w in text for w in ("超软", "软", "垂感", "不透", "凉感", "冰丝", "亲肤", "透气", "不起球", "抗皱")):
        s += 46.0
    if any(w in text for w in ("细节", "蕾丝", "拼接", "做工", "走线", "领口", "腰线")):
        s += 34.0
    if any(w in text for w in _VISUAL_HOOK_WORDS):
        s += 14.0
    # Scene alone is weaker as the absolute first line
    if any(w in text for w in ("适合", "通勤", "日常", "小个子", "梨形", "微胖")):
        s += 8.0 if s >= 30 else 2.0
    if any(w in text for w in _PAIN_HOOK_WORDS):
        s += 6.0 if s >= 30 else 1.0
    if any(w in text for w in _WELFARE_HOOK_WORDS):
        s -= 40.0
    if _looks_like_live_room(text) or any(w in text for w in _OPENING_BAN_WORDS):
        s -= 100.0
    if any(p in text for p in _PRICE_TEXT) or any(p in text for p in _SIZE_TEXT):
        s -= 80.0
    if any(p in text for p in _POLICY_RISK_TEXT):
        s -= 90.0
    s += _hook_strength(c) * 0.30
    try:
        s += learned_text_score(text, for_hook=True) * 0.4
    except Exception:
        pass
    return s


def _logic_order_key(c: Clip) -> tuple:
    """
    Fast-paced clothing short logic (rules fallback for LLM).

    Hard excludes stay in score_clip / _eligible (size/price/ship/live/non-clothing).
    This key only orders *already-eligible* clips:
    3s 吸睛(上身/面料特写) → 全身效果 → 细节做工 → 穿搭场景
    """
    text = c.text or ""
    # 0 = reserved for selected opener; body of film:
    # 1 全身效果, 2 细节做工, 3 穿搭场景, 4 弱体验/其他, 5 纯搭配换装
    if any(w in text for w in ("版型", "显瘦", "收腰", "遮肉", "上身", "全身", "修身", "高腰", "比例")):
        stage = 1
    elif ClaimType.FIT in c.claim_types or (_is_true_feature(c) and ClaimType.SELLING_POINT in c.claim_types):
        stage = 1
    elif any(w in text for w in _CRAFT_DETAIL_WORDS) or ClaimType.DETAIL in c.claim_types:
        stage = 2
    elif ClaimType.FABRIC in c.claim_types or any(
        w in text for w in ("面料", "布料", "材质", "垂感", "透气", "不透", "凉感", "亲肤", "不闷", "软")
    ):
        stage = 2
    elif any(w in text for w in _SCENE_WORDS):
        stage = 3
    elif _is_wear_experience(c):
        stage = 3
    else:
        stage = 4
    if _is_outfit_or_change(c) and stage >= 3:
        stage = max(stage, 4)

    uniq = -_unique_feature_boost(text)
    hook = -_hook_strength(c)
    open_s = -_hook_open_score(c)  # used when picking opener; low stage uses body scores
    try:
        learn = -learned_text_score(text, for_hook=(stage <= 1))
    except Exception:
        learn = 0.0
    price_pen = 20.0 if any(p in text for p in _PRICE_TEXT) else 0.0
    return (stage, open_s, uniq, learn, hook, price_pen, -c.score, c.t0_ms)


def build_timeline_plan(
    clips: list[Clip],
    settings: Settings | None = None,
) -> TimelinePlan:
    """
    Single logical storyline plan (NO forced 黄金/信任/收尾 buckets).

    Select product-useful clips, order by narrative logic + soft time continuity,
    store as one sequence in `golden` with role=story (trust/cta empty for compat).
    """
    settings = settings or Settings()
    scored = score_all(clips)
    by_id = {c.clip_id: c for c in scored}

    speed = getattr(settings, "playback_speed", 1.0) or 1.0
    if speed < 0.8:
        speed = 1.0
    source_target_s = getattr(settings, "source_select_duration_s", settings.target_duration_s)
    target_ms = int(source_target_s) * 1000

    warnings: list[str] = ["policy:logic_storyline"]
    if abs(speed - 1.0) > 0.01:
        warnings.append(f"source_select_for_speed={speed:.2f}x")

    pool = [c for c in scored if _eligible(c)]
    core = [c for c in pool if _is_true_feature(c) or _is_wear_experience(c) or c.score >= 8]
    if len(core) < 4:
        # broaden pool so we can still hit ~60s final after speed
        core = [c for c in pool if c.score > 0] or pool[:]

    min_plan = getattr(settings, "source_min_plan_ms", None)
    max_plan = getattr(settings, "source_max_plan_ms", None)
    if min_plan is None:
        min_plan = int(round(getattr(settings, "min_plan_ms", 50_000) * speed * 1.05))
    if max_plan is None:
        max_plan = int(round(getattr(settings, "max_plan_ms", 65_000) * speed * 1.10))
    # Aim closer to 60s final (source ≈ target * speed).
    # Hard-ish floor: >=50s final after speed (source >= 50s * speed).
    floor_final_ms = 50_000
    aim = max(min_plan, min(max_plan, target_ms))
    soft_min = max(int(aim * 0.90), int(floor_final_ms * speed))

    ordered = sorted(core, key=_logic_order_key)

    selected: list[Clip] = []
    used: set[str] = set()
    total = 0
    last_t1: int | None = None
    selected_texts: list[str] = []

    def _coverage(texts: list[str]) -> dict[str, bool]:
        blob = " ".join(texts)
        return {
            "fit": any(w in blob for w in ("版型", "显瘦", "收腰", "遮肉", "上身", "修身")),
            "fabric": any(w in blob for w in ("面料", "布料", "材质", "垂感", "透气", "不透", "软", "凉感", "亲肤")),
            "audience": any(
                w in blob
                for w in ("适合", "适用", "微胖", "梨形", "小个子", "大码", "姐妹", "通勤", "日常", "显白")
            ),
        }

    # 3s opener: strongest on-body look or fabric close-up (no price / live cues)
    openers = [c for c in ordered if not _is_outfit_or_change(c)]
    openers = sorted(openers, key=_hook_open_score, reverse=True)
    if not openers:
        openers = ordered[:1]
    if openers:
        first = openers[0]
        selected.append(first)
        used.add(first.clip_id)
        total += first.duration_ms
        last_t1 = first.t1_ms
        selected_texts.append(first.text or "")

    max_slots = 40  # more slots so short clips can still sum to >=50s final
    while total < aim and len(selected) < max_slots:
        best = None
        best_key = None
        cov = _coverage(selected_texts)
        for c in ordered:
            if c.clip_id in used:
                continue
            sim = max((_similarity(c.text or "", p) for p in selected_texts), default=0.0)
            if sim >= 0.92 and total > aim * 0.55:
                continue
            # Narrative progress: body → craft → scene (not source stage ids)
            text = c.text or ""
            if any(w in text for w in ("版型", "显瘦", "收腰", "遮肉", "上身", "全身", "修身")):
                narr = 1
            elif any(w in text for w in _CRAFT_DETAIL_WORDS) or any(
                w in text for w in ("面料", "布料", "垂感", "透气", "不透", "软", "材质")
            ):
                narr = 2
            elif any(w in text for w in _SCENE_WORDS):
                narr = 3
            else:
                narr = 4
            stage_pen = 30.0 if (_is_outfit_or_change(c) and total < aim * 0.45) else 0.0
            chrono = 0.0
            if last_t1 is not None:
                gap = c.t0_ms - last_t1
                if 0 <= gap <= 12000:
                    chrono = 28.0 * (1.0 - gap / 12000.0)  # softer chrono; narrative first
                elif 0 <= gap <= 45000:
                    chrono = 10.0 * (1.0 - gap / 45000.0)
                elif gap < 0:
                    chrono = -12.0
                else:
                    chrono = -2.0
            try:
                learn = learned_text_score(c.text or "", for_hook=(total < aim * 0.40)) * 1.8
            except Exception:
                learn = 0.0
            progress = total / max(1, aim)
            # After opener: body (~20–45%) → craft (~45–70%) → scene (~70%+)
            desired = 1 if progress < 0.45 else 2 if progress < 0.70 else 3
            stage_fit = -abs(narr - desired) * 10.0
            cover_boost = 0.0
            if not cov["fit"] and any(w in text for w in ("版型", "显瘦", "收腰", "遮肉", "上身", "全身")):
                cover_boost += 30.0
            if not cov["fabric"] and any(
                w in text for w in ("面料", "布料", "材质", "垂感", "透气", "不透", "软", "细节", "蕾丝")
            ):
                cover_boost += 28.0
            if not cov["audience"] and any(
                w in text
                for w in (
                    "适合", "适用", "微胖", "梨形", "小个子", "大码", "通勤", "日常", "显白",
                    "黄黑皮", "黑皮", "白皮", "皮肤", "姐妹可以穿", "胯宽", "肚子", "上班", "场景",
                )
            ):
                cover_boost += 32.0
            key = (
                c.score
                + learn
                + chrono
                + stage_fit
                + cover_boost
                + _hook_strength(c) * (0.35 if total < aim * 0.4 else 0.12)
                - stage_pen
                - sim * 20.0
            )
            if best is None or key > best_key:  # type: ignore[operator]
                best, best_key = c, key
        if best is None:
            break
        selected.append(best)
        used.add(best.clip_id)
        total += best.duration_ms
        last_t1 = best.t1_ms
        selected_texts.append(best.text or "")

    def _duration_fill_ok(c: Clip) -> bool:
        """Pad duration only with clothing-useful lines; never live/price/size."""
        if not _eligible(c) or c.score <= 0:
            return False
        # Prefer true product content over weak leftovers
        return _is_true_feature(c) or _is_wear_experience(c) or c.score >= 6

    # Keep filling until soft_min (>=50s final source) with clothing leftovers only
    if total < soft_min:
        leftovers = sorted(
            [c for c in ordered if c.clip_id not in used and _duration_fill_ok(c)],
            key=lambda c: (-c.score, c.t0_ms),
        )
        for c in leftovers:
            if total >= soft_min or len(selected) >= max_slots:
                break
            if any(_similarity(c.text or "", p) >= 0.95 for p in selected_texts) and total > soft_min * 0.85:
                continue
            selected.append(c)
            used.add(c.clip_id)
            total += c.duration_ms
            selected_texts.append(c.text or "")

    # Final push: expand to eligible pool until floor (still clothing-only)
    if total < soft_min:
        leftovers = sorted(
            [c for c in pool if c.clip_id not in used and _duration_fill_ok(c)],
            key=lambda c: (-c.score, c.t0_ms),
        )
        for c in leftovers:
            if total >= soft_min or len(selected) >= max_slots:
                break
            if any(_similarity(c.text or "", p) >= 0.96 for p in selected_texts):
                continue
            selected.append(c)
            used.add(c.clip_id)
            total += c.duration_ms
            selected_texts.append(c.text or "")

    if total < min_plan:
        warnings.append(f"short_source_ms={total}")
        leftovers = [
            c for c in pool if c.clip_id not in used and _duration_fill_ok(c)
        ]
        for c in leftovers:
            if total >= soft_min or len(selected) >= max_slots:
                break
            if any(_similarity(c.text or "", p) >= 0.96 for p in selected_texts):
                continue
            selected.append(c)
            used.add(c.clip_id)
            total += c.duration_ms
            selected_texts.append(c.text or "")
    if total < soft_min:
        warnings.append(f"under_50s_floor_source_ms={total}")

    if len(selected) >= 2:
        head = selected[0]
        rest = sorted(selected[1:], key=lambda c: (_logic_order_key(c)[0], c.t0_ms))
        selected = [head, *rest]

    def _plan_ms(items: list[Clip]) -> int:
        return sum(c.duration_ms for c in items)

    while _plan_ms(selected) > max_plan and len(selected) > 3:
        # prefer dropping incomplete / outfit crumbs, keep opener + closer
        def drop_key(i: int) -> tuple:
            c = selected[i]
            t = c.text or ""
            incomplete = 1 if t.endswith(("然后", "因为", "所以", "而且", "但是", "的话", "的")) else 0
            return (incomplete, 1 if _is_outfit_or_change(c) else 0, -c.score)

        drop_i = max(range(1, len(selected) - 1), key=drop_key) if len(selected) > 2 else len(selected) - 1
        selected.pop(drop_i)

    # complete dangling last thought with next nearby clip if needed
    if selected:
        last_t = (selected[-1].text or "").strip()
        if last_t.endswith(("然后", "因为", "所以", "而且", "但是", "的话", "你看", "还有")):
            for c in ordered:
                if c.clip_id in used:
                    continue
                if 0 <= c.t0_ms - selected[-1].t1_ms <= 8000 and not _is_outfit_or_change(c):
                    selected.append(c)
                    used.add(c.clip_id)
                    break

    story = [_to_slot(c, "story") for c in selected]
    # merge adjacent tiny modules that are almost continuous (avoid choppy hard stops)
    # Same-topic only — don't glue 面料+版型 into one module.
    def _coarse_topic(text: str) -> str:
        t = text or ""
        if any(w in t for w in ("版型", "显瘦", "收腰", "遮肉", "上身", "全身", "修身", "高腰", "比例")):
            return "fit"
        if any(w in t for w in ("面料", "布料", "材质", "垂感", "透气", "不透", "软", "凉感", "亲肤", "闷")):
            return "fabric"
        if any(w in t for w in _CRAFT_DETAIL_WORDS):
            return "detail"
        if any(w in t for w in _SCENE_WORDS):
            return "audience"
        return "other"

    merged: list[PlanSlot] = []
    for s in story:
        if not merged:
            merged.append(s)
            continue
        prev = merged[-1]
        gap = s.t0_ms - prev.t1_ms
        prev_dur = prev.t1_ms - prev.t0_ms
        cur_dur = s.t1_ms - s.t0_ms
        prev_incomplete = (prev.text or "").endswith(("然后", "因为", "所以", "而且", "但是", "的话", "的", "了"))
        same_topic = _coarse_topic(prev.text or "") == _coarse_topic(s.text or "")
        # merge close fragments / incomplete tails into one natural module
        if (
            same_topic
            and 0 <= gap <= 650
            and (prev_incomplete or prev_dur < 3000 or cur_dur < 2800)
            and (prev_dur + cur_dur + gap) <= 11000
        ):
            prev.t1_ms = max(prev.t1_ms, s.t1_ms)
            if s.text and s.text not in (prev.text or ""):
                joiner = "" if (prev.text or "").endswith(("，", "。", "！", "？", ",", ".")) else "，"
                prev.text = f"{prev.text}{joiner}{s.text}"
            prev.score = max(prev.score, s.score)
            continue
        merged.append(s)
    story = merged

    # never end on incomplete text
    if story:
        end = (story[-1].text or "").strip()
        if end.endswith(("然后", "因为", "所以", "而且", "但是", "的话", "你看", "还有", "的", "了")) and len(story) > 2:
            story.pop()
            warnings.append("dropped_incomplete_tail")

    # Bundle same-topic clauses so selling points aren't interleaved
    # (e.g. fabric → fit → fabric). Prefer llm_plan helper when available.
    try:
        from clipper.llm_plan import _cluster_slots_by_topic

        clustered = _cluster_slots_by_topic(story)
        if clustered and len(clustered) == len(story):
            story = clustered
            warnings.append("policy:topic_blocks_together")
    except Exception:
        # Lightweight local fallback: group by coarse narrative stage
        if len(story) > 2:
            opener = story[0]
            rest = story[1:]

            def _topic_rank(s: PlanSlot) -> int:
                t = s.text or ""
                if any(w in t for w in ("版型", "显瘦", "收腰", "遮肉", "上身", "全身", "修身", "高腰", "比例")):
                    return 0
                if any(w in t for w in ("面料", "布料", "材质", "垂感", "透气", "不透", "软", "凉感", "亲肤", "闷")):
                    return 1
                if any(w in t for w in _CRAFT_DETAIL_WORDS):
                    return 2
                if any(w in t for w in _SCENE_WORDS):
                    return 3
                return 4

            rest = sorted(rest, key=lambda s: (_topic_rank(s), s.t0_ms))
            story = [opener, *rest]
            warnings.append("policy:topic_blocks_together")

    total = sum(s.t1_ms - s.t0_ms for s in story)
    # Do NOT stretch t1_ms past real speech just to hit target duration.
    # Extending tails pulls in silence / blank stage and looks like 1–2s black gaps
    # between "segments" after concat (especially at 1.4x playback).
    if total < min_plan and story:
        warnings.append(f"short_but_complete_ms={total}")
        warnings.append(f"short_content_ms={total}")
    warnings.append("policy:complete_logic_no_cutoff")

    front_ms = int(round(settings.golden_s * 1000 * speed))
    acc = 0
    front_has_feature = False
    for s in story:
        acc += s.t1_ms - s.t0_ms
        c = by_id.get(s.clip_id)
        if c and (_is_true_feature(c) or _is_wear_experience(c)):
            front_has_feature = True
        if acc >= front_ms:
            break
    golden20_passed = bool(story) and front_has_feature
    if not story:
        warnings.append("no_story_clips")
    warnings.append("policy:size_excluded")
    warnings.append("policy:de_live_room_feel")
    warnings.append("policy:logic_over_sections")
    warnings.append("policy:reverse_cut_learning")

    ratio = 1.0 if story else 0.0
    return TimelinePlan(
        target_duration_s=settings.target_duration_s,
        golden=story,
        trust=[],
        cta=[],
        total_duration_ms=total,
        golden_weight_ratio=ratio,
        golden20_passed=golden20_passed,
        warnings=warnings,
    )
