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
    ffprobe = which_ffprobe() or "ffprobe"
    cmd = [
        ffprobe if which_ffprobe() else "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    # prefer sibling of ffmpeg if ffprobe missing name but ffmpeg exists
    if not which_ffprobe():
        ff = which_ffmpeg()
        if ff:
            cand = Path(ff).with_name("ffprobe.exe")
            if cand.exists():
                cmd[0] = str(cand)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr or "ffprobe failed")
    return int(float(proc.stdout.strip()) * 1000)


def _probe_stream_size(video: str | Path) -> tuple[int, int]:
    """Return (width, height) of first video stream; fallback 1080x1920."""
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
        return int(w), int(h)
    except Exception:
        return 1080, 1920


def cut_segment(
    video: str | Path,
    t0_ms: int,
    t1_ms: int,
    out_path: str | Path,
    reencode: bool = True,
    *,
    edge_fade_s: float = 0.10,
    target_w: int | None = None,
    target_h: int | None = None,
    fps: int = 30,
) -> Path:
    """Cut one segment with soft edge fades and normalized video/audio."""
    ffmpeg = require_ffmpeg()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ss = max(0.0, t0_ms / 1000.0)
    dur = max(0.12, (t1_ms - t0_ms) / 1000.0)

    # keep fade shorter than half duration
    fade = min(edge_fade_s, max(0.04, dur / 4.0))
    fade_out_st = max(0.0, dur - fade)

    if not reencode:
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

    w = target_w or 1080
    h = target_h or 1920
    # scale+pad to uniform size, constant fps, soft video/audio edge fades
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},"
        f"fade=t=in:st=0:d={fade:.3f},"
        f"fade=t=out:st={fade_out_st:.3f}:d={fade:.3f}"
    )
    af = (
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={fade_out_st:.3f}:d={fade:.3f},"
        f"aresample=async=1:first_pts=0"
    )
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
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "44100",
        "-ac",
        "2",
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
    crossfade_s: float = 0.12,
) -> Path:
    """
    Smooth concat:
    - 1 segment: copy/re-encode as-is
    - 2+ segments: xfade video + acrossfade audio when each part is long enough
    - fallback: concat demuxer re-encode
    """
    ffmpeg = require_ffmpeg()
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not segment_paths:
        raise FFmpegError("no segments to concat")
    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], out_mp4)
        return out_mp4

    # durations
    durs: list[float] = []
    for p in segment_paths:
        try:
            durs.append(max(0.05, probe_duration_ms(p) / 1000.0))
        except Exception:
            durs.append(1.0)

    # xfade disabled by default: multi-clip graphs often produce wrong duration.
    # Edge fades on each part already soften hard cuts.
    if crossfade_s and crossfade_s > 0.01 and len(segment_paths) <= 12:
        cf = max(0.06, min(float(crossfade_s), 0.20))
        can_xfade = all(d > cf * 2.2 for d in durs)
        if can_xfade:
            try:
                return _concat_xfade(segment_paths, durs, out_mp4, cf)
            except FFmpegError:
                pass

    return _concat_demuxer(segment_paths, out_mp4)


def _concat_demuxer(segment_paths: list[Path], out_mp4: Path) -> Path:
    """Reliable full-length join: pairwise concat filter (avoids demuxer/graph bugs)."""
    ffmpeg = require_ffmpeg()
    if not segment_paths:
        raise FFmpegError("no segments")
    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], out_mp4)
        return out_mp4

    def _pair_concat(a: Path, b: Path, dest: Path) -> None:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(a),
            "-i",
            str(b),
            "-filter_complex",
            "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(dest),
        ]
        run_cmd(cmd)

    with tempfile.TemporaryDirectory(prefix="clipper_pair_") as td:
        td_path = Path(td)
        cur = segment_paths[0]
        for i, nxt in enumerate(segment_paths[1:], start=1):
            dest = td_path / f"join_{i:03d}.mp4"
            _pair_concat(cur, nxt, dest)
            cur = dest
        shutil.copy2(cur, out_mp4)
    return out_mp4


