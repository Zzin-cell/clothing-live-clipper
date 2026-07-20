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
        ff = which_ffmpeg()
        if ff:
            cand = Path(ff).with_name("ffprobe.exe")
            if cand.exists():
                ffprobe = str(cand)
    if not ffprobe:
        # last resort: parse ffmpeg -i
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
    edge_fade_s: float = 0.12,
    target_w: int | None = None,
    target_h: int | None = None,
    fps: int = 30,
    zoom_style: str = "soft",
) -> Path:
    """
    Cut one segment CapCut-like:
    - normalize size/fps
    - soft in/out fades on A/V
    - subtle zoom (slow push-in) for less static feel
    """
    ffmpeg = require_ffmpeg()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ss = max(0.0, t0_ms / 1000.0)
    dur = max(0.20, (t1_ms - t0_ms) / 1000.0)

    fade = min(max(0.06, edge_fade_s), max(0.05, dur / 3.5))
    fade_out_st = max(0.0, dur - fade)

    if not reencode:
        cmd = [
            ffmpeg, "-y",
            "-ss", f"{ss:.3f}",
            "-i", str(video),
            "-t", f"{dur:.3f}",
            "-c", "copy",
            str(out_path),
        ]
        run_cmd(cmd)
        return out_path

    w = target_w or 1080
    h = target_h or 1920
    # even dimensions
    w -= w % 2
    h -= h % 2

    # CapCut-like gentle zoom: 1.00 → ~1.04 over clip
    # zoompan is heavy; use scale+crop with animated crop via zoompan on normalized frames
    if zoom_style == "soft" and dur >= 0.45:
        # frames approx
        frames = max(2, int(round(dur * fps)))
        # z goes 1.0 to 1.035
        zexpr = f"min(1.035,1+0.035*on/{max(1, frames - 1)})"
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"fps={fps},"
            f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
            f"fade=t=in:st=0:d={fade:.3f}:alpha=0,"
            f"fade=t=out:st={fade_out_st:.3f}:d={fade:.3f}:alpha=0"
        )
    else:
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
        ffmpeg, "-y",
        "-ss", f"{ss:.3f}",
        "-i", str(video),
        "-t", f"{dur:.3f}",
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        run_cmd(cmd)
    except FFmpegError:
        # fallback without zoompan if filter fails
        vf2 = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},"
            f"fade=t=in:st=0:d={fade:.3f},"
            f"fade=t=out:st={fade_out_st:.3f}:d={fade:.3f}"
        )
        cmd2 = [
            ffmpeg, "-y",
            "-ss", f"{ss:.3f}",
            "-i", str(video),
            "-t", f"{dur:.3f}",
            "-vf", vf2,
            "-af", af,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            str(out_path),
        ]
        run_cmd(cmd2)
    return out_path


def concat_segments(
    segment_paths: list[Path],
    out_mp4: str | Path,
    *,
    crossfade_s: float = 0.18,
) -> Path:
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not segment_paths:
        raise FFmpegError("no segments to concat")
    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], out_mp4)
        return out_mp4

    durs: list[float] = []
    for p in segment_paths:
        try:
            durs.append(max(0.05, probe_duration_ms(p) / 1000.0))
        except Exception:
            durs.append(1.0)

    cf = max(0.08, min(float(crossfade_s or 0.0), 0.28))
    # pairwise xfade when every part is long enough (CapCut-like soft cut)
    if cf > 0.01 and all(d > cf * 2.5 for d in durs):
        try:
            return _concat_pairwise_xfade(segment_paths, durs, out_mp4, cf)
        except FFmpegError:
            pass

    return _concat_pairwise_hard(segment_paths, out_mp4)


def _concat_pairwise_hard(segment_paths: list[Path], out_mp4: Path) -> Path:
    ffmpeg = require_ffmpeg()

    def _pair(a: Path, b: Path, dest: Path) -> None:
        cmd = [
            ffmpeg, "-y",
            "-i", str(a),
            "-i", str(b),
            "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            str(dest),
        ]
        run_cmd(cmd)

    with tempfile.TemporaryDirectory(prefix="clipper_pair_") as td:
        td_path = Path(td)
        cur = segment_paths[0]
        for i, nxt in enumerate(segment_paths[1:], start=1):
            dest = td_path / f"join_{i:03d}.mp4"
            _pair(cur, nxt, dest)
            cur = dest
        shutil.copy2(cur, out_mp4)
    return out_mp4


