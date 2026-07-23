"""Local auto worker: video-only job → ASR → filter → clipper (no Agent)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]

# ensure project scripts importable
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

_ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if _ffbin.exists():
    os.environ["PATH"] = str(_ffbin) + os.pathsep + os.environ.get("PATH", "")

_lock = threading.Lock()
_running: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_meta(job_dir: Path, meta: dict[str, Any]) -> None:
    (job_dir / "job_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_meta(job_dir: Path) -> dict[str, Any]:
    p = job_dir / "job_meta.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _set_progress(job_dir: Path, stage: str, pct: int, detail: str = "") -> None:
    meta = _read_meta(job_dir)
    meta["status"] = "processing"
    meta["stage"] = stage
    meta["progress"] = max(0, min(100, int(pct)))
    meta["stage_detail"] = detail
    meta["updated_at"] = _utc_now()
    _write_meta(job_dir, meta)


def _find_video(job_dir: Path) -> Path | None:
    uploads = job_dir / "uploads"
    if not uploads.exists():
        return None
    for p in uploads.iterdir():
        if p.is_file() and p.suffix.lower() in {
            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
            ".m4v",
            ".ts",
            ".mts",
            ".m2ts",
        }:
            return p
    return None


def process_job_dir(job_dir: Path) -> None:
    """Blocking full pipeline for one job directory."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    meta = _read_meta(job_dir)
    try:
        video = _find_video(job_dir)
        if not video:
            raise RuntimeError("未找到上传视频")

        target = int(meta.get("target_seconds") or 60)
        render = bool(meta.get("render_requested", True))
        speed = float(meta.get("playback_speed") or os.environ.get("CLIPPER_PLAYBACK_SPEED") or 1.3)

        meta["status"] = "processing"
        meta["worker"] = "local_auto"
        meta["started_at"] = _utc_now()
        meta["error"] = None
        _write_meta(job_dir, meta)

        # Import local pipeline pieces
        from filter_transcript_v2 import filter_for_duration  # type: ignore

        from clipper.config import Settings
        from clipper.pipeline import run_pipeline

        # 1) extract + ASR via agent_clip helpers
        _set_progress(job_dir, "extract_audio", 8, "抽取音频")
        sys.path.insert(0, str(ROOT / "scripts"))
        from agent_clip_video import asr_local, extract_wav, resolve_local_model  # type: ignore

        work = job_dir / "asr_work"
        wav = work / "audio_16k.wav"
        extract_wav(video, wav)

        model_name = resolve_local_model()
        _set_progress(job_dir, "asr", 25, f"高精度口播打轴 ({model_name})，首次加载/听写可能需几分钟")
        # heartbeat: if asr takes long, UI still shows activity
        import time as _time

        t_asr0 = _time.time()
        raw = asr_local(wav)
        raw_path = job_dir / "transcript_asr.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = _read_meta(job_dir)
        meta["asr_model"] = str(model_name)
        meta["asr_segments"] = len(raw)
        meta["asr_seconds"] = round(_time.time() - t_asr0, 1)
        _write_meta(job_dir, meta)
        _set_progress(job_dir, "asr_done", 40, f"听写完成 {len(raw)} 句 · {meta['asr_seconds']}s")

        _set_progress(job_dir, "filter", 45, "过滤无效/非服装内容")
        # source length for 1.3x → ~60s final
        sp = speed if speed > 0 else 1.0
        kept = filter_for_duration(
            raw,
            target_ms=int(78_000 * sp / 1.3),
            min_ms=int(72_000 * sp / 1.3),
            max_ms=int(85_000 * sp / 1.3),
        )
        tr_path = job_dir / "transcript_for_clipper.json"
        tr_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

        _set_progress(job_dir, "clipper", 65, "卖点排序与时间轴")
        settings = Settings.from_env()
        # rebuild settings with target + speed
        settings = Settings(
            target_duration_s=target,
            golden_s=settings.golden_s,
            cta_s=settings.cta_s,
            min_clip_ms=settings.min_clip_ms,
            max_clip_ms=settings.max_clip_ms,
            min_plan_ms=settings.min_plan_ms,
            max_plan_ms=settings.max_plan_ms,
            playback_speed=sp,
            golden_weight_ratio=settings.golden_weight_ratio,
            # always inherit global product policy
            golden_features_only=settings.golden_features_only,
            demote_outfit_change_from_golden=settings.demote_outfit_change_from_golden,
            exclude_price_from_cut=settings.exclude_price_from_cut,
            clothing_only=settings.clothing_only,
            llm_api_key=settings.llm_api_key,
            llm_base_url=settings.llm_base_url,
            llm_model=settings.llm_model,
        )

        _set_progress(job_dir, "render", 80, "渲染成片" if render else "仅生成计划")
        result = run_pipeline(
            video=video,
            transcript_path=tr_path,
            out_dir=job_dir,
            settings=settings,
            render=render,
        )

        has_plan = (job_dir / "plan.json").exists()
        has_final = (job_dir / "final.mp4").exists()
        if has_plan and has_final:
            status = "success"
        elif has_plan:
            status = "success_partial"
        else:
            status = "failed"

        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": status,
                "stage": "done" if status != "failed" else "failed",
                "progress": 100 if status != "failed" else meta.get("progress", 90),
                "finished_at": _utc_now(),
                "has_video": True,
                "has_final": has_final,
                "output_mp4": has_final,
                "transcript_source": "faster_whisper_local",
                "worker": "local_auto",
                "playback_speed": sp,
                "selected_clips": len(result.plan.all_slots()) if result.plan else 0,
                "golden20_passed": bool(result.plan.golden20_passed) if result.plan else False,
                "duration_s": (result.plan.total_duration_ms / 1000.0) if result.plan else 0,
                "warnings": result.plan.warnings if result.plan else [],
                "error": None if status != "failed" else "未生成 plan/final",
            }
        )
        # final duration if available
        if has_final:
            try:
                from clipper.media import probe_duration_ms

                meta["final_duration_s"] = round(probe_duration_ms(job_dir / "final.mp4") / 1000.0, 2)
            except Exception:
                pass
        _write_meta(job_dir, meta)
    except Exception as e:
        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": "failed",
                "stage": "failed",
                "progress": meta.get("progress", 0),
                "error": str(e),
                "traceback": traceback.format_exc()[-2000:],
                "finished_at": _utc_now(),
                "worker": "local_auto",
            }
        )
        _write_meta(job_dir, meta)
    finally:
        with _lock:
            _running.discard(job_id)


