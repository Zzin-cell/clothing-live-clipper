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
# Whisper/CUDA model load+infer is safest serialized; other stages can run concurrent.
_asr_lock = threading.Lock()
# allow many jobs in parallel (non-ASR stages overlap)
_MAX_CONCURRENT_JOBS = int(os.environ.get("CLIPPER_MAX_CONCURRENT_JOBS") or "4")


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
        speed = float(meta.get("playback_speed") or os.environ.get("CLIPPER_PLAYBACK_SPEED") or 1.4)

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
        _set_progress(job_dir, "asr", 25, f"高精度口播打轴 ({model_name})，GPU听写排队中/进行中")
        # heartbeat: if asr takes long, UI still shows activity
        import time as _time

        t_asr0 = _time.time()
        # serialize only the Whisper call so concurrent jobs don't thrash GPU/model
        with _asr_lock:
            _set_progress(job_dir, "asr", 28, f"正在听写 ({model_name})")
            raw = asr_local(wav)
        raw_path = job_dir / "transcript_asr.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = _read_meta(job_dir)
        meta["asr_model"] = str(model_name)
        meta["asr_segments"] = len(raw)
        meta["asr_seconds"] = round(_time.time() - t_asr0, 1)
        _write_meta(job_dir, meta)
        _set_progress(job_dir, "asr_done", 40, f"听写完成 {len(raw)} 句 · {meta['asr_seconds']}s")

        sp = speed if speed > 0 else 1.4
        settings = Settings.from_env()
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
            golden_features_only=settings.golden_features_only,
            demote_outfit_change_from_golden=settings.demote_outfit_change_from_golden,
            exclude_price_from_cut=settings.exclude_price_from_cut,
            clothing_only=settings.clothing_only,
            de_live_room_feel=getattr(settings, "de_live_room_feel", True),
            unique_features_first=getattr(settings, "unique_features_first", True),
            llm_plan_enabled=getattr(settings, "llm_plan_enabled", True),
            llm_api_key=settings.llm_api_key,
            llm_base_url=settings.llm_base_url,
            llm_model=settings.llm_model,
        )

        tr_path = job_dir / "transcript_for_clipper.json"
        planner = "rules"
        llm_debug: dict[str, Any] = {}
        used_llm = False

        # ---- Preferred path: ASR -> LLM logic plan -> reverse cut ----
        # LLM credentials come from frontend user config, not env
        try:
            from clipper.user_llm import public_user_llm, runtime_llm

            _ul = public_user_llm()
            can_llm = bool(_ul.get("plan_ready"))
            if can_llm:
                # inject into settings-like fields for debug only
                rt = runtime_llm()
                settings = Settings(
                    target_duration_s=settings.target_duration_s,
                    golden_s=settings.golden_s,
                    cta_s=settings.cta_s,
                    min_clip_ms=settings.min_clip_ms,
                    max_clip_ms=settings.max_clip_ms,
                    min_plan_ms=settings.min_plan_ms,
                    max_plan_ms=settings.max_plan_ms,
                    playback_speed=settings.playback_speed,
                    golden_weight_ratio=settings.golden_weight_ratio,
                    golden_features_only=settings.golden_features_only,
                    demote_outfit_change_from_golden=settings.demote_outfit_change_from_golden,
                    exclude_price_from_cut=settings.exclude_price_from_cut,
                    clothing_only=settings.clothing_only,
                    de_live_room_feel=settings.de_live_room_feel,
                    unique_features_first=settings.unique_features_first,
                    llm_plan_enabled=True,
                    llm_api_key=rt.get("api_key"),
                    llm_base_url=rt.get("base_url") or "",
                    llm_model=rt.get("model") or "",
                )
        except Exception:
            can_llm = False
        if can_llm:
            _set_progress(job_dir, "llm_plan", 55, "LLM 逻辑处理口播稿…")
            try:
                from clipper.llm_plan import plan_from_asr_with_llm
                from asr_enhance import is_garbage_asr_text  # type: ignore

                # Submit nearly full ASR; only drop pure hallucination loops.
                # Do NOT pre-filter clothing content — LLM extracts main points from all clauses.
                llm_input = []
                dropped_garbage = 0
                for u in raw:
                    if not isinstance(u, dict):
                        continue
                    tx = str(u.get("text") or "").strip()
                    if not tx:
                        continue
                    try:
                        if is_garbage_asr_text(tx):
                            dropped_garbage += 1
                            continue
                    except Exception:
                        pass
                    llm_input.append(u)
                if not llm_input:
                    llm_input = list(raw)

                _set_progress(
                    job_dir,
                    "llm_plan",
                    58,
                    f"LLM 读取全量口播小句并提取主要内容（{len(llm_input)}句）…",
                )
                plan_llm, llm_obj = plan_from_asr_with_llm(
                    llm_input,
                    target_seconds=target,
                    playback_speed=sp,
                    settings=settings,
                )
                llm_debug = {
                    "product_summary": llm_obj.get("product_summary"),
                    "main_points": llm_obj.get("main_points"),
                    "hook_type": llm_obj.get("hook_type"),
                    "logic": llm_obj.get("logic"),
                    "notes": llm_obj.get("notes"),
                    "drop_ids": llm_obj.get("drop_ids"),
                    "_meta": llm_obj.get("_meta"),
                    "keep_n": len(plan_llm.golden),
                    "input_utterances": len(llm_input),
                    "dropped_garbage": dropped_garbage,
                }
                (job_dir / "llm_plan.json").write_text(
                    json.dumps(llm_obj, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (job_dir / "plan.json").write_text(
                    plan_llm.model_dump_json(indent=2), encoding="utf-8"
                )
                # keep lines for UI / learning compatibility
                kept_lines = []
                for i, s in enumerate(plan_llm.golden):
                    kept_lines.append(
                        {
                            "utt_id": str(s.clip_id or f"llm{i:04d}"),
                            "text": s.text,
                            "t0_ms": int(s.t0_ms),
                            "t1_ms": int(s.t1_ms),
                        }
                    )
                tr_path.write_text(
                    json.dumps(kept_lines, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                planner = "llm"
                used_llm = True
                meta = _read_meta(job_dir)
                meta["planner"] = "llm"
                meta["llm_model"] = (llm_obj.get("_meta") or {}).get("model") or settings.llm_model
                meta["llm_summary"] = str(llm_obj.get("product_summary") or "")[:120]
                meta["selected_clips"] = len(plan_llm.golden)
                meta["warnings"] = list(plan_llm.warnings or [])
                _write_meta(job_dir, meta)

                if render:
                    _set_progress(job_dir, "render", 80, f"按 LLM 逻辑反剪渲染（{len(plan_llm.golden)}段 · draft）")
                    meta = _read_meta(job_dir)
                    meta["render_profile"] = "draft"
                    _write_meta(job_dir, meta)
                    render_from_plan_only(job_dir)
                    # render_from_plan_only writes final status; reload
                    meta = _read_meta(job_dir)
                    meta["planner"] = "llm"
                    meta["transcript_source"] = "faster_whisper_local"
                    meta["playback_speed"] = sp
                    meta["llm_summary"] = str(llm_obj.get("product_summary") or "")[:120]
                    _write_meta(job_dir, meta)
                else:
                    meta = _read_meta(job_dir)
                    meta.update(
                        {
                            "status": "success_partial",
                            "stage": "done",
                            "progress": 100,
                            "finished_at": _utc_now(),
                            "has_final": False,
                            "output_mp4": False,
                            "planner": "llm",
                            "worker": "local_auto",
                            "playback_speed": sp,
                            "selected_clips": len(plan_llm.golden),
                            "duration_s": plan_llm.total_duration_ms / 1000.0,
                            "warnings": plan_llm.warnings,
                            "error": None,
                        }
                    )
                    _write_meta(job_dir, meta)

                # learning debug (planner=llm)
                try:
                    from clipper.learning import learning_status

                    (job_dir / "learning_debug.json").write_text(
                        json.dumps(
                            {
                                "planner": "llm",
                                "status": learning_status(),
                                "llm": llm_debug,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return
            except Exception as e:
                # fall through to rules
                meta = _read_meta(job_dir)
                meta["planner"] = "rules"
                meta["llm_fallback"] = True
                meta["llm_error"] = str(e)[:500]
                _write_meta(job_dir, meta)
                _set_progress(job_dir, "filter", 50, f"LLM 不可用，回退规则：{str(e)[:80]}")
        else:
            meta = _read_meta(job_dir)
            meta["planner"] = "rules"
            meta["llm_fallback"] = False
            meta["llm_error"] = "llm_plan_disabled_or_missing_key"
            _write_meta(job_dir, meta)

        # ---- Fallback path: rules filter + rank + render ----
        _set_progress(job_dir, "filter", 52, "过滤无效/非服装内容（规则+学习）")
        kept = filter_for_duration(
            raw,
            target_ms=int(84_000 * sp / 1.4),
            min_ms=int(76_000 * sp / 1.4),
            max_ms=int(92_000 * sp / 1.4),
        )
        tr_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            from clipper.learning import learned_text_score, learning_status

            learn_rows = []
            for u in kept[:30]:
                t = str(u.get("text") or "")
                learn_rows.append(
                    {
                        "text": t[:80],
                        "learn_hook": round(learned_text_score(t, for_hook=True), 2),
                        "learn_all": round(learned_text_score(t, for_hook=False), 2),
                    }
                )
            learn_rows.sort(key=lambda x: x["learn_hook"], reverse=True)
            (job_dir / "learning_debug.json").write_text(
                json.dumps(
                    {
                        "planner": "rules",
                        "status": learning_status(),
                        "kept_top": learn_rows[:12],
                        "llm": llm_debug or None,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            meta = _read_meta(job_dir)
            meta["learning_events"] = (learning_status().get("events") or 0)
            meta["learning_applied"] = True
            _write_meta(job_dir, meta)
        except Exception as e:
            meta = _read_meta(job_dir)
            meta["learning_applied"] = False
            meta["learning_error"] = str(e)
            _write_meta(job_dir, meta)

        _set_progress(job_dir, "clipper", 65, "规则逻辑排序与时间轴")
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
                "planner": meta.get("planner") or planner,
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
        speed = float(meta.get("playback_speed") or os.environ.get("CLIPPER_PLAYBACK_SPEED") or 1.4)

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
            playback_speed=speed if speed > 0 else 1.4,
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


def running_job_ids() -> list[str]:
    with _lock:
        return sorted(_running)


def start_job_async(job_dir: Path) -> bool:
    """Start background thread if not already running this job.

    Multiple different jobs can run concurrently. Whisper ASR stage is
    serialized via `_asr_lock` so GPU model use does not thrash; LLM/render
    stages of different jobs can overlap.
    """
    job_dir = Path(job_dir)
    job_id = job_dir.name
    with _lock:
        if job_id in _running:
            return False
        # soft cap: still allow queueing by returning False when too many
        if len(_running) >= max(1, _MAX_CONCURRENT_JOBS):
            # keep job meta as queued for UI retry/poll
            try:
                meta = _read_meta(job_dir)
                meta["status"] = "queued"
                meta["stage"] = "queued"
                meta["stage_detail"] = f"等待并发空位（{_MAX_CONCURRENT_JOBS}）"
                meta["progress"] = max(1, int(meta.get("progress") or 1))
                _write_meta(job_dir, meta)
            except Exception:
                pass
            # schedule delayed retry start without blocking caller
            def _retry():
                import time as _t

                for _ in range(120):
                    _t.sleep(2.0)
                    with _lock:
                        if job_id in _running:
                            return
                        if len(_running) >= max(1, _MAX_CONCURRENT_JOBS):
                            continue
                        _running.add(job_id)
                    threading.Thread(
                        target=process_job_dir, args=(job_dir,), daemon=True, name=f"job-{job_id}"
                    ).start()
                    return

            threading.Thread(target=_retry, daemon=True, name=f"queue-{job_id}").start()
            return True
        _running.add(job_id)
    t = threading.Thread(target=process_job_dir, args=(job_dir,), daemon=True, name=f"job-{job_id}")
    t.start()
    return True


def start_reclip_async(job_dir: Path) -> bool:
    """Reclip using saved transcript without ASR (concurrent across jobs)."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    with _lock:
        if job_id in _running:
            return False
        _running.add(job_id)
    t = threading.Thread(
        target=reclip_from_saved_transcript, args=(job_dir,), daemon=True, name=f"reclip-{job_id}"
    )
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

        speed = float(meta.get("playback_speed") or os.environ.get("CLIPPER_PLAYBACK_SPEED") or 1.4)
        profile = str(meta.get("render_profile") or "draft").strip().lower()
        if profile not in {"draft", "final"}:
            profile = "draft"
        _set_progress(
            job_dir,
            "render",
            75,
            f"按结构调整渲染（{len(segs)}段 · {profile}）",
        )
        from clipper.media import probe_duration_ms, render_plan
        import shutil

        # Keep previous draft parts for P3 reuse; only wipe finals when exporting final.
        preview_path = job_dir / "preview.mp4"
        final_path = job_dir / "final.mp4"
        parts_dir = job_dir / (f"_parts_{profile}")
        if profile == "final" and final_path.exists():
            try:
                final_path.unlink()
            except OSError:
                pass
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

        out_target = preview_path if profile == "draft" else final_path
        render_plan(
            video,
            segs,
            out_target,
            work_dir=parts_dir,
            smooth=True,
            crossfade_s=0.0,
            playback_speed=speed if speed > 0 else 1.4,
            profile=profile,
            reuse_parts=True,
        )
        if profile == "draft" and preview_path.exists():
            # UI historically loads final.mp4 — mirror draft for fast preview loop
            try:
                shutil.copy2(preview_path, final_path)
            except Exception:
                pass
        has_preview = preview_path.exists()
        has_final = final_path.exists()
        meta = _read_meta(job_dir)
        meta.update(
            {
                "status": "success" if (has_preview or has_final) else "success_partial",
                "stage": "done",
                "progress": 100,
                "finished_at": _utc_now(),
                "has_final": has_final,
                "has_preview": has_preview,
                "output_mp4": has_final or has_preview,
                "worker": "manual_plan_render",
                "error": None if (has_preview or has_final) else "渲染未生成预览/成片",
                "render_token": _utc_now(),
                "render_segments": len(segs),
                "render_profile": profile,
            }
        )
        play_path = preview_path if has_preview else final_path
        if play_path.exists():
            try:
                meta["final_duration_s"] = round(probe_duration_ms(play_path) / 1000.0, 2)
                meta["final_size"] = play_path.stat().st_size
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