def _concat_pairwise_xfade(
    segment_paths: list[Path],
    durs: list[float],
    out_mp4: Path,
    crossfade_s: float,
) -> Path:
    """
    CapCut-like soft transitions via pairwise xfade+acrossfade.
    Pairwise keeps duration correct (unlike huge multi-input graphs).
    """
    ffmpeg = require_ffmpeg()
    # transition variants for visual variety (still soft)
    transitions = ["fade", "fadewhite", "smoothleft", "smoothright", "fadeblack"]

    def _pair(a: Path, b: Path, da: float, dest: Path, idx: int) -> float:
        tr = transitions[idx % len(transitions)]
        # offset must be da - crossfade
        offset = max(0.05, da - crossfade_s)
        # prefer fade for very short a
        if da < crossfade_s * 3:
            tr = "fade"
            offset = max(0.05, da - crossfade_s)
        cmd = [
            ffmpeg, "-y",
            "-i", str(a),
            "-i", str(b),
            "-filter_complex",
            (
                f"[0:v][1:v]xfade=transition={tr}:duration={crossfade_s:.3f}:offset={offset:.3f}[v];"
                f"[0:a][1:a]acrossfade=d={crossfade_s:.3f}:c1=tri:c2=tri[a]"
            ),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            str(dest),
        ]
        run_cmd(cmd)
        # new duration ≈ da + db - crossfade
        try:
            return probe_duration_ms(dest) / 1000.0
        except Exception:
            return da + 1.0 - crossfade_s

    with tempfile.TemporaryDirectory(prefix="clipper_xfade_") as td:
        td_path = Path(td)
        cur = segment_paths[0]
        cur_d = durs[0]
        for i, nxt in enumerate(segment_paths[1:], start=1):
            dest = td_path / f"xf_{i:03d}.mp4"
            cur_d = _pair(cur, nxt, cur_d, dest, i - 1)
            cur = dest
        shutil.copy2(cur, out_mp4)
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

    # slight sharpen after speed for less mushy CapCut look
    vf = f"setpts=PTS/{speed:.5f},unsharp=3:3:0.4:3:3:0.0"
    af = atempo_chain(speed)
    cmd = [
        ffmpeg, "-y",
        "-i", str(src_mp4),
        "-filter:v", vf,
        "-filter:a", af,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
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
    crossfade_s: float = 0.20,
    edge_fade_s: float = 0.14,
    playback_speed: float = 1.0,
) -> Path:
    """CapCut-like render: soft edges, pairwise crossfades, optional speed."""
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
    parts: list[Path] = []
    for i, (t0, t1) in enumerate(segments):
        if t1 - t0 < 280:
            t1 = t0 + 280
        # slight handles for smoother transitions (CapCut often cuts with handles)
        if smooth:
            t0 = max(0, t0 - 40)
            t1 = t1 + 40
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
            zoom_style="soft" if smooth else "none",
        )
        parts.append(part)

    joined = out_mp4
    speed = playback_speed if playback_speed and playback_speed > 0 else 1.0
    if abs(speed - 1.0) > 0.01:
        joined = work_dir / "_joined_1x.mp4"

    # CapCut-like crossfade length; clamp for many clips.
    # Compensate selection length so final after speed still ≈ target.
    cf = crossfade_s if smooth else 0.0
    if len(parts) > 16:
        cf = min(cf, 0.14)
    if len(parts) > 24:
        cf = min(cf, 0.10)

    concat_segments(parts, joined, crossfade_s=cf)

    if abs(speed - 1.0) > 0.01:
        apply_playback_speed(joined, out_mp4, speed=speed)
    elif joined != out_mp4:
        shutil.copy2(joined, out_mp4)

    # If final is still short vs expected (xfade eats duration), slightly slow
    # back toward ~60s instead of leaving a hard short cut.
    try:
        final_ms = probe_duration_ms(out_mp4)
        # expected ~ source/speed; if much shorter than 55s, retime gently
        if final_ms > 0 and final_ms < 56_500:
            target_ms = 58_500
            factor = min(1.15, max(1.01, target_ms / final_ms))
            # slightly slow down to approach ~58–60s after soft transitions
            tmp = out_mp4.with_suffix(".retime.mp4")
            apply_playback_speed(out_mp4, tmp, speed=1.0 / factor)
            tmp.replace(out_mp4)
    except Exception:
        pass
    return out_mp4