def reclip_from_saved_transcript(job_dir: Path) -> None:
    """Re-run clipper using existing transcript_for_clipper.json (skip ASR)."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    meta = _read_meta(job_dir)
    try:
        video = _find_video(job_dir)
        if not video:
            raise RuntimeError("未找到上传视频")
        tr_path = job_dir / "transcript_for_clipper.json"
        if not tr_path.exists():
            raise RuntimeError("未找到可重剪的口播稿 transcript_for_clipper.json")

        target = int(meta.get("target_seconds") or 60)
        render = bool(meta.get("render_requested", True))
        speed = float(meta.get("playback_speed") or os.environ.get("CLIPPER_PLAYBACK_SPEED") or 1.3)

        _set_progress(job_dir, "reclip", 55, "按口播稿重新切片")
        from clipper.config import Settings
        from clipper.pipeline import run_pipeline

        base = Settings.from_env()
        settings = Settings(
            target_duration_s=target,
            golden_s=base.golden_s,
            cta_s=base.cta_s,
            min_clip_ms=base.min_clip_ms,
            max_clip_ms=base.max_clip_ms,
            min_plan_ms=base.min_plan_ms,
            max_plan_ms=base.max_plan_ms,
            playback_speed=speed if speed > 0 else 1.3,
            golden_weight_ratio=base.golden_weight_ratio,
            golden_features_only=base.golden_features_only,
            demote_outfit_change_from_golden=base.demote_outfit_change_from_golden,
            exclude_price_from_cut=base.exclude_price_from_cut,
            clothing_only=base.clothing_only,
            llm_api_key=base.llm_api_key,
            llm_base_url=base.llm_base_url,
            llm_model=base.llm_model,
        )
        _set_progress(job_dir, "render", 80, "渲染成片" if render else "仅生成计划")
        # clear old final so new render is obvious
        final_path = job_dir / "final.mp4"
        if final_path.exists():
            try:
                final_path.unlink()
            except OSError:
                pass
        result = run_pipeline(
            video=video,
            transcript_path=tr_path,
            out_dir=job_dir,
            settings=settings,
            render=render,
        )
        has_plan = (job_dir / "plan.json").exists()
        has_final = (job_dir / "final.mp4").exists()
        status = (
            "success"
            if has_plan and has_final
            else ("success_partial" if has_plan else "failed")
        )
        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": status,
                "stage": "done" if status != "failed" else "failed",
                "progress": 100 if status != "failed" else 90,
                "finished_at": _utc_now(),
                "has_final": has_final,
                "output_mp4": has_final,
                "worker": "local_reclip",
                "render_token": _utc_now(),
                "selected_clips": len(result.plan.all_slots()) if result.plan else 0,
                "golden20_passed": bool(result.plan.golden20_passed) if result.plan else False,
                "duration_s": (result.plan.total_duration_ms / 1000.0) if result.plan else 0,
                "warnings": result.plan.warnings if result.plan else [],
                "error": None if status != "failed" else "重剪失败：未生成 plan/final",
            }
        )
        if has_final:
            try:
                from clipper.media import probe_duration_ms

                meta["final_duration_s"] = round(
                    probe_duration_ms(job_dir / "final.mp4") / 1000.0, 2
                )
            except Exception:
                pass
        _write_meta(job_dir, meta)
    except Exception as e:
        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": "failed",
                "stage": "failed",
                "error": str(e),
                "traceback": traceback.format_exc()[-2000:],
                "finished_at": _utc_now(),
                "worker": "local_reclip",
            }
        )
        _write_meta(job_dir, meta)
    finally:
        with _lock:
            _running.discard(job_id)


def start_job_async(job_dir: Path) -> bool:
    """Start background thread if not already running this job."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    with _lock:
        if job_id in _running:
            return False
        _running.add(job_id)
    t = threading.Thread(target=process_job_dir, args=(job_dir,), daemon=True)
    t.start()
    return True


