from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clipper.config import Settings
from clipper.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clipper",
        description="服装带货直播智能切片 — 简单可用版（黄金20秒重排 + 约60秒成片）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="根据转写生成切片计划并可选渲染成片")
    run.add_argument("--video", type=str, default=None, help="源视频路径（可选，仅 plan 可不传）")
    run.add_argument(
        "--transcript",
        type=str,
        required=True,
        help="转写文件 .json 或 .srt（必填）",
    )
    run.add_argument("--out", type=str, required=True, help="输出目录")
    run.add_argument(
        "--no-render",
        action="store_true",
        help="只生成 plan.json / review.md，不调用 ffmpeg",
    )
    run.add_argument("--target-seconds", type=int, default=60, help="目标成片秒数，默认60")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        settings = Settings.from_env()
        # dataclass is frozen — build new
        settings = Settings(
            target_duration_s=args.target_seconds,
            golden_s=min(20, max(8, args.target_seconds // 3)),
            cta_s=min(10, max(5, args.target_seconds // 6)),
            min_clip_ms=settings.min_clip_ms,
            max_clip_ms=settings.max_clip_ms,
            min_plan_ms=settings.min_plan_ms,
            max_plan_ms=settings.max_plan_ms,
            playback_speed=settings.playback_speed,
            golden_weight_ratio=settings.golden_weight_ratio,
            # GLOBAL policy — all CLI jobs
            golden_features_only=settings.golden_features_only,
            demote_outfit_change_from_golden=settings.demote_outfit_change_from_golden,
            exclude_price_from_cut=settings.exclude_price_from_cut,
            clothing_only=settings.clothing_only,
            llm_api_key=settings.llm_api_key,
            llm_base_url=settings.llm_base_url,
            llm_model=settings.llm_model,
        )
        video = args.video
        if video and not Path(video).exists():
            print(f"[error] video not found: {video}", file=sys.stderr)
            return 2
        if not Path(args.transcript).exists():
            print(f"[error] transcript not found: {args.transcript}", file=sys.stderr)
            return 2

        result = run_pipeline(
            video=video,
            transcript_path=args.transcript,
            out_dir=args.out,
            settings=settings,
            render=not args.no_render,
        )
        plan = result.plan
        print("=== clothing clipper MVP ===")
        print(f"out: {args.out}")
        if plan:
            print(f"clips selected: {len(plan.all_slots())}")
            print(f"duration_s: {plan.total_duration_ms/1000:.1f}")
            print(f"golden20_passed: {plan.golden20_passed}")
            print(f"warnings: {plan.warnings}")
        if result.output_mp4:
            print(f"final: {result.output_mp4}")
        elif result.meta.get("render_skipped"):
            print(f"render skipped: {result.meta.get('render_error', 'flag')}")
        print("done.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
