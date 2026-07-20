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
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


class ConfigUpdate(BaseModel):
    persist: bool = True
    api_key: str | None = None
    base_url: str | None = None
    asr_model: str | None = None
    llm_model: str | None = None
    llm_enabled: bool | None = None
    asr_enabled: bool | None = None
    asr_provider: str | None = None


class ProbeBody(BaseModel):
    target: str = Field(default="all")


class AgentCompleteBody(BaseModel):
    status: str = "success"
    error: str | None = None
    message: str | None = None
    transcript_source: str | None = None


class AgentFailBody(BaseModel):
    error: str = "agent_failed"


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
        a = asr_status()
        st = build_status()
        return {
            "ok": True,
            "ffmpeg": bool(which_ffmpeg()),
            "sample_transcript": SAMPLE_TRANSCRIPT.exists(),
            "asr_configured": a["asr_configured"],
            "asr_note": a.get("asr_note"),
            "lights": st.get("lights"),
            "time": _utc_now(),
        }

    @app.get("/api/system/status")
    def system_status() -> dict[str, Any]:
        return build_status()

    @app.get("/api/system/config")
    def system_config_get() -> dict[str, Any]:
        return public_config()

    @app.put("/api/system/config")
    def system_config_put(body: ConfigUpdate) -> dict[str, Any]:
        try:
            cfg = apply_config_update(body.model_dump(exclude_none=True))
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
            elif isinstance(lm, dict) and lm.get("ok") is False and lm.get("error") != "missing_api_key":
                status["llm"]["ok"] = False
                status["lights"]["llm"] = "red"
        return {"ok": True, "probe": result, "status": status}

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
        meta["files"] = {
            "plan": (d / "plan.json").exists(),
            "review": (d / "review.md").exists(),
            "clips": (d / "clips.json").exists(),
            "final": (d / "final.mp4").exists(),
            "result": (d / "result.json").exists(),
            "transcript": (d / "transcript.json").exists()
            or (d / "transcript_asr.json").exists(),
            "transcript_asr": (d / "transcript_asr.json").exists(),
        }
        return meta

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
        return FileResponse(path, media_type=media, filename=filename)

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
    ) -> dict[str, Any]:
        """Video-only intake: save file and queue for Agent skill worker."""
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
            "process_mode": "agent",
            "video_source": video.filename,
            "queue_hint": "在 Agent 对话发送：处理队列",
            "user_hint": "输入只要视频；口播打轴由 Agent skill 完成",
        }
        _write_meta(d, meta)
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
