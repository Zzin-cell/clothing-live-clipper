from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        ff = which_ffmpeg()
        if ff:
            cand = Path(ff).with_name("ffprobe.exe")
            if cand.exists():
                ffprobe = str(cand)
    if not ffprobe:
        ff = require_ffmpeg()
        proc = subprocess.run(
            [ff, "-i", str(video)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        import re

        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
        if not m:
            raise FFmpegError("ffprobe missing and duration parse failed")
        h, mi, s = m.groups()
        return int((int(h) * 3600 + int(mi) * 60 + float(s)) * 1000)

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
        raise FFmpegError(proc.stderr or "ffprobe failed")
    return int(float(proc.stdout.strip()) * 1000)


def _probe_stream_size(video: str | Path) -> tuple[int, int]:
    ffprobe = which_ffprobe()
    if not ffprobe:
        ff = which_ffmpeg()
        if ff:
            cand = Path(ff).with_name("ffprobe.exe")
            if cand.exists():
                ffprobe = str(cand)
    if not ffprobe:
        return 1080, 1920
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not proc.stdout.strip():
        return 1080, 1920
    try:
        w, h = proc.stdout.strip().split("x")
        return max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)
    except Exception:
        return 1080, 1920


def cut_segment(
    video: str | Path,
    t0_ms: int,
    t1_ms: int,
    out_path: str | Path,
    reencode: bool = True,
    *,
    edge_fade_s: float = 0.03,
    target_w: int | None = None,
    target_h: int | None = None,
    fps: int = 30,
    zoom_style: str = "none",
) -> Path:
    """
    Invisible-edit style cut:
    - unify size/fps/audio so joins don't glitch
    - only micro audio/video edge fades (~30ms) to kill pops — not visible transitions
    - no zoom / no flashy effects
    """
    del zoom_style  # reserved; always none for invisible edit
    ffmpeg = require_ffmpeg()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ss = max(0.0, t0_ms / 1000.0)
    dur = max(0.12, (t1_ms - t0_ms) / 1000.0)

    # Micro-fade only (not a visible dissolve)
    fade = min(max(0.02, edge_fade_s), max(0.02, dur / 8.0))
    fade_out_st = max(0.0, dur - fade)

    w = (target_w or 1080) - ((target_w or 1080) % 2)
    h = (target_h or 1920) - ((target_h or 1920) % 2)

    # Fast path: avoid scale/pad when possible; micro audio fade only
    # Video filter kept light for speed
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps}"
    )
    # skip video fade for speed (was cosmetic); keep tiny audio de-click
    af = (
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={fade_out_st:.3f}:d={fade:.3f},"
        f"aresample=async=1:first_pts=0"
    )

    threads = str(max(1, min(4, (os.cpu_count() or 4))))
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{ss:.3f}",
        "-i",
        str(video),
        "-t",
        f"{dur:.3f}",
        "-vf",
        vf,
        "-af",
        af,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "26",
        "-threads",
        threads,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "44100",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    run_cmd(cmd)
    return out_path


def concat_segments(
    segment_paths: list[Path],
    out_mp4: str | Path,
    *,
    crossfade_s: float = 0.0,
) -> Path:
    """
    Direct seamless join (no visible transition).
    Pairwise concat filter keeps full duration reliably.
    crossfade_s is ignored for visual dissolves (policy: invisible hard cut).
    """
    del crossfade_s
    return _concat_pairwise_hard(segment_paths, Path(out_mp4))


def _concat_pairwise_hard(segment_paths: list[Path], out_mp4: Path) -> Path:
    """Single-pass concat demuxer (much faster than pairwise re-encode)."""
    ffmpeg = require_ffmpeg()
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not segment_paths:
        raise FFmpegError("no segments")
    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], out_mp4)
        return out_mp4

    with tempfile.TemporaryDirectory(prefix="clipper_concat_") as td:
        list_file = Path(td) / "list.txt"
        lines = []
        for p in segment_paths:
            # ffmpeg concat demuxer wants escaped single quotes on Windows paths
            ap = p.resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{ap}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        threads = str(max(2, min(8, (os.cpu_count() or 4))))
        # parts already same codec/params → stream copy is fastest & full duration
        cmd_copy = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        try:
            run_cmd(cmd_copy)
            return out_mp4
        except FFmpegError:
            # fallback re-encode once
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
                "ultrafast",
                "-crf",
                "23",
                "-threads",
                threads,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(out_mp4),
            ]
            run_cmd(cmd)
    return out_mp4


