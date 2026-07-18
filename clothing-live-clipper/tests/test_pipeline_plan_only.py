from pathlib import Path

from clipper.pipeline import run_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


def test_plan_only_pipeline(tmp_path: Path):
    out = tmp_path / "out"
    result = run_pipeline(
        video=None,
        transcript_path=FIXTURE,
        out_dir=out,
        render=False,
    )
    assert (out / "plan.json").exists()
    assert (out / "review.md").exists()
    assert result.plan is not None
    assert result.plan.golden
    assert result.meta.get("render_skipped") is True