def _concat_xfade(
    segment_paths: list[Path],
    durs: list[float],
    out_mp4: Path,
    crossfade_s: float,
) -> Path:
    """Chain xfade + acrossfade across all parts."""
    ffmpeg = require_ffmpeg()
    n = len(segment_paths)
    cmd: list[str] = [ffmpeg, "-y"]
    for p in segment_paths:
        cmd += ["-i", str(p)]

    # Build filter graph
    # v0 = first video stream, a0 = first audio
    filters: list[str] = []
    # ensure each input labeled
    # progressive offsets for xfade
    # offset_i = sum(d[0..i]) - crossfade * i
    v_prev = "[0:v]"
    a_prev = "[0:a]"
    timeline = durs[0]
    for i in range(1, n):
        v_out = f"[v{i}]"
        a_out = f"[a{i}]"
        offset = max(0.0, timeline - crossfade_s)
        filters.append(
            f"{v_prev}[{i}:v]xfade=transition=fade:duration={crossfade_s:.3f}:offset={offset:.3f}{v_out}"
        )
        filters.append(
            f"{a_prev}[{i}:a]acrossfade=d={crossfade_s:.3f}:c1=tri:c2=tri{a_out}"
        )
        v_prev, a_prev = v_out, a_out
        timeline = timeline + durs[i] - crossfade_s

    # final map
    fc = ";".join(filters)
    cmd += [
        "-filter_complex",
        fc,
        "-map",
        v_prev,
        "-map",
        a_prev,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
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
    """Speed up final video+audio. speed=1.3 → 1.3x playback, shorter duration."""
    ffmpeg = require_ffmpeg()
    src_mp4 = Path(src_mp4)
    out_mp4 = Path(out_mp4)
    if speed <= 0:
        raise FFmpegError(f"invalid speed: {speed}")
    if abs(speed - 1.0) < 0.01:
        if src_mp4.resolve() != out_mp4.resolve():
            shutil.copy2(src_mp4, out_mp4)
        return out_mp4

    # atempo only supports 0.5–2.0; chain if needed
    def atempo_chain(sp: float) -> str:
        filters: list[str] = []
        rest = sp
        # convert duration factor: playback speed S means tempo = S
        while rest > 2.0 + 1e-6:
            filters.append("atempo=2.0")
            rest /= 2.0
        while rest < 0.5 - 1e-6:
            filters.append("atempo=0.5")
            rest /= 0.5
        filters.append(f"atempo={rest:.5f}")
        return ",".join(filters)

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
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
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
    crossfade_s: float = 0.12,
    edge_fade_s: float = 0.10,
    playback_speed: float = 1.0,
) -> Path:
    """segments: list of (t0_ms, t1_ms) in output order.

    If playback_speed != 1, select/cut at source speed first, then speed the
    joined file so final duration ≈ source_duration / playback_speed.
    """
    video = Path(video)
    out_mp4 = Path(out_mp4)
    if work_dir is None:
        work_dir = out_mp4.parent / "_parts"
    work_dir = Path(work_dir)
    if work_dir.exists():
        # clean ALL intermediate files (parts + joined) to avoid stale short joins
        for old in work_dir.glob("*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass
    work_dir.mkdir(parents=True, exist_ok=True)

    tw, th = _probe_stream_size(video)
    parts: list[Path] = []
    for i, (t0, t1) in enumerate(segments):
        if t1 - t0 < 200:
            t1 = t0 + 200
        part = work_dir / f"part_{i:03d}.mp4"
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
        )
        parts.append(part)

    joined = out_mp4
    speed = playback_speed if playback_speed and playback_speed > 0 else 1.0
    if abs(speed - 1.0) > 0.01:
        joined = work_dir / "_joined_1x.mp4"

    # Always use concat demuxer for full duration fidelity.
    # Per-clip edge fades already smooth joins; multi-clip xfade graphs
    # frequently collapse duration when part count is large.
    concat_segments(parts, joined, crossfade_s=0.0)

    if abs(speed - 1.0) > 0.01:
        apply_playback_speed(joined, out_mp4, speed=speed)
    return out_mp4