def start_reclip_async(job_dir: Path) -> bool:
    """Reclip using saved transcript without ASR."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    with _lock:
        if job_id in _running:
            return False
        _running.add(job_id)
    t = threading.Thread(target=reclip_from_saved_transcript, args=(job_dir,), daemon=True)
    t.start()
    return True


def render_from_plan_only(job_dir: Path) -> None:
    """Render final.mp4 from existing plan.json segments (manual structure edit)."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    meta = _read_meta(job_dir)
    try:
        video = _find_video(job_dir)
        if not video:
            raise RuntimeError("未找到上传视频")
        plan_path = job_dir / "plan.json"
        if not plan_path.exists():
            raise RuntimeError("未找到 plan.json")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        segs = []
        seen: set[tuple[int, int]] = set()
        for key in ("golden", "trust", "cta"):
            for s in plan.get(key) or []:
                if not isinstance(s, dict):
                    continue
                if s.get("removed") is True:
                    continue
                t0 = int(s.get("t0_ms") or 0)
                t1 = int(s.get("t1_ms") or 0)
                if t1 <= t0:
                    continue
                w = (t0, t1)
                if w in seen:
                    continue
                seen.add(w)
                segs.append(w)
        if not segs:
            raise RuntimeError("plan 无有效片段")

        speed = float(meta.get("playback_speed") or os.environ.get("CLIPPER_PLAYBACK_SPEED") or 1.3)
        _set_progress(job_dir, "render", 75, f"按调整后的结构渲染（{len(segs)}段）")
        from clipper.media import probe_duration_ms, render_plan

        # clean old render artifacts so browser/file size always changes
        final_path = job_dir / "final.mp4"
        parts_dir = job_dir / "_parts"
        if final_path.exists():
            try:
                final_path.unlink()
            except OSError:
                pass
        if parts_dir.exists():
            for old in parts_dir.glob("*"):
                try:
                    if old.is_file():
                        old.unlink()
                except OSError:
                    pass
        # also clear joined temp if any
        for extra in ("_joined_1x.mp4", "final.retime.mp4"):
            p = job_dir / extra
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

        # write exact segment list used for this render (debug / verify deletes)
        (job_dir / "render_segments.json").write_text(
            json.dumps(
                [{"t0_ms": a, "t1_ms": b, "dur_s": round((b - a) / 1000, 3)} for a, b in segs],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        render_plan(
            video,
            segs,
            final_path,
            work_dir=parts_dir,
            smooth=True,
            crossfade_s=0.0,
            edge_fade_s=0.03,
            playback_speed=speed if speed > 0 else 1.3,
        )
        has_final = final_path.exists()
        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": "success" if has_final else "success_partial",
                "stage": "done",
                "progress": 100,
                "finished_at": _utc_now(),
                "has_final": has_final,
                "output_mp4": has_final,
                "worker": "manual_plan_render",
                "error": None if has_final else "渲染未生成 final.mp4",
                "render_token": _utc_now(),
                "render_segments": len(segs),
            }
        )
        if has_final:
            try:
                meta["final_duration_s"] = round(
                    probe_duration_ms(final_path) / 1000.0, 2
                )
                meta["final_size"] = final_path.stat().st_size
            except Exception:
                pass
        # refresh review snippet
        try:
            lines = [
                "# Clip review (manual edit)",
                "",
                f"- selected_clips: {len(segs)}",
                f"- source_plan_ms: {sum(b - a for a, b in segs)}",
                f"- playback_speed: {speed}",
                "",
                "## Golden",
            ]
            for s in plan.get("golden") or []:
                lines.append(f"- [{s.get('t0_ms')}-{s.get('t1_ms')}] {s.get('text')}")
            lines += ["", "## Trust"]
            for s in plan.get("trust") or []:
                lines.append(f"- [{s.get('t0_ms')}-{s.get('t1_ms')}] {s.get('text')}")
            lines += ["", "## CTA"]
            for s in plan.get("cta") or []:
                lines.append(f"- [{s.get('t0_ms')}-{s.get('t1_ms')}] {s.get('text')}")
            (job_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass
        _write_meta(job_dir, meta)
    except Exception as e:
        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": "failed",
                "stage": "failed",
                "error": str(e),
                "traceback": traceback.format_exc()[-2000:],
                "finished_at": _utc_now(),
                "worker": "manual_plan_render",
            }
        )
        _write_meta(job_dir, meta)
    finally:
        with _lock:
            _running.discard(job_id)


def start_render_plan_async(job_dir: Path) -> bool:
    job_dir = Path(job_dir)
    job_id = job_dir.name
    with _lock:
        if job_id in _running:
            return False
        _running.add(job_id)
    t = threading.Thread(target=render_from_plan_only, args=(job_dir,), daemon=True)
    t.start()
    return True
