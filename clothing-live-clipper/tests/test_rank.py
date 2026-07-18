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
    assert any(k in golden_text for k in ("显瘦", "闭眼入", "醋酸", "收腰", "券后"))


def test_plan_has_three_sections_when_enough_material():
    clips = _clips_from_fixture()
    plan = build_timeline_plan(clips, Settings(target_duration_s=60))
    assert plan.golden
    assert plan.trust or plan.cta
    assert plan.total_duration_ms > 0
    assert plan.golden20_passed is True
