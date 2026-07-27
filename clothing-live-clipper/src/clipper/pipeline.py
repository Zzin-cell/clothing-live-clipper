from __future__ import annotations

import json
from pathlib import Path

from clipper.asr import load_transcript
from clipper.config import Settings
from clipper.extract import extract_claims, split_long_utterance, utterances_to_clips
from clipper.media import render_plan, which_ffmpeg
from clipper.models import JobResult, TimelinePlan
from clipper.rank import build_timeline_plan, score_all


def run_pipeline(
    *,
    video: str | Path | None,
    transcript_path: str | Path,
    out_dir: str | Path,
    settings: Settings | None = None,
    render: bool = True,
) -> JobResult:
    settings = settings or Settings.from_env()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = load_transcript(transcript_path)
    transcript = []
    for u in raw:
        transcript.extend(split_long_utterance(u))

    claims = extract_claims(transcript)
    clips = utterances_to_clips(
        transcript,
        claims=claims,
        min_clip_ms=settings.min_clip_ms,
        max_clip_ms=settings.max_clip_ms,
    )
    clips = score_all(clips)
    plan: TimelinePlan = build_timeline_plan(clips, settings)

    # persist
    (out_dir / "transcript.json").write_text(
        json.dumps([u.model_dump() for u in transcript], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "claims.json").write_text(
        json.dumps([c.model_dump() for c in claims], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "clips.json").write_text(
        json.dumps([c.model_dump() for c in clips], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "plan.json").write_text(
        plan.model_dump_json(indent=2),
        encoding="utf-8",
    )

    # human review
    speed = float(getattr(settings, "playback_speed", 1.0) or 1.0)
    est_final_s = (plan.total_duration_ms / 1000.0) / speed if speed > 0 else plan.total_duration_ms / 1000.0
    review_lines = [
        f"# Clip review",
        f"",
        f"- target_final: {settings.target_duration_s}s",
        f"- playback_speed: {speed:.2f}x",
        f"- source_plan: {plan.total_duration_ms/1000:.1f}s",
        f"- estimated_final: {est_final_s:.1f}s",
        f"- golden20_passed: {plan.golden20_passed}",
        f"- golden_weight_ratio: {plan.golden_weight_ratio:.2f}",
        f"- warnings: {', '.join(plan.warnings) if plan.warnings else '(none)'}",
        f"",
        f"## Golden (source, ~0-{int(settings.golden_s * speed)}s before speed → ~0-20s final)",
    ]
    for s in plan.golden:
        review_lines.append(f"- [{s.t0_ms}-{s.t1_ms}] ({s.score:.0f}) {s.text}")
    review_lines.append("")
    review_lines.append("## Trust")
    for s in plan.trust:
        review_lines.append(f"- [{s.t0_ms}-{s.t1_ms}] ({s.score:.0f}) {s.text}")
    review_lines.append("")
    review_lines.append("## CTA")
    for s in plan.cta:
        review_lines.append(f"- [{s.t0_ms}-{s.t1_ms}] ({s.score:.0f}) {s.text}")
    (out_dir / "review.md").write_text("\n".join(review_lines), encoding="utf-8")

    output_mp4 = None
    meta: dict = {"render_skipped": False}
    if render and video:
        if not which_ffmpeg():
            meta["render_skipped"] = True
            meta["render_error"] = "ffmpeg not found"
        elif not plan.all_slots():
            meta["render_skipped"] = True
            meta["render_error"] = "empty plan"
        else:
            segs = [(s.t0_ms, s.t1_ms) for s in plan.all_slots()]
            speed = getattr(settings, "playback_speed", 1.4) or 1.0
            # Default auto path = draft (fast preview). Export can re-render final.
            import shutil

            preview_path = out_dir / "preview.mp4"
            final_path = out_dir / "final.mp4"
            render_plan(
                video,
                segs,
                preview_path,
                work_dir=out_dir / "_parts_draft",
                smooth=True,
                crossfade_s=0.0,
                playback_speed=float(speed),
                profile="draft",
                reuse_parts=True,
            )
            # Back-compat: UI/download still use final.mp4 until export upgrades quality
            try:
                shutil.copy2(preview_path, final_path)
            except Exception:
                final_path = preview_path
            output_mp4 = str(final_path)
            meta["playback_speed"] = float(speed)
            meta["source_plan_ms"] = int(plan.total_duration_ms)
            meta["render_profile"] = "draft"
            meta["has_preview"] = preview_path.exists()
    elif render and not video:
        meta["render_skipped"] = True
        meta["render_error"] = "no video provided (plan-only mode)"
    else:
        meta["render_skipped"] = True

    result = JobResult(
        video=str(video) if video else None,
        transcript=transcript,
        claims=claims,
        clips=clips,
        plan=plan,
        output_mp4=output_mp4,
        meta=meta,
    )
    (out_dir / "result.json").write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return result
