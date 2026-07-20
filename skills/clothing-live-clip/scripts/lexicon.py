# clothing-live-clip/scripts/lexicon.py
from __future__ import annotations
from pathlib import Path

_FALLBACK_SIZE = [
    "尺码", "选码", "偏大", "偏小", "胸围", "腰围", "臀围", "身高",
    "穿M", "穿S", "穿L", "穿XL", "均码", "加大码", "码数", "建议穿",
]
_FALLBACK_SENTIMENT = [
    "做了五年", "不容易", "感谢陪伴", "创业", "初心", "故事是这样",
    "一路走来", "谢谢支持我", "喜欢我的人",
]
_FALLBACK_CHITCHAT = [
    "家人们", "老铁们", "听得到吗", "扣1", "扣一", "点点关注",
    "双击", "晚上好啊", "来了吗",
]


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_lexicon_section(body: str) -> list[str]:
    words: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        for part in line.split(","):
            w = part.strip()
            if w:
                words.append(w)
    return words


def load_lexicon(root: Path | None = None) -> tuple[list[str], list[str], list[str]]:
    root = root or skill_root()
    path = root / "assets" / "exclude-lexicon.md"
    size, sentiment, chitchat = list(_FALLBACK_SIZE), list(_FALLBACK_SENTIMENT), list(_FALLBACK_CHITCHAT)
    if not path.is_file():
        return size, sentiment, chitchat

    raw = path.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal current, buf
        if current is not None:
            sections[current] = _parse_lexicon_section("\n".join(buf))
        current = None
        buf = []

    for line in raw.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip().lower()
            if heading.startswith("size"):
                current = "size"
            elif heading.startswith("sentiment"):
                current = "sentiment"
            elif heading.startswith("chitchat"):
                current = "chitchat"
            else:
                current = None
            buf = []
        elif current is not None:
            buf.append(line)
    flush()

    if sections.get("size"):
        size = sections["size"]
    if sections.get("sentiment"):
        sentiment = sections["sentiment"]
    if sections.get("chitchat"):
        chitchat = sections["chitchat"]
    return size, sentiment, chitchat
