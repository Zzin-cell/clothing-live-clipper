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

from clipper.config import Settings
from clipper.media import which_ffmpeg
from clipper.pipeline import run_pipeline

APP_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
JOBS_DIR = APP_ROOT / "output" / "web_jobs"
SAMPLE_TRANSCRIPT = APP_ROOT / "tests" / "fixtures" / "sample_transcript.json"

ALLOWED_TRANSCRIPT = {".json", ".srt"}
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(name: str | None, default: str) -> str:
    raw = Path(name or default).name
    return raw if raw else default


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
        return {
            "ok": True,
            "ffmpeg": bool(which_ffmpeg()),
            "sample_transcript": SAMPLE_TRANSCRIPT.exists(),
            "time": _utc_now(),
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
        meta["files"] = {
            "plan": (d / "plan.json").exists(),
            "review": (d / "review.md").exists(),
            "clips": (d / "clips.json").exists(),
            "final": (d / "final.mp4").exists(),
            "result": (d / "result.json").exists(),
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

    @app.post("/api/jobs")
    async def create_job(
        transcript: UploadFile | None = File(default=None),
        video: UploadFile | None = File(default=None),
        use_sample: bool = Form(default=False),
        target_seconds: int = Form(default=60),
        render: bool = Form(default=True),
    ) -> dict[str, Any]:
        target_seconds = int(target_seconds)
        if target_seconds < 15 or target_seconds > 180:
            raise HTTPException(status_code=400, detail="target_seconds must be 15-180")

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        d = _job_dir(job_id)
        uploads = d / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = {
            "job_id": job_id,
            "status": "processing",
            "created_at": _utc_now(),
            "target_seconds": target_seconds,
            "render_requested": render,
            "error": None,
        }
        (d / "job_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        try:
            # transcript
            transcript_path: Path
            if use_sample:
                if not SAMPLE_TRANSCRIPT.exists():
                    raise HTTPException(status_code=500, detail="sample transcript missing")
                transcript_path = uploads / "transcript.json"
                shutil.copy2(SAMPLE_TRANSCRIPT, transcript_path)
                meta["transcript_source"] = "sample"
            else:
                if transcript is None or not transcript.filename:
                    raise HTTPException(
                        status_code=400,
                        detail="请上传转写文件（json/srt），或勾选使用示例转写",
                    )
                suffix = Path(transcript.filename).suffix.lower()
                if suffix not in ALLOWED_TRANSCRIPT:
                    raise HTTPException(
                        status_code=400, detail="转写仅支持 .json / .srt"
                    )
                transcript_path = uploads / _safe_name(transcript.filename, "transcript.json")
                if transcript_path.suffix.lower() not in ALLOWED_TRANSCRIPT:
                    transcript_path = uploads / f"transcript{suffix}"
                await _save_upload(transcript, transcript_path)
                meta["transcript_source"] = transcript.filename

            video_path: Path | None = None
            if video is not None and video.filename:
                suffix = Path(video.filename).suffix.lower()
                if suffix not in ALLOWED_VIDEO:
                    raise HTTPException(
                        status_code=400,
                        detail=f"视频格式不支持: {suffix}",
                    )
                video_path = uploads / _safe_name(video.filename, f"video{suffix}")
                await _save_upload(video, video_path)
                meta["video_source"] = video.filename
            else:
                meta["video_source"] = None

            # if no video, cannot render
            do_render = bool(render and video_path is not None)
            settings = Settings.from_env()
            settings = Settings(
                target_duration_s=target_seconds,
                golden_s=min(20, max(8, target_seconds // 3)),
                cta_s=min(10, max(5, target_seconds // 6)),
                min_clip_ms=settings.min_clip_ms,
                max_clip_ms=settings.max_clip_ms,
                golden_weight_ratio=settings.golden_weight_ratio,
                llm_api_key=settings.llm_api_key,
                llm_base_url=settings.llm_base_url,
                llm_model=settings.llm_model,
            )

            result = run_pipeline(
                video=video_path,
                transcript_path=transcript_path,
                out_dir=d,
                settings=settings,
                render=do_render,
            )

            meta.update(
                {
                    "status": "success",
                    "finished_at": _utc_now(),
                    "output_mp4": bool(result.output_mp4),
                    "render_skipped": result.meta.get("render_skipped"),
                    "render_error": result.meta.get("render_error"),
                    "golden20_passed": bool(result.plan.golden20_passed)
                    if result.plan
                    else False,
                    "duration_s": (result.plan.total_duration_ms / 1000.0)
                    if result.plan
                    else 0,
                    "warnings": result.plan.warnings if result.plan else [],
                    "selected_clips": len(result.plan.all_slots()) if result.plan else 0,
                }
            )
        except HTTPException:
            meta["status"] = "failed"
            meta["finished_at"] = _utc_now()
            (d / "job_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise
        except Exception as e:  # noqa: BLE001 - surface to UI
            meta["status"] = "failed"
            meta["error"] = str(e)
            meta["finished_at"] = _utc_now()
            (d / "job_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise HTTPException(status_code=500, detail=str(e)) from e

        (d / "job_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # return detail payload
        return get_job(job_id)

    return app


app = create_app()
