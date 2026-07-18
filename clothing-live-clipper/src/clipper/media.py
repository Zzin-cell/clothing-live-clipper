from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def which_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def which_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def require_ffmpeg() -> str:
    exe = which_ffmpeg()
    if not exe:
        raise FFmpegError(
            "未找到 ffmpeg，请先安装并加入 PATH。Windows 可: winget install ffmpeg"
        )
    return exe


def run_cmd(cmd: list[str]) -> None:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
        )


def probe_duration_ms(video: str | Path) -> int:
    ffprobe = which_ffprobe()
    if not ffprobe:
        raise FFmpegError("未找到 ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr)
    return int(float(proc.stdout.strip()) * 1000)


def cut_segment(
    video: str | Path,
    t0_ms: int,
    t1_ms: int,
    out_path: str | Path,
    reencode: bool = True,
) -> Path:
    ffmpeg = require_ffmpeg()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ss = t0_ms / 1000.0
    dur = max(0.05, (t1_ms - t0_ms) / 1000.0)
    if reencode:
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{ss:.3f}",
            "-i",
            str(video),
            "-t",
            f"{dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{ss:.3f}",
            "-i",
            str(video),
            "-t",
            f"{dur:.3f}",
            "-c",
            "copy",
            str(out_path),
        ]
    run_cmd(cmd)
    return out_path


def concat_segments(segment_paths: list[Path], out_mp4: str | Path) -> Path:
    ffmpeg = require_ffmpeg()
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not segment_paths:
        raise FFmpegError("no segments to concat")

    with tempfile.TemporaryDirectory(prefix="clipper_concat_") as td:
        list_file = Path(td) / "list.txt"
        # concat demuxer needs escaped paths; use absolute forward-ish for ffmpeg on win
        lines = []
        for p in segment_paths:
            ap = p.resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{ap}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        run_cmd(cmd)
    return out_mp4


def render_plan(
    video: str | Path,
    segments: list[tuple[int, int]],
    out_mp4: str | Path,
    work_dir: str | Path | None = None,
) -> Path:
    """segments: list of (t0_ms, t1_ms) in output order."""
    video = Path(video)
    out_mp4 = Path(out_mp4)
    if work_dir is None:
        work_dir = out_mp4.parent / "_parts"
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    for i, (t0, t1) in enumerate(segments):
        part = work_dir / f"part_{i:03d}.mp4"
        cut_segment(video, t0, t1, part, reencode=True)
        parts.append(part)
    return concat_segments(parts, out_mp4)
