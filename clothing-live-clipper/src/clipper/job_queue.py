"""
Stable LAN job queue + pipeline resource slots.

A/B/C/D improvements:
- A: render slot + sensible active defaults for pipeline overlap
- B: ETA from sliding average of job durations
- C: queue/ui build version for deploy alignment
- D: warm-extract only for front-of-queue jobs (background)
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Bump when queue/UI contract changes (frontend compares).
QUEUE_BUILD = "20260808-abcd-pipeline"
UI_BUILD_EXPECTED = "jy71-learn-all-paths"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name) or default))
    except Exception:
        return max(minimum, default)


def _queue_mode() -> str:
    m = (os.environ.get("CLIPPER_QUEUE_MODE") or "stable").strip().lower()
    return m if m in {"stable", "throughput"} else "stable"


@dataclass
class QueueConfig:
    max_active_jobs: int = 3  # pipeline: one asr + one render + one wait/llm
    asr_slots: int = 1
    llm_slots: int = 1
    render_slots: int = 1
    warm_extract_slots: int = 1
    warm_front_n: int = 2  # only front N pending get background warm extract

    @classmethod
    def from_env(cls) -> "QueueConfig":
        mode = _queue_mode()
        if mode == "throughput":
            return cls(
                max_active_jobs=_env_int("CLIPPER_MAX_CONCURRENT_JOBS", 4),
                asr_slots=_env_int("CLIPPER_ASR_SLOTS", 1),
                llm_slots=_env_int("CLIPPER_LLM_SLOTS", 2),
                render_slots=_env_int("CLIPPER_RENDER_SLOTS", 2),
                warm_extract_slots=_env_int("CLIPPER_WARM_EXTRACT_SLOTS", 2),
                warm_front_n=_env_int("CLIPPER_WARM_FRONT_N", 3),
            )
        return cls(
            max_active_jobs=_env_int("CLIPPER_MAX_CONCURRENT_JOBS", 3),
            asr_slots=_env_int("CLIPPER_ASR_SLOTS", 1),
            llm_slots=_env_int("CLIPPER_LLM_SLOTS", 1),
            render_slots=_env_int("CLIPPER_RENDER_SLOTS", 1),
            warm_extract_slots=_env_int("CLIPPER_WARM_EXTRACT_SLOTS", 1),
            warm_front_n=_env_int("CLIPPER_WARM_FRONT_N", 2),
        )


class JobQueue:
    """Process-local FIFO dispatcher with resource semaphores."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._cfg = QueueConfig.from_env()
        self._pending: deque[str] = deque()
        self._job_dirs: dict[str, Path] = {}
        self._active: set[str] = set()
        self._known: set[str] = set()
        self._dispatcher_started = False
        self._model_warmed = False
        self._model_warmup_note = "not_started"
        self._asr_sem = threading.Semaphore(self._cfg.asr_slots)
        self._llm_sem = threading.Semaphore(self._cfg.llm_slots)
        self._render_sem = threading.Semaphore(self._cfg.render_slots)
        self._warm_sem = threading.Semaphore(self._cfg.warm_extract_slots)
        self._asr_inflight = 0
        self._llm_inflight = 0
        self._render_inflight = 0
        self._warm_inflight = 0
        self._process_fn: Callable[[Path], None] | None = None
        self._write_meta_fn: Callable[[Path, dict[str, Any]], None] | None = None
        self._read_meta_fn: Callable[[Path], dict[str, Any]] | None = None
        self._extract_fn: Callable[[Path, Path], None] | None = None
        # duration samples seconds (queued/started → finished)
        self._dur_samples: deque[float] = deque(maxlen=12)
        self._warming_ids: set[str] = set()

    def configure_handlers(
        self,
        *,
        process_fn: Callable[[Path], None],
        write_meta_fn: Callable[[Path, dict[str, Any]], None],
        read_meta_fn: Callable[[Path], dict[str, Any]],
        extract_fn: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._process_fn = process_fn
        self._write_meta_fn = write_meta_fn
        self._read_meta_fn = read_meta_fn
        if extract_fn is not None:
            self._extract_fn = extract_fn
        self.reload_config()

    def reload_config(self) -> None:
        with self._lock:
            old = self._cfg
            self._cfg = QueueConfig.from_env()
            idle = (
                self._asr_inflight == 0
                and self._llm_inflight == 0
                and self._render_inflight == 0
                and self._warm_inflight == 0
            )
            if idle and (
                old.asr_slots != self._cfg.asr_slots
                or old.llm_slots != self._cfg.llm_slots
                or old.render_slots != self._cfg.render_slots
                or old.warm_extract_slots != self._cfg.warm_extract_slots
            ):
                self._asr_sem = threading.Semaphore(self._cfg.asr_slots)
                self._llm_sem = threading.Semaphore(self._cfg.llm_slots)
                self._render_sem = threading.Semaphore(self._cfg.render_slots)
                self._warm_sem = threading.Semaphore(self._cfg.warm_extract_slots)

    def ensure_dispatcher(self) -> None:
        with self._lock:
            if self._dispatcher_started:
                return
            self._dispatcher_started = True
            threading.Thread(
                target=self._dispatch_loop, name="job-queue-dispatcher", daemon=True
            ).start()

    def enqueue(self, job_dir: Path) -> dict[str, Any]:
        self.ensure_dispatcher()
        job_dir = Path(job_dir)
        job_id = job_dir.name
        with self._cv:
            self._job_dirs[job_id] = job_dir
            if job_id in self._active:
                return self._job_view_locked(job_id)
            first = job_id not in self._known
            if first:
                self._known.add(job_id)
                self._pending.append(job_id)
                if self._read_meta_fn and self._write_meta_fn:
                    try:
                        meta = self._read_meta_fn(job_dir) or {}
                        if not meta.get("queued_at"):
                            meta["queued_at"] = _utc_now()
                        if not meta.get("created_at"):
                            meta["created_at"] = meta["queued_at"]
                        meta["status"] = "queued"
                        meta["stage"] = "queued"
                        self._write_meta_fn(job_dir, meta)
                    except Exception:
                        pass
            self._write_queue_meta_locked(job_id, stage="queued", detail=None)
            self._cv.notify_all()
            return self._job_view_locked(job_id)

    def mark_finished(self, job_id: str, *, success: bool | None = None, duration_s: float | None = None) -> None:
        with self._cv:
            job_dir = self._job_dirs.get(job_id)
            if duration_s is None and job_dir and self._read_meta_fn:
                try:
                    meta = self._read_meta_fn(job_dir) or {}
                    duration_s = self._infer_duration_s(meta)
                except Exception:
                    duration_s = None
            if duration_s is not None and duration_s > 1:
                self._dur_samples.append(float(duration_s))
            self._active.discard(job_id)
            self._known.discard(job_id)
            self._job_dirs.pop(job_id, None)
            self._warming_ids.discard(job_id)
            for jid in list(self._pending):
                self._write_queue_meta_locked(jid, stage="queued", detail=None)
            self._cv.notify_all()

    def note_job_duration(self, job_dir: Path) -> None:
        """Call when a job completes successfully (or fails after work)."""
        if not self._read_meta_fn:
            return
        try:
            meta = self._read_meta_fn(job_dir) or {}
            d = self._infer_duration_s(meta)
            if d is not None and d > 1:
                with self._lock:
                    self._dur_samples.append(float(d))
        except Exception:
            pass

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._active

    def active_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._active)

    def avg_job_seconds(self) -> float | None:
        with self._lock:
            if not self._dur_samples:
                # heuristic default for clothing clips
                return 180.0
            return sum(self._dur_samples) / len(self._dur_samples)

    def eta_seconds_for(self, job_id: str) -> int | None:
        pos, _total = self.queue_position(job_id)
        avg = self.avg_job_seconds() or 180.0
        if pos <= 0:
            # active: rough remaining = half avg if unknown
            return max(15, int(avg * 0.45))
        # jobs ahead ≈ pos-1 fully + current partial
        ahead = max(0, pos - 1)
        return int(ahead * avg + avg * 0.5)

    def acquire_asr(self, job_dir: Path) -> None:
        job_id = Path(job_dir).name
        pos = self.queue_position(job_id)
        wait_s = self._wait_s_for(job_dir)
        eta = self.eta_seconds_for(job_id)
        detail = self._wait_detail("听写", pos, wait_s, eta)
        self._set_stage(
            job_dir,
            "wait_asr",
            20,
            detail,
            extra={
                "queue_pos": pos[0],
                "queue_total": pos[1],
                "queue_wait_s": wait_s,
                "eta_s": eta,
            },
        )
        self._asr_sem.acquire()
        with self._lock:
            self._asr_inflight += 1
        self._set_stage(job_dir, "asr", 28, "正在听写（已获得听写槽）")

    def release_asr(self) -> None:
        with self._lock:
            self._asr_inflight = max(0, self._asr_inflight - 1)
        self._asr_sem.release()
        with self._cv:
            self._cv.notify_all()

    def acquire_llm(self, job_dir: Path) -> None:
        job_id = Path(job_dir).name
        pos = self.queue_position(job_id)
        wait_s = self._wait_s_for(job_dir)
        eta = self.eta_seconds_for(job_id)
        self._set_stage(
            job_dir,
            "wait_llm",
            52,
            self._wait_detail("LLM排片", pos, wait_s, eta),
            extra={
                "queue_pos": pos[0],
                "queue_total": pos[1],
                "queue_wait_s": wait_s,
                "eta_s": eta,
            },
        )
        self._llm_sem.acquire()
        with self._lock:
            self._llm_inflight += 1
        self._set_stage(job_dir, "llm_plan", 55, "LLM 逻辑处理口播稿…")

    def release_llm(self) -> None:
        with self._lock:
            self._llm_inflight = max(0, self._llm_inflight - 1)
        self._llm_sem.release()
        with self._cv:
            self._cv.notify_all()

    def acquire_render(self, job_dir: Path) -> None:
        wait_s = self._wait_s_for(job_dir)
        self._set_stage(
            job_dir,
            "wait_render",
            72,
            f"等待渲染槽 · 已等{self._format_wait(wait_s)}",
            extra={"queue_wait_s": wait_s},
        )
        self._render_sem.acquire()
        with self._lock:
            self._render_inflight += 1
        self._set_stage(job_dir, "render", 80, "渲染成片（已获得渲染槽）")

    def release_render(self) -> None:
        with self._lock:
            self._render_inflight = max(0, self._render_inflight - 1)
        self._render_sem.release()
        with self._cv:
            self._cv.notify_all()

    def should_warm_extract(self, job_id: str) -> bool:
        """D: only front-of-queue (or already active) jobs do warm extract."""
        with self._lock:
            if job_id in self._active:
                return True
            try:
                idx = list(self._pending).index(job_id) + 1
            except ValueError:
                return False
            return 0 < idx <= self._cfg.warm_front_n

    def acquire_warm_extract(self, job_dir: Path) -> bool:
        job_id = Path(job_dir).name
        if not self.should_warm_extract(job_id):
            # Not in warm front: extract will still happen later without warm status,
            # or skip warm contention — process_job_dir may extract without this slot.
            return False
        if self._warm_sem.acquire(blocking=False):
            with self._lock:
                self._warm_inflight += 1
            self._set_stage(job_dir, "warm_extract", 8, "预热抽音频中…")
            return True
        # Front job: block briefly for warm slot
        pos = self.queue_position(job_id)
        wait_s = self._wait_s_for(job_dir)
        self._set_stage(
            job_dir,
            "queued",
            4,
            self._wait_detail("预热空位", pos, wait_s, self.eta_seconds_for(job_id)),
            extra={"queue_pos": pos[0], "queue_total": pos[1]},
        )
        self._warm_sem.acquire()
        with self._lock:
            self._warm_inflight += 1
        self._set_stage(job_dir, "warm_extract", 8, "预热抽音频中…")
        return True

    def release_warm_extract(self) -> None:
        with self._lock:
            self._warm_inflight = max(0, self._warm_inflight - 1)
        self._warm_sem.release()
        with self._cv:
            self._cv.notify_all()

    def queue_position(self, job_id: str) -> tuple[int, int]:
        with self._lock:
            if job_id in self._active:
                return 0, len(self._pending) + len(self._active)
            try:
                idx = list(self._pending).index(job_id) + 1
            except ValueError:
                idx = 0
            total = len(self._pending) + len(self._active)
            return idx, total

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg = None
            if self._dur_samples:
                avg = round(sum(self._dur_samples) / len(self._dur_samples), 1)
            return {
                "mode": _queue_mode(),
                "queue_build": QUEUE_BUILD,
                "ui_build_expected": UI_BUILD_EXPECTED,
                "queued": list(self._pending),
                "active": sorted(self._active),
                "active_count": len(self._active),
                "max_active_jobs": self._cfg.max_active_jobs,
                "asr_slots": self._cfg.asr_slots,
                "llm_slots": self._cfg.llm_slots,
                "render_slots": self._cfg.render_slots,
                "warm_extract_slots": self._cfg.warm_extract_slots,
                "warm_front_n": self._cfg.warm_front_n,
                "asr_inflight": self._asr_inflight,
                "llm_inflight": self._llm_inflight,
                "render_inflight": self._render_inflight,
                "warm_inflight": self._warm_inflight,
                "model_warmed": self._model_warmed,
                "model_warmup_note": self._model_warmup_note,
                "avg_job_s": avg,
                "updated_at": _utc_now(),
            }

    def set_model_warmup(self, ok: bool, note: str = "") -> None:
        with self._lock:
            self._model_warmed = bool(ok)
            self._model_warmup_note = note or ("ok" if ok else "failed")

    def model_warmed(self) -> bool:
        with self._lock:
            return self._model_warmed

    # ---- internals ----
    def _infer_duration_s(self, meta: dict[str, Any]) -> float | None:
        for key in ("started_at", "queued_at", "created_at"):
            raw = str(meta.get(key) or "").strip()
            if not raw:
                continue
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
            except Exception:
                continue
        if meta.get("asr_seconds") is not None:
            try:
                return float(meta.get("asr_seconds") or 0) + 60.0
            except Exception:
                return None
        return None

    def _queue_wait_seconds(self, meta: dict[str, Any]) -> int:
        for key in ("queued_at", "created_at"):
            raw = str(meta.get(key) or "").strip()
            if not raw:
                continue
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
            except Exception:
                continue
        return 0

    def _wait_s_for(self, job_dir: Path) -> int:
        if not self._read_meta_fn:
            return 0
        try:
            return self._queue_wait_seconds(self._read_meta_fn(job_dir) or {})
        except Exception:
            return 0

    def _format_wait(self, secs: int) -> str:
        secs = max(0, int(secs))
        if secs < 60:
            return f"{secs}s"
        m, s = divmod(secs, 60)
        if m < 60:
            return f"{m}分{s:02d}秒"
        h, m = divmod(m, 60)
        return f"{h}小时{m:02d}分"

    def _format_eta(self, secs: int | None) -> str:
        if secs is None:
            return ""
        return f"预计还需{self._format_wait(secs)}"

    def _wait_detail(
        self,
        what: str,
        pos: tuple[int, int],
        wait_s: int | None = None,
        eta_s: int | None = None,
    ) -> str:
        p, total = pos
        warm = "模型已预热" if self._model_warmed else "模型未预热"
        parts: list[str] = []
        if p > 0:
            parts.append(f"排队等待{what}")
            parts.append(f"第 {p}/{max(total, p)} 位")
        else:
            parts.append(f"等待{what}槽")
        if wait_s is not None:
            parts.append(f"已等{self._format_wait(wait_s)}")
        if eta_s is not None:
            parts.append(self._format_eta(eta_s))
        parts.append(warm)
        return " · ".join(parts)

    def _job_view_locked(self, job_id: str) -> dict[str, Any]:
        try:
            pos = list(self._pending).index(job_id) + 1 if job_id not in self._active else 0
        except ValueError:
            pos = 0
        total = len(self._pending) + len(self._active)
        eta = self.eta_seconds_for(job_id)
        return {
            "job_id": job_id,
            "queued": job_id in self._pending,
            "active": job_id in self._active,
            "queue_pos": pos,
            "queue_total": total,
            "eta_s": eta,
            "model_warmed": self._model_warmed,
            "queue_build": QUEUE_BUILD,
            "ui_build_expected": UI_BUILD_EXPECTED,
            "queue": self.snapshot(),
        }

    def _write_queue_meta_locked(self, job_id: str, *, stage: str, detail: str | None) -> None:
        if not self._write_meta_fn or not self._read_meta_fn:
            return
        job_dir = self._job_dirs.get(job_id)
        if not job_dir:
            return
        try:
            meta = self._read_meta_fn(job_dir) or {}
            try:
                pos = list(self._pending).index(job_id) + 1 if job_id in self._pending else 0
            except ValueError:
                pos = 0
            total = len(self._pending) + len(self._active)
            if not meta.get("queued_at"):
                meta["queued_at"] = meta.get("created_at") or _utc_now()
            wait_s = self._queue_wait_seconds(meta)
            eta_s = self.eta_seconds_for(job_id)
            meta["queue_wait_s"] = wait_s
            meta["eta_s"] = eta_s
            if job_id in self._pending:
                meta["status"] = "queued"
            meta["stage"] = stage
            meta["queue_pos"] = pos
            meta["queue_total"] = total
            meta["progress"] = max(1, int(meta.get("progress") or 1))
            if detail:
                meta["stage_detail"] = detail
            else:
                meta["stage_detail"] = self._wait_detail("调度", (pos, total), wait_s, eta_s)
            meta["updated_at"] = _utc_now()
            meta["queue_build"] = QUEUE_BUILD
            meta["ui_build_expected"] = UI_BUILD_EXPECTED
            meta["warmup"] = {
                "model": self._model_warmed,
                "model_note": self._model_warmup_note,
            }
            meta["resource"] = {
                "active_jobs": len(self._active),
                "max_active_jobs": self._cfg.max_active_jobs,
                "asr_slots": self._cfg.asr_slots,
                "llm_slots": self._cfg.llm_slots,
                "render_slots": self._cfg.render_slots,
            }
            self._write_meta_fn(job_dir, meta)
        except Exception:
            pass

    def _set_stage(
        self,
        job_dir: Path,
        stage: str,
        pct: int,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self._write_meta_fn or not self._read_meta_fn:
            return
        try:
            meta = self._read_meta_fn(job_dir) or {}
            if stage in {"queued"} and meta.get("status") not in {"processing", "success", "failed"}:
                meta["status"] = "queued"
            else:
                if meta.get("status") not in {"success", "failed", "success_partial"}:
                    meta["status"] = "processing"
            meta["stage"] = stage
            meta["progress"] = max(0, min(100, int(pct)))
            meta["stage_detail"] = detail
            meta["updated_at"] = _utc_now()
            meta["queue_build"] = QUEUE_BUILD
            meta["ui_build_expected"] = UI_BUILD_EXPECTED
            if extra:
                meta.update(extra)
            warm = {"model": self._model_warmed, "model_note": self._model_warmup_note}
            prev = meta.get("warmup") if isinstance(meta.get("warmup"), dict) else {}
            warm.update({k: v for k, v in prev.items() if k not in warm})
            meta["warmup"] = warm
            self._write_meta_fn(job_dir, meta)
        except Exception:
            pass

    def _maybe_background_warm(self) -> None:
        """D: warm-extract only front pending jobs (background, non-active)."""
        if not self._extract_fn or not self._read_meta_fn:
            return
        with self._lock:
            front = list(self._pending)[: self._cfg.warm_front_n]
            cfg_n = self._cfg.warm_front_n
        for job_id in front:
            with self._lock:
                if job_id in self._warming_ids or job_id in self._active:
                    continue
                job_dir = self._job_dirs.get(job_id)
                if not job_dir:
                    continue
                self._warming_ids.add(job_id)
            # spawn warm worker
            threading.Thread(
                target=self._bg_warm_one,
                args=(job_id, job_dir),
                name=f"warm-{job_id[:12]}",
                daemon=True,
            ).start()
            # only launch limited parallel warm threads
            with self._lock:
                if len(self._warming_ids) >= max(1, min(cfg_n, self._cfg.warm_extract_slots)):
                    break

    def _bg_warm_one(self, job_id: str, job_dir: Path) -> None:
        try:
            if not self._extract_fn:
                return
            video = None
            uploads = job_dir / "uploads"
            if uploads.exists():
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
                        video = p
                        break
            if not video:
                return
            wav = job_dir / "asr_work" / "audio_16k.wav"
            if wav.exists() and wav.stat().st_size > 1000:
                return
            # only if still front
            if not self.should_warm_extract(job_id):
                return
            got = self.acquire_warm_extract(job_dir)
            if not got:
                return
            try:
                self._extract_fn(video, wav)
                if self._read_meta_fn and self._write_meta_fn:
                    meta = self._read_meta_fn(job_dir) or {}
                    warm = meta.get("warmup") if isinstance(meta.get("warmup"), dict) else {}
                    warm = dict(warm or {})
                    warm["extract"] = True
                    meta["warmup"] = warm
                    meta["stage_detail"] = (meta.get("stage_detail") or "") + " · 音频已预热"
                    self._write_meta_fn(job_dir, meta)
            finally:
                self.release_warm_extract()
        except Exception as e:
            print(f"[warm] {job_id} failed: {e}", flush=True)
        finally:
            with self._lock:
                self._warming_ids.discard(job_id)

    def _dispatch_loop(self) -> None:
        while True:
            job_dir: Path | None = None
            job_id: str | None = None
            with self._cv:
                while True:
                    self.reload_config()
                    while self._pending and self._pending[0] not in self._job_dirs:
                        self._pending.popleft()
                    for jid in list(self._pending):
                        self._write_queue_meta_locked(jid, stage="queued", detail=None)
                    # background warm for front of queue
                    try:
                        self._maybe_background_warm()
                    except Exception:
                        pass
                    if self._pending and len(self._active) < self._cfg.max_active_jobs:
                        job_id = self._pending.popleft()
                        job_dir = self._job_dirs.get(job_id)
                        if not job_dir:
                            continue
                        self._active.add(job_id)
                        break
                    self._cv.wait(timeout=1.0)
            if not job_id or not job_dir or not self._process_fn:
                continue
            with self._lock:
                for jid in list(self._pending):
                    self._write_queue_meta_locked(jid, stage="queued", detail=None)

            def _run(jid: str = job_id, d: Path = job_dir) -> None:
                try:
                    if self._process_fn:
                        self._process_fn(d)
                finally:
                    try:
                        self.note_job_duration(d)
                    except Exception:
                        pass
                    self.mark_finished(jid)

            threading.Thread(target=_run, name=f"job-{job_id}", daemon=True).start()


QUEUE = JobQueue()


def start_model_warmup_async() -> None:
    def _warm() -> None:
        QUEUE.set_model_warmup(False, "warming")
        try:
            import sys

            root = Path(__file__).resolve().parents[2]
            scripts = root / "scripts"
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            from agent_clip_video import _get_whisper_model, resolve_local_model  # type: ignore

            model = resolve_local_model()
            _get_whisper_model(model)
            QUEUE.set_model_warmup(True, f"ready:{model}")
            print(f"[warmup] whisper ready model={model}", flush=True)
        except Exception as e:
            QUEUE.set_model_warmup(False, f"failed:{type(e).__name__}:{e}"[:200])
            print(f"[warmup] failed: {e}", flush=True)

    threading.Thread(target=_warm, name="asr-model-warmup", daemon=True).start()
