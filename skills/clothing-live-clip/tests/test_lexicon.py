# clothing-live-clip/tests/test_lexicon.py
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lexicon import load_lexicon, skill_root


def test_skill_root_points_to_package():
    assert (skill_root() / "assets" / "exclude-lexicon.md").is_file()


def test_load_lexicon_has_size_markers():
    size, sentiment, chitchat = load_lexicon()
    assert "尺码" in size
    assert "建议穿" in size
    assert "感谢陪伴" in sentiment
    assert "家人们" in chitchat


def test_cta_not_in_exclude_lists():
    size, sentiment, chitchat = load_lexicon()
    banned = set(size + sentiment + chitchat)
    for w in ["小黄车", "加购", "下单", "弹窗", "购物车"]:
        assert w not in banned