def apply_playback_speed(
    src_mp4: str | Path,
    out_mp4: str | Path,
    speed: float = 1.3,
) -> Path:
    ffmpeg = require_ffmpeg()
    src_mp4 = Path(src_mp4)
    out_mp4 = Path(out_mp4)
    if speed <= 0:
        raise FFmpegError(f"invalid speed: {speed}")
    if abs(speed - 1.0) < 0.01:
        if src_mp4.resolve() != out_mp4.resolve():
            shutil.copy2(src_mp4, out_mp4)
        return out_mp4

    def atempo_chain(sp: float) -> str:
        filters: list[str] = []
        rest = sp
        while rest > 2.0 + 1e-6:
            filters.append("atempo=2.0")
            rest /= 2.0
        while rest < 0.5 - 1e-6:
            filters.append("atempo=0.5")
            rest /= 0.5
        filters.append(f"atempo={rest:.5f}")
        return ",".join(filters)

    # No unsharp — keep natural look
    vf = f"setpts=PTS/{speed:.5f}"
    af = atempo_chain(speed)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src_mp4),
        "-filter:v",
        vf,
        "-filter:a",
        af,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-threads",
        str(max(2, min(8, (os.cpu_count() or 4)))),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",
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
    *,
    smooth: bool = True,
    crossfade_s: float = 0.0,
    edge_fade_s: float = 0.03,
    playback_speed: float = 1.0,
) -> Path:
    """
    Invisible-edit render:
    - direct seamless joins (no xfade dissolve)
    - micro edge fades only to avoid audio clicks
    - optional global playback speed (default 1.3) after join
    """
    del crossfade_s  # never visual dissolve
    video = Path(video)
    out_mp4 = Path(out_mp4)
    if work_dir is None:
        work_dir = out_mp4.parent / "_parts"
    work_dir = Path(work_dir)
    if work_dir.exists():
        for old in work_dir.glob("*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass
    work_dir.mkdir(parents=True, exist_ok=True)

    tw, th = _probe_stream_size(video)
    # prepare segment specs
    specs: list[tuple[int, int, int, Path]] = []
    for i, (t0, t1) in enumerate(segments):
        if t1 - t0 < 200:
            t1 = t0 + 200
        if smooth:
            t0 = max(0, t0 - 30)
            t1 = t1 + 30
        part = work_dir / f"part_{i:03d}.mp4"
        specs.append((i, t0, t1, part))

    def _cut_one(spec: tuple[int, int, int, Path]) -> tuple[int, Path]:
        i, t0, t1, part = spec
        cut_segment(
            video,
            t0,
            t1,
            part,
            reencode=True,
            edge_fade_s=edge_fade_s if smooth else 0.0,
            target_w=tw,
            target_h=th,
            fps=30,
            zoom_style="none",
        )
        return i, part

    # parallel cuts (I/O + encode bound) — biggest render speedup
    workers = max(2, min(8, (os.cpu_count() or 4)))
    parts_map: dict[int, Path] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_cut_one, sp) for sp in specs]
        for fut in as_completed(futs):
            i, part = fut.result()
            parts_map[i] = part
    parts = [parts_map[i] for i in range(len(specs))]

    joined = out_mp4
    speed = playback_speed if playback_speed and playback_speed > 0 else 1.0
    if abs(speed - 1.0) > 0.01:
        joined = work_dir / "_joined_1x.mp4"

    # Always direct connect (no dissolve)
    concat_segments(parts, joined, crossfade_s=0.0)

    if abs(speed - 1.0) > 0.01:
        apply_playback_speed(joined, out_mp4, speed=speed)
    elif joined != out_mp4:
        shutil.copy2(joined, out_mp4)

    # If still short after speed (material shortage), slight retime toward ~58s
    try:
        final_ms = probe_duration_ms(out_mp4)
        if final_ms > 0 and final_ms < 56_500:
            target_ms = 58_500
            factor = min(1.12, max(1.01, target_ms / final_ms))
            tmp = out_mp4.with_suffix(".retime.mp4")
            apply_playback_speed(out_mp4, tmp, speed=1.0 / factor)
            tmp.replace(out_mp4)
    except Exception:
        pass
    return out_mp4
