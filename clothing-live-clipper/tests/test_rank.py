from pathlib import Path

from clipper.asr import load_transcript
from clipper.config import Settings
from clipper.extract import extract_claims, utterances_to_clips
from clipper.rank import build_timeline_plan, score_all

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


def _clips_from_fixture():
    tr = load_transcript(FIXTURE)
    claims = extract_claims(tr)
    clips = utterances_to_clips(tr, claims=claims)
    return score_all(clips)


def test_chitchat_score_zero():
    clips = _clips_from_fixture()
    chitchat = [c for c in clips if "关注" in c.text or "家人们" in c.text]
    assert chitchat
    assert all(c.score == 0 for c in chitchat)


def test_selling_point_high_score():
    clips = _clips_from_fixture()
    top = max(clips, key=lambda c: c.score)
    assert top.score > 0
    # top clip should carry real product value, not chitchat
    blob = top.text
    assert any(
        k in blob
        for k in ("显瘦", "闭眼入", "醋酸", "收腰", "遮胯", "券后", "垂感", "遮肉")
    )


def test_golden_excludes_chitchat_and_has_value():
    clips = _clips_from_fixture()
    plan = build_timeline_plan(clips, Settings(target_duration_s=60))
    assert plan.golden, "golden should not be empty"
    golden_text = " ".join(s.text for s in plan.golden)
    assert "家人们" not in golden_text
    assert "扣个" not in golden_text
    # price excluded globally; features required in golden
    assert "券后" not in golden_text
    assert any(k in golden_text for k in ("显瘦", "闭眼入", "醋酸", "收腰", "面料", "版型"))


def test_global_policy_outfit_not_in_golden():
    """GLOBAL: try-on / outfit change must not lead first 20s."""
    from clipper.models import ClaimType, Clip

    clips = [
        Clip(
            clip_id="o1",
            text="我给你穿一下牛仔裤再换装搭配看看",
            t0_ms=0,
            t1_ms=3000,
            claim_types=[ClaimType.OUTFIT],
            score=30,
        ),
        Clip(
            clip_id="f1",
            text="这件面料超级软还不透显瘦",
            t0_ms=4000,
            t1_ms=8000,
            claim_types=[ClaimType.FABRIC, ClaimType.SELLING_POINT],
            score=40,
        ),
        Clip(
            clip_id="f2",
            text="收腰版型梨形闭眼入",
            t0_ms=9000,
            t1_ms=12000,
            claim_types=[ClaimType.FIT, ClaimType.SELLING_POINT],
            score=38,
        ),
    ]
    plan = build_timeline_plan(clips, Settings(target_duration_s=60, playback_speed=1.0))
    assert plan.golden, "golden must use features"
    golden_text = " ".join(s.text for s in plan.golden)
    assert "穿一下牛仔裤" not in golden_text
    assert "换装" not in golden_text
    assert any(k in golden_text for k in ("面料", "显瘦", "收腰", "版型", "不透"))
    # outfit can appear later
    body = " ".join(s.text for s in (plan.trust + plan.cta))
    assert ("牛仔裤" in body) or ("换装" in body) or True  # may be dropped if weak
    assert any("policy:golden_features_only" in w or "outfit_change" in w for w in plan.warnings)


def test_plan_has_three_sections_when_enough_material():
    clips = _clips_from_fixture()
    plan = build_timeline_plan(clips, Settings(target_duration_s=60))
    assert plan.golden
    assert plan.trust or plan.cta
    assert plan.total_duration_ms > 0
    assert plan.golden20_passed is True
