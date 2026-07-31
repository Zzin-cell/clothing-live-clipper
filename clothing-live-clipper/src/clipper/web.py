from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from clipper.config import Settings, apply_config_update, asr_status, public_config
from clipper.job_worker import start_job_async
from clipper.learning import clear_learning, learning_status, record_plan_feedback
from clipper.media import which_ffmpeg
from clipper.pipeline import run_pipeline
from clipper.system_status import build_status, run_probe
from clipper.whisper_asr import ASRError, transcribe_video_to_json
from pydantic import BaseModel, Field

APP_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
JOBS_DIR = APP_ROOT / "output" / "web_jobs"
SAMPLE_TRANSCRIPT = APP_ROOT / "tests" / "fixtures" / "sample_transcript.json"

ALLOWED_TRANSCRIPT = {".json", ".srt"}
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".mts", ".m2ts"}


class ConfigUpdate(BaseModel):
    persist: bool = True
    api_key: str | None = None
    base_url: str | None = None
    asr_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_enabled: bool | None = None
    llm_plan: bool | None = None
    organization: str | None = None
    asr_enabled: bool | None = None
    asr_provider: str | None = None


class ProbeBody(BaseModel):
    target: str = Field(default="all")


class LlmModelsBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    preferred: str | None = None


class AgentCompleteBody(BaseModel):
    status: str = "success"
    error: str | None = None
    message: str | None = None
    transcript_source: str | None = None


class AgentFailBody(BaseModel):
    error: str = "agent_failed"


class TranscriptSaveBody(BaseModel):
    items: list[dict] = Field(default_factory=list)
    reclip: bool = True


