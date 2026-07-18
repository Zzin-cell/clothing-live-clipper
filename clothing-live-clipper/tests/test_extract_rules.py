from pathlib import Path

from clipper.asr import load_transcript
from clipper.extract import extract_claims, tag_utterance
from clipper.models import ClaimType, TranscriptUtterance

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


def test_tag_selling_and_fit():
    utt = TranscriptUtterance(
        utt_id="1",
        text="收腰版型特别显瘦，梨形身材闭眼入",
        t0_ms=0,
        t1_ms=3000,
    )
    types = {c.type for c in tag_utterance(utt)}
    assert ClaimType.SELLING_POINT in types
    assert ClaimType.FIT in types


def test_chitchat_only():
    utt = TranscriptUtterance(
        utt_id="2",
        text="家人们来了扣个1点点关注",
        t0_ms=0,
        t1_ms=2000,
    )
    claims = tag_utterance(utt)
    assert len(claims) == 1
    assert claims[0].type == ClaimType.CHITCHAT


def test_price_tag():
    utt = TranscriptUtterance(
        utt_id="3",
        text="原价299券后只要129",
        t0_ms=0,
        t1_ms=2000,
    )
    types = {c.type for c in tag_utterance(utt)}
    assert ClaimType.PRICE in types


def test_livestream_cta_price_keywords():
    """专属直播挂车/链接话术应识别为 price，而非 chitchat。"""
    samples = [
        "喜欢的去1号链接下单",
        "小黄车拍起来库存不多了",
        "2号链接同款加购",
        "点击下方购物车领券",
        "弹窗已上速度拍",
    ]
    for text in samples:
        utt = TranscriptUtterance(utt_id="cta", text=text, t0_ms=0, t1_ms=2000)
        types = {c.type for c in tag_utterance(utt)}
        assert ClaimType.PRICE in types, f"expected PRICE for: {text}"
        assert ClaimType.CHITCHAT not in types, f"unexpected CHITCHAT for: {text}"


def test_fixture_extract_has_core_claims():
    tr = load_transcript(FIXTURE)
    claims = extract_claims(tr)
    types = {c.type for c in claims}
    assert ClaimType.SELLING_POINT in types
    assert ClaimType.FABRIC in types
    assert ClaimType.PRICE in types