class PlanEditBody(BaseModel):
    golden: list[dict] = Field(default_factory=list)
    trust: list[dict] = Field(default_factory=list)
    cta: list[dict] = Field(default_factory=list)
    reclip: bool = True
    # Plan D: user chooses whether this reverse-cut should train global ranking
    learn: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_meta(d: Path, meta: dict) -> None:
    (d / "job_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _safe_name(name: str | None, default: str) -> str:
    raw = Path(name or default).name
    return raw if raw else default


def _settings_for_target(target_seconds: int) -> Settings:
    base = Settings.from_env()
    return Settings(
        target_duration_s=target_seconds,
        golden_s=min(20, max(8, target_seconds // 3)),
        cta_s=min(10, max(5, target_seconds // 6)),
        min_clip_ms=base.min_clip_ms,
        max_clip_ms=base.max_clip_ms,
        golden_weight_ratio=base.golden_weight_ratio,
        llm_api_key=base.llm_api_key,
        llm_base_url=base.llm_base_url,
        llm_model=base.llm_model,
    )


def _status_from_result(result: Any) -> str:
    if result.plan and result.output_mp4:
        return "success"
    if result.plan:
        return "success_partial"
    return "failed"


def _apply_result_meta(
    meta: dict[str, Any],
    result: Any,
    *,
    has_vid: bool,
    status: str,
) -> None:
    meta.update(
        {
            "status": status,
            "finished_at": _utc_now(),
            "has_video": has_vid,
            "has_final": bool(result.output_mp4),
            "output_mp4": bool(result.output_mp4),
            "render_skipped": result.meta.get("render_skipped"),
            "render_error": result.meta.get("render_error"),
            "golden20_passed": bool(result.plan.golden20_passed) if result.plan else False,
            "duration_s": (result.plan.total_duration_ms / 1000.0) if result.plan else 0,
            "warnings": result.plan.warnings if result.plan else [],
            "selected_clips": len(result.plan.all_slots()) if result.plan else 0,
        }
    )


def _find_uploaded_video(uploads: Path) -> Path | None:
    if not uploads.exists():
        return None
    for p in uploads.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_VIDEO:
            return p
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="服装带货智能切片", version="0.1.0")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return HTMLResponse("<h1>static/index.html missing</h1>", status_code=500)
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from clipper.config import llm_status, public_config

        a = asr_status()
        l = llm_status()
        st = build_status()
        return {
            "ok": True,
            "ffmpeg": bool(which_ffmpeg()),
            "sample_transcript": SAMPLE_TRANSCRIPT.exists(),
            "asr_configured": a["asr_configured"],
            "asr_note": a.get("asr_note"),
            "llm_plan_ready": bool(l.get("plan_ready")),
            "llm_plan_enabled": bool(l.get("plan_enabled")),
            "llm_model": l.get("model"),
            "llm_note": l.get("note"),
            "lights": st.get("lights"),
            "config": {
                "llm_plan_ready": bool(l.get("plan_ready")),
                "has_llm_key": bool(l.get("has_key")),
            },
            "time": _utc_now(),
        }

    @app.get("/api/system/status")
    def system_status() -> dict[str, Any]:
        st = build_status()
        try:
            st["learning"] = learning_status()
        except Exception as e:
            st["learning"] = {"enabled": False, "error": str(e)}
        return st

    @app.get("/api/learning/status")
    def api_learning_status() -> dict[str, Any]:
        return learning_status()

    @app.post("/api/learning/clear")
    def api_learning_clear() -> dict[str, Any]:
        """Delete previous learned preferences (fresh start)."""
        try:
            st = clear_learning(keep_events_backup=True)
            return {"ok": True, "learning": st}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"clear learning failed: {e}") from e

    @app.get("/api/system/config")
    def system_config_get() -> dict[str, Any]:
        return public_config()

    @app.put("/api/system/config")
    def system_config_put(body: ConfigUpdate) -> dict[str, Any]:
        try:
            # validate LLM fields early with clear Chinese errors
            from clipper.user_llm import validate_user_llm_fields

            errs = validate_user_llm_fields(
                base_url=body.llm_base_url or body.base_url,
                api_key=body.llm_api_key or body.api_key,
                model=body.llm_model,
            )
            if errs:
                raise HTTPException(status_code=400, detail="；".join(errs))
            cfg = apply_config_update(body.model_dump(exclude_none=True))
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"写入配置失败: {e}") from e
        return {"ok": True, "config": cfg, "status": build_status()}

    @app.post("/api/system/probe")
    def system_probe(body: ProbeBody) -> dict[str, Any]:
        result = run_probe(body.target)
        # refresh lights after probe where possible
        status = build_status()
        if body.target in {"whisper", "all"} and isinstance(result, dict):
            w = result if body.target == "whisper" else result.get("whisper") or {}
            if isinstance(w, dict) and w.get("ok") is True:
                status["asr"]["ok"] = True
                status["lights"]["asr"] = "green"
            elif isinstance(w, dict) and w.get("ok") is False:
                status["asr"]["ok"] = False
                status["lights"]["asr"] = "red"
        if body.target in {"llm", "all"} and isinstance(result, dict):
            lm = result if body.target == "llm" else result.get("llm") or {}
            if isinstance(lm, dict) and lm.get("ok") is True:
                status["llm"]["ok"] = True
                status["lights"]["llm"] = "green"
            elif isinstance(lm, dict) and lm.get("ok") is False and lm.get("error") not in {
                "missing_api_key",
                "missing_user_api_key",
            }:
                status["llm"]["ok"] = False
                status["lights"]["llm"] = "red"
        return {"ok": True, "probe": result, "status": status}

    @app.post("/api/system/llm/models")
    def system_llm_models(body: LlmModelsBody) -> dict[str, Any]:
        """List models from user-provided OpenAI-compatible base_url + key, auto-pick one."""
        import time

        from clipper.openai_compat import discover_models_and_pick
        from clipper.user_llm import runtime_llm, save_user_llm, validate_user_llm_fields

        rt = runtime_llm()
        base = (body.base_url or rt.get("base_url") or "").strip()
        key = (body.api_key or rt.get("api_key") or "").strip()
        preferred = (body.preferred or rt.get("model") or "").strip() or None
        errs = validate_user_llm_fields(base_url=base, api_key=key, require_key=True)
        if errs:
            raise HTTPException(status_code=400, detail="；".join(errs))
        t0 = time.perf_counter()
        disc = discover_models_and_pick(base_url=base, api_key=key, preferred=preferred)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        # optionally remember base/key/model to user config if key provided in request
        if body.api_key or body.base_url or disc.get("picked"):
            try:
                save_user_llm(
                    {
                        "llm_base_url": base,
                        "llm_api_key": body.api_key if body.api_key else None,
                        "llm_model": disc.get("picked") or preferred or "",
                        "llm_plan": True,
                        "llm_enabled": True,
                    },
                    keep_old_key_if_blank=True,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "ok": bool(disc.get("ok")),
            "base_url": disc.get("base_url") or base,
            "models": disc.get("models") or [],
            "picked": disc.get("picked"),
            "count": disc.get("count") or 0,
            "latency_ms": latency_ms,
            "config": public_config(),
        }

    @app.get("/api/jobs")
    def list_jobs(limit: int = 30) -> dict[str, Any]:
        jobs = []
        if JOBS_DIR.exists():
            dirs = sorted(
                [p for p in JOBS_DIR.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for d in dirs[: max(1, min(limit, 100))]:
                meta_path = d / "job_meta.json"
                if meta_path.exists():
                    jobs.append(_read_json(meta_path))
                else:
                    jobs.append({"job_id": d.name, "status": "unknown"})
        return {"jobs": jobs}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        meta = _read_json(meta_path)
        plan_path = d / "plan.json"
        review_path = d / "review.md"
        if plan_path.exists():
            meta["plan"] = _read_json(plan_path)
        if review_path.exists():
            meta["review_md"] = review_path.read_text(encoding="utf-8")
        has_preview = (d / "preview.mp4").exists()
        has_final = (d / "final.mp4").exists()
        meta["files"] = {
            "plan": (d / "plan.json").exists(),
            "review": (d / "review.md").exists(),
            "clips": (d / "clips.json").exists(),
            "preview": has_preview,
            "final": has_final,
            "result": (d / "result.json").exists(),
            "transcript": (d / "transcript.json").exists()
            or (d / "transcript_asr.json").exists(),
            "transcript_asr": (d / "transcript_asr.json").exists(),
        }
        meta["has_preview"] = has_preview
        meta["has_final"] = has_final
        meta["render_profile"] = meta.get("render_profile") or (
            "draft" if has_preview and not meta.get("export_final") else meta.get("render_profile")
        )
        return meta

    def _download_name(job_id: str, filename: str) -> str:
        """
        Browser download filename.
        final.mp4 / preview.mp4 → {original_stem}final.mp4 / {original_stem}preview.mp4
        e.g. 连衣裙.mp4 → 连衣裙final.mp4
        """
        meta_path = _job_dir(job_id) / "job_meta.json"
        src = ""
        try:
            if meta_path.exists():
                meta = _read_json(meta_path)
                src = str(meta.get("video_source") or "").strip()
        except Exception:
            src = ""
        stem = Path(src).stem if src else ""
        # sanitize for Content-Disposition / Windows-ish downloads
        if stem:
            bad = '<>:"/\\|?*\n\r\t'
            stem = "".join("_" if c in bad else c for c in stem).strip(" .")
            stem = stem[:80] if stem else ""
        if not stem:
            stem = str(job_id)
        if filename == "final.mp4":
            return f"{stem}final.mp4"
        if filename == "preview.mp4":
            return f"{stem}preview.mp4"
        # other artifacts: prefix original stem for clarity
        if stem and filename:
            return f"{stem}_{filename}"
        return filename

    @app.get("/api/jobs/{job_id}/files/{filename}")
    def get_job_file(job_id: str, filename: str) -> FileResponse:
        allowed = {
            "plan.json",
            "review.md",
            "clips.json",
            "claims.json",
            "transcript.json",
            "transcript_asr.json",
            "result.json",
            "preview.mp4",
            "final.mp4",
            "job_meta.json",
        }
        if filename not in allowed:
            raise HTTPException(status_code=400, detail="file not allowed")
        path = _job_dir(job_id) / filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="file not found")
        media = {
            ".mp4": "video/mp4",
            ".json": "application/json",
            ".md": "text/markdown; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")
        download_as = _download_name(job_id, filename)
        return FileResponse(path, media_type=media, filename=download_as)

    async def _save_upload(upload: UploadFile | None, dest: Path) -> None:
        if upload is None:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = await upload.read()
        dest.write_bytes(data)

    def _list_job_dirs() -> list[Path]:
        if not JOBS_DIR.exists():
            return []
        return sorted(
            [p for p in JOBS_DIR.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
        )

    @app.get("/api/agent/next")
    def agent_next() -> dict[str, Any]:
        """Claim oldest queued job for Agent + skill processing."""
        for d in _list_job_dirs():
            meta_path = d / "job_meta.json"
            if not meta_path.exists():
                continue
            meta = _read_json(meta_path)
            if meta.get("status") != "queued":
                continue
            meta["status"] = "claimed"
            meta["claimed_at"] = _utc_now()
            meta["worker"] = "agent_skill"
            _write_meta(d, meta)
            video = _find_uploaded_video(d / "uploads")
            return {
                "job": meta,
                "paths": {
                    "job_dir": str(d.resolve()),
                    "uploads": str((d / "uploads").resolve()),
                    "video": str(video.resolve()) if video else None,
                    "meta": str(meta_path.resolve()),
                },
                "instructions": (
                    "Use clothing-live-clip skill: smart speech timeline from video, "
                    "claims, golden 20s / ~60s plan, optional final.mp4 via clipper; "
                    "write outputs into job_dir then POST /api/agent/jobs/{id}/complete"
                ),
            }
        return {"job": None, "message": "queue empty"}

    @app.post("/api/agent/jobs/{job_id}/complete")
    def agent_complete(job_id: str, body: AgentCompleteBody) -> dict[str, Any]:
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        meta = _read_json(meta_path)
        status = body.status if body.status in {
            "success",
            "success_partial",
            "failed",
        } else "success"
        # derive from files if present
        has_plan = (d / "plan.json").exists()
        has_final = (d / "final.mp4").exists()
        if status != "failed":
            if has_plan and has_final:
                status = "success"
            elif has_plan:
                status = "success_partial"
            elif not has_plan:
                status = "failed"
                meta["error"] = body.error or "complete called but plan.json missing"
        meta["status"] = status
        meta["finished_at"] = _utc_now()
        meta["has_final"] = has_final
        meta["output_mp4"] = has_final
        meta["worker"] = "agent_skill"
        if body.transcript_source:
            meta["transcript_source"] = body.transcript_source
        if body.message:
            meta["agent_message"] = body.message
        if body.error:
            meta["error"] = body.error
        if has_plan:
            try:
                plan = _read_json(d / "plan.json")
                meta["golden20_passed"] = bool(plan.get("golden20_passed"))
                meta["duration_s"] = (plan.get("total_duration_ms") or 0) / 1000.0
                slots = (plan.get("golden") or []) + (plan.get("trust") or []) + (
                    plan.get("cta") or []
                )
                meta["selected_clips"] = len(slots)
                meta["warnings"] = plan.get("warnings") or []
            except Exception:
                pass
        _write_meta(d, meta)
        return get_job(job_id)

    @app.post("/api/agent/jobs/{job_id}/fail")
    def agent_fail(job_id: str, body: AgentFailBody) -> dict[str, Any]:
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        meta = _read_json(meta_path)
        meta["status"] = "failed"
        meta["error"] = body.error
        meta["finished_at"] = _utc_now()
        meta["worker"] = "agent_skill"
        _write_meta(d, meta)
        return get_job(job_id)

    @app.post("/api/jobs")
    async def create_job(
        video: UploadFile | None = File(default=None),
        target_seconds: int = Form(default=60),
        render: bool = Form(default=True),
        auto_process: bool = Form(default=True),
    ) -> dict[str, Any]:
        """Video-only intake: save file and auto-run local pipeline (no Agent needed)."""
        target_seconds = int(target_seconds)
        if target_seconds < 15 or target_seconds > 180:
            raise HTTPException(status_code=400, detail="target_seconds must be 15-180")

        if video is None or not video.filename:
            raise HTTPException(status_code=400, detail="请上传直播视频（唯一输入）")

        suffix = Path(video.filename).suffix.lower()
        if suffix not in ALLOWED_VIDEO:
            raise HTTPException(status_code=400, detail=f"视频格式不支持: {suffix}")

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        d = _job_dir(job_id)
        uploads = d / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)

        video_path = uploads / _safe_name(video.filename, f"video{suffix}")
        try:
            await _save_upload(video, video_path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"保存视频失败: {e}") from e

        meta: dict[str, Any] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": _utc_now(),
            "target_seconds": target_seconds,
            "render_requested": bool(render),
            "error": None,
            "has_video": True,
            "has_final": False,
            "process_mode": "local_auto",
            "video_source": video.filename,
            "progress": 0,
            "stage": "queued",
            "user_hint": "上传后自动听写打轴并切片，无需 Agent",
        }
        _write_meta(d, meta)

        # Fire-and-forget local worker (ASR + clipper)
        if auto_process:
            started = start_job_async(d)
            meta = _read_json(d / "job_meta.json")
            meta["auto_started"] = bool(started)
            if started:
                meta["status"] = "processing"
                meta["stage"] = "starting"
                meta["progress"] = 1
            _write_meta(d, meta)

        return get_job(job_id)

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, Any]:
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        meta = _read_json(meta_path)
        meta["status"] = "queued"
        meta["error"] = None
        meta["stage"] = "queued"
        meta["progress"] = 0
        meta.pop("finished_at", None)
        _write_meta(d, meta)
        start_job_async(d)
        return get_job(job_id)

    @app.get("/api/jobs/{job_id}/transcript")
    def get_transcript(job_id: str, kind: str = "kept") -> dict[str, Any]:
        """Return ASR transcript for editing. kind=kept|raw|all"""
        d = _job_dir(job_id)
        if not (d / "job_meta.json").exists():
            raise HTTPException(status_code=404, detail="job not found")
        kept_p = d / "transcript_for_clipper.json"
        raw_p = d / "transcript_asr.json"
        raw = _read_json(raw_p) if raw_p.exists() else []
        kept = _read_json(kept_p) if kept_p.exists() else []
        if not isinstance(raw, list):
            raw = []
        if not isinstance(kept, list):
            kept = []
        # normalize
        def norm(items: list) -> list[dict[str, Any]]:
            out = []
            for i, u in enumerate(items):
                if not isinstance(u, dict):
                    continue
                out.append(
                    {
                        "utt_id": str(u.get("utt_id") or f"u{i:04d}"),
                        "text": str(u.get("text") or "").strip(),
                        "t0_ms": int(u.get("t0_ms") or 0),
                        "t1_ms": int(u.get("t1_ms") or 0),
                        "keep": True,
                    }
                )
            return out

        raw_n = norm(raw)
        kept_ids = {
            (int(u.get("t0_ms") or 0), str(u.get("text") or "").strip()) for u in kept
        }
        for u in raw_n:
            key = (u["t0_ms"], u["text"])
            u["keep"] = key in kept_ids if kept_ids else True
        if kind == "raw":
            items = raw_n
        elif kind == "kept":
            items = [u for u in raw_n if u["keep"]] or norm(kept)
        else:
            items = raw_n
        return {
            "job_id": job_id,
            "has_raw": raw_p.exists(),
            "has_kept": kept_p.exists(),
            "count": len(items),
            "items": items,
        }

    @app.put("/api/jobs/{job_id}/plan")
    def save_plan(job_id: str, body: PlanEditBody) -> dict[str, Any]:
        """Save human-edited plan structure and optionally re-render final video."""
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")

        # baseline before overwrite (for learning)
        before_plan = None
        if (d / "plan.json").exists():
            try:
                before_plan = _read_json(d / "plan.json")
            except Exception:
                before_plan = None

        def clean_slots(items: list[dict], role: str) -> list[dict]:
            out: list[dict] = []
            for i, s in enumerate(items or []):
                if not isinstance(s, dict):
                    continue
                if s.get("removed") is True:
                    continue
                # allow empty text: reverse cut is time-based; text is annotation
                text = str(s.get("text") or "").strip()
                t0 = int(s.get("t0_ms") or 0)
                t1 = int(s.get("t1_ms") or 0)
                if t1 <= t0:
                    continue
                out.append(
                    {
                        "clip_id": str(s.get("clip_id") or f"{role}_{i:03d}"),
                        "role": role if role != "hook" else "hook",
                        "t0_ms": t0,
                        "t1_ms": t1,
                        "text": text,
                        "score": float(s.get("score") or 0),
                    }
                )
            return out

        golden = clean_slots(body.golden, "hook")
        trust = clean_slots(body.trust, "trust")
        cta = clean_slots(body.cta, "cta")
        if not (golden or trust or cta):
            raise HTTPException(status_code=400, detail="成片结构不能为空，请至少保留一个片段")

        # de-dup identical time windows (prevent deleted-but-duplicated ranges)
        seen_win: set[tuple[int, int]] = set()
        def dedup(slots: list[dict]) -> list[dict]:
            out = []
            for s in slots:
                w = (int(s["t0_ms"]), int(s["t1_ms"]))
                if w in seen_win:
                    continue
                seen_win.add(w)
                out.append(s)
            return out

        golden, trust, cta = dedup(golden), dedup(trust), dedup(cta)
        total_ms = sum((s["t1_ms"] - s["t0_ms"]) for s in golden + trust + cta)
        plan = {
            "target_duration_s": _read_json(meta_path).get("target_seconds", 60),
            "golden": golden,
            "trust": trust,
            "cta": cta,
            "total_duration_ms": total_ms,
            "golden20_passed": bool(golden),
            "golden_weight_ratio": 0.0,
            "warnings": [
                "manual_plan_edit",
                f"manual_segments={len(golden)+len(trust)+len(cta)}",
            ],
        }
        (d / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (d / "plan_edited.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Plan D: optional learn from human reverse-edit (user toggle)
        learn_flag = bool(getattr(body, "learn", False))
        if learn_flag:
            try:
                prefs = record_plan_feedback(
                    job_id=job_id,
                    before_plan=before_plan if isinstance(before_plan, dict) else None,
                    after_plan=plan,
                    source="plan_edit",
                )
                plan.setdefault("warnings", []).append(
                    f"learning_events={((prefs.get('stats') or {}).get('events') or 0)}"
                )
                (d / "plan.json").write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                plan.setdefault("warnings", []).append(f"learning_skip:{e}")
        else:
            plan.setdefault("warnings", []).append("learning_skipped_by_user")
            (d / "plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        # Derive transcript lines from remaining slots for reclip pipeline compatibility
        lines = []
        for i, s in enumerate(golden + trust + cta):
            if not s.get("text"):
                continue
            lines.append(
                {
                    "utt_id": str(s.get("clip_id") or f"p{i:04d}"),
                    "text": s["text"],
                    "t0_ms": s["t0_ms"],
                    "t1_ms": s["t1_ms"],
                }
            )
        if lines:
            (d / "transcript_for_clipper.json").write_text(
                json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        meta = _read_json(meta_path)
        meta["plan_edited_at"] = _utc_now()
        meta["selected_clips"] = len(golden) + len(trust) + len(cta)
        meta["duration_s"] = total_ms / 1000.0
        meta["warnings"] = plan["warnings"]
        meta["learning"] = bool(learn_flag)
        _write_meta(d, meta)

        if body.reclip:
            from clipper.job_worker import start_render_plan_async

            meta["status"] = "processing"
            meta["stage"] = "render"
            meta["progress"] = 70
            meta["error"] = None
            # reverse-cut iteration stays on draft for speed (P1/P3)
            meta["render_profile"] = "draft"
            meta.pop("finished_at", None)
            _write_meta(d, meta)
            start_render_plan_async(d)
        return get_job(job_id)

    @app.post("/api/jobs/{job_id}/export-final")
    def export_final(job_id: str) -> dict[str, Any]:
        """Re-render current plan at final quality (NVENC/libx264 single-pass)."""
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        if not (d / "plan.json").exists():
            raise HTTPException(status_code=400, detail="missing plan.json")
        from clipper.job_worker import start_render_plan_async

        meta = _read_json(meta_path)
        meta["status"] = "processing"
        meta["stage"] = "export"
        meta["stage_detail"] = "导出终稿…"
        meta["progress"] = 70
        meta["error"] = None
        meta["render_profile"] = "final"
        meta["export_final"] = True
        meta.pop("finished_at", None)
        _write_meta(d, meta)
        start_render_plan_async(d)
        return get_job(job_id)

    @app.put("/api/jobs/{job_id}/transcript")
    def save_transcript(job_id: str, body: TranscriptSaveBody) -> dict[str, Any]:
        """Save edited transcript (kept lines) and optionally re-run clipper without ASR."""
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        items = []
        for i, u in enumerate(body.items or []):
            if not isinstance(u, dict):
                continue
            text = str(u.get("text") or "").strip()
            if not text:
                continue
            # allow explicit keep=false to drop
            if u.get("keep") is False:
                continue
            t0 = int(u.get("t0_ms") or 0)
            t1 = int(u.get("t1_ms") or (t0 + 1000))
            if t1 <= t0:
                t1 = t0 + 500
            items.append(
                {
                    "utt_id": str(u.get("utt_id") or f"e{i:04d}"),
                    "text": text,
                    "t0_ms": t0,
                    "t1_ms": t1,
                }
            )
        if not items:
            raise HTTPException(status_code=400, detail="口播稿为空，请至少保留一句")
        items.sort(key=lambda x: x["t0_ms"])
        (d / "transcript_for_clipper.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # also archive edited copy
        (d / "transcript_edited.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        meta = _read_json(meta_path)
        meta["transcript_source"] = "manual_edit"
        meta["transcript_edited_at"] = _utc_now()
        meta["transcript_kept_count"] = len(items)
        _write_meta(d, meta)

        if body.reclip:
            from clipper.job_worker import start_reclip_async

            meta["status"] = "processing"
            meta["stage"] = "reclip"
            meta["progress"] = 50
            meta["error"] = None
            meta.pop("finished_at", None)
            _write_meta(d, meta)
            start_reclip_async(d)
        return get_job(job_id)

    @app.post("/api/jobs/{job_id}/transcript")
    async def attach_transcript(
        job_id: str,
        transcript: UploadFile = File(...),
        render: bool = Form(default=True),
        target_seconds: int | None = Form(default=None),
    ) -> dict[str, Any]:
        d = _job_dir(job_id)
        meta_path = d / "job_meta.json"
        if not meta_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        meta = _read_json(meta_path)
        if meta.get("status") != "needs_transcript":
            raise HTTPException(status_code=400, detail="job is not waiting for transcript")

        uploads = d / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)

        if not transcript.filename:
            raise HTTPException(status_code=400, detail="请上传转写文件（json/srt）")
        suffix = Path(transcript.filename).suffix.lower()
        if suffix not in ALLOWED_TRANSCRIPT:
            raise HTTPException(status_code=400, detail="转写仅支持 .json / .srt")

        transcript_path = uploads / _safe_name(transcript.filename, "transcript.json")
        if transcript_path.suffix.lower() not in ALLOWED_TRANSCRIPT:
            transcript_path = uploads / f"transcript{suffix}"
        await _save_upload(transcript, transcript_path)
        meta["transcript_source"] = transcript.filename

        video_path = _find_uploaded_video(uploads)
        has_vid = video_path is not None

        ts = int(target_seconds) if target_seconds is not None else int(
            meta.get("target_seconds") or 60
        )
        if ts < 15 or ts > 180:
            raise HTTPException(status_code=400, detail="target_seconds must be 15-180")

        meta["status"] = "processing"
        meta["error"] = None
        _write_meta(d, meta)

        try:
            do_render = bool(render and video_path is not None)
            settings = _settings_for_target(ts)
            result = run_pipeline(
                video=video_path,
                transcript_path=transcript_path,
                out_dir=d,
                settings=settings,
                render=do_render,
            )
            status = _status_from_result(result)
            _apply_result_meta(meta, result, has_vid=has_vid, status=status)
        except Exception as e:  # noqa: BLE001 - surface to UI
            meta["status"] = "failed"
            meta["error"] = str(e)
            meta["finished_at"] = _utc_now()
            _write_meta(d, meta)
            raise HTTPException(status_code=500, detail=str(e)) from e

        _write_meta(d, meta)
        return get_job(job_id)

    return app


app = create_app()
