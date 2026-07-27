from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderProfile:
    name: str
    max_edge: int | None  # None = keep source size
    fps: int
    # Video must NOT fade to black between cuts (looks like black flashes).
    # Keep only a tiny audio ease to avoid pops; video is hard-cut.
    edge_fade_s: float  # legacy alias → audio fade only
    video_fade_s: float
    audio_fade_s: float
    smooth_handle_ms: int
    # Adjacent cuts expand into each other by N *output* frames so the join
    # briefly shares continuous motion (micro-stutter) instead of a hard pop.
    join_overlap_frames: int
    crf: int
    x264_preset: str
    nvenc_preset: str
    nvenc_cq: int
    audio_bitrate: str


def get_render_profile(name: str = "final") -> RenderProfile:
    n = (name or "final").strip().lower()
    if n in {"draft", "preview", "fast"}:
        return RenderProfile(
            name="draft",
            max_edge=720,
            fps=25,
            edge_fade_s=0.0,
            video_fade_s=0.0,
            audio_fade_s=0.03,
            smooth_handle_ms=40,
            join_overlap_frames=2,
            crf=28,
            x264_preset="ultrafast",
            nvenc_preset="p1",
            nvenc_cq=28,
            audio_bitrate="96k",
        )
    return RenderProfile(
        name="final",
        max_edge=None,
        fps=30,
        edge_fade_s=0.0,
        video_fade_s=0.0,
        audio_fade_s=0.04,
        smooth_handle_ms=80,
        join_overlap_frames=2,
        crf=23,
        x264_preset="ultrafast",
        nvenc_preset="p4",
        nvenc_cq=23,
        audio_bitrate="128k",
    )


def join_overlap_source_ms(
    *,
    frames: int,
    fps: int,
    playback_speed: float = 1.0,
) -> int:
    """How many source ms to expand a cut so ~N output frames of content are shared."""
    n = max(0, int(frames))
    if n <= 0:
        return 0
    fp = max(1, int(fps or 30))
    sp = float(playback_speed) if playback_speed and playback_speed > 0 else 1.0
    # After setpts=PTS/sp, 1 output frame ≈ (1000/fps)*sp source ms
    return max(1, int(round(n * (1000.0 / fp) * sp)))


def apply_join_overlaps(
    segments: list[tuple[int, int]],
    *,
    overlap_ms: int,
) -> list[tuple[int, int]]:
    """
    Expand each segment into its neighbors by overlap_ms so adjacent cuts share
    1–2 frames of continuous source (no dissolve / no black).
    """
    if not segments or overlap_ms <= 0:
        return [(int(a), int(b)) for a, b in segments]
    n = len(segments)
    out: list[tuple[int, int]] = []
    for i, (t0, t1) in enumerate(segments):
        a, b = int(t0), int(t1)
        if i > 0:
            a = max(0, a - overlap_ms)
        if i < n - 1:
            b = b + overlap_ms
        if b <= a:
            b = a + 280
        out.append((a, b))
    return out


_ENCODER_CACHE: dict[str, bool] = {}


def _ffmpeg_has_encoder(name: str) -> bool:
    key = name.lower()
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]
    try:
        ffmpeg = require_ffmpeg()
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        ok = key in (proc.stdout or "").lower()
    except Exception:
        ok = False
    _ENCODER_CACHE[key] = ok
    return ok


def pick_video_encoder(*, profile: RenderProfile) -> tuple[str, list[str]]:
    """
    Return (codec_name, extra_args_before_pix_fmt).
    CLIPPER_RENDER_HW=auto|off|nvenc
    """
    mode = (os.environ.get("CLIPPER_RENDER_HW") or "auto").strip().lower()
    if mode in {"off", "0", "false", "cpu", "libx264"}:
        return "libx264", ["-preset", profile.x264_preset, "-crf", str(profile.crf)]
    want_nvenc = mode in {"auto", "nvenc", "gpu", "h264_nvenc", "1", "true", "on"}
    if want_nvenc and _ffmpeg_has_encoder("h264_nvenc"):
        return (
            "h264_nvenc",
            [
                "-preset",
                profile.nvenc_preset,
                "-rc",
                "vbr",
                "-cq",
                str(profile.nvenc_cq),
                "-b:v",
                "0",
            ],
        )
    return "libx264", ["-preset", profile.x264_preset, "-crf", str(profile.crf)]


def _atempo_chain(speed: float) -> str:
    filters: list[str] = []
    rest = float(speed)
    while rest > 2.0 + 1e-6:
        filters.append("atempo=2.0")
        rest /= 2.0
    while rest < 0.5 - 1e-6:
        filters.append("atempo=0.5")
        rest /= 0.5
    filters.append(f"atempo={rest:.5f}")
    return ",".join(filters)


def _fit_target_size(
    src_w: int,
    src_h: int,
    *,
    max_edge: int | None,
    force_w: int | None = None,
    force_h: int | None = None,
) -> tuple[int, int]:
    if force_w and force_h:
        w, h = force_w, force_h
    else:
        w, h = src_w, src_h
    if max_edge and max(w, h) > max_edge:
        if h >= w:
            h = max_edge
            w = max(2, int(round(src_w * (max_edge / float(src_h)))))
        else:
            w = max_edge
            h = max(2, int(round(src_h * (max_edge / float(src_w)))))
    w = max(2, w - (w % 2))
    h = max(2, h - (h % 2))
    return w, h


def build_cut_cmd(
    *,
    ffmpeg: str,
    video: Path,
    t0_ms: int,
    t1_ms: int,
    out_path: Path,
    target_w: int,
    target_h: int,
    fps: int,
    edge_fade_s: float = 0.0,
    video_fade_s: float | None = None,
    audio_fade_s: float | None = None,
    playback_speed: float,
    vcodec: str,
    v_extra: list[str],
    threads: int,
    audio_bitrate: str = "96k",
) -> list[str]:
    """Build ffmpeg argv for one cut. Speed is applied in the same pass when != 1.

    Policy: hard video cuts between segments (no black flashes). Optional tiny
    audio fades only, to avoid pop clicks at joins.
    """
    ss = max(0.0, t0_ms / 1000.0)
    # Source duration before speed
    src_dur = max(0.12, (t1_ms - t0_ms) / 1000.0)
    speed = float(playback_speed) if playback_speed and playback_speed > 0 else 1.0
    if abs(speed - 1.0) < 0.01:
        speed = 1.0

    out_dur = src_dur / speed if speed != 1.0 else src_dur
    # Default: no video fade. Legacy edge_fade_s used to darken to black — broken for cuts.
    v_fade = 0.0 if video_fade_s is None else max(0.0, float(video_fade_s))
    if audio_fade_s is None:
        # interpret legacy edge_fade_s as audio-only when positive but small
        a_fade = max(0.0, float(edge_fade_s or 0.0))
        if a_fade > 0.06:
            # old profiles used 0.10 for video black fade — clamp to short audio ease
            a_fade = 0.04
    else:
        a_fade = max(0.0, float(audio_fade_s))
    if a_fade > 0:
        a_fade = min(a_fade, max(0.02, min(0.08, out_dur / 8.0)))
    a_fade_out_st = max(0.0, out_dur - a_fade) if a_fade > 0 else 0.0

    w = target_w - (target_w % 2)
    h = target_h - (target_h % 2)

    # Cover-crop instead of black pad when aspect differs (avoids black bars + "black seam" look)
    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}",
        f"fps={int(fps)}",
    ]
    af_parts: list[str] = []
    if speed != 1.0:
        vf_parts.append(f"setpts=PTS/{speed:.5f}")
        af_parts.append(_atempo_chain(speed))
    # Video fade intentionally off by default (v_fade==0)
    if v_fade > 0:
        v_out_st = max(0.0, out_dur - v_fade)
        vf_parts.append(f"fade=t=in:st=0:d={v_fade:.3f}")
        vf_parts.append(f"fade=t=out:st={v_out_st:.3f}:d={v_fade:.3f}")
    if a_fade > 0:
        af_parts.append(f"afade=t=in:st=0:d={a_fade:.3f}")
        af_parts.append(f"afade=t=out:st={a_fade_out_st:.3f}:d={a_fade:.3f}")
    af_parts.append("aresample=async=1:first_pts=0")

    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{ss:.3f}",
        "-i",
        str(video),
        "-t",
        f"{src_dur:.3f}",
        "-vf",
        ",".join(vf_parts),
        "-af",
        ",".join(af_parts),
        "-c:v",
        vcodec,
        *v_extra,
        "-threads",
        str(max(1, int(threads))),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ar",
        "44100",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return cmd


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
    playback_speed: float = 1.0,
    profile: str | RenderProfile | None = None,
    vcodec: str | None = None,
    v_extra: list[str] | None = None,
) -> Path:
    """
    Invisible-edit style cut:
    - unify size/fps/audio so joins don't glitch
    - optional micro fades
    - optional in-cut playback_speed (single-pass, no second full-file retime)
    """
    del zoom_style, reencode  # reserved
    prof = profile if isinstance(profile, RenderProfile) else get_render_profile(profile or "final")
    ffmpeg = require_ffmpeg()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w = (target_w or 1080) - ((target_w or 1080) % 2)
    h = (target_h or 1920) - ((target_h or 1920) % 2)
    if vcodec is None or v_extra is None:
        enc, extra = pick_video_encoder(profile=prof)
        vcodec = vcodec or enc
        v_extra = list(v_extra or extra)

    cmd = build_cut_cmd(
        ffmpeg=ffmpeg,
        video=Path(video),
        t0_ms=int(t0_ms),
        t1_ms=int(t1_ms),
        out_path=out_path,
        target_w=w,
        target_h=h,
        fps=int(fps or prof.fps),
        edge_fade_s=float(edge_fade_s if edge_fade_s is not None else prof.edge_fade_s),
        video_fade_s=float(prof.video_fade_s),
        audio_fade_s=float(prof.audio_fade_s),
        playback_speed=float(playback_speed or 1.0),
        vcodec=str(vcodec),
        v_extra=list(v_extra),
        threads=max(1, min(4, (os.cpu_count() or 4))),
        audio_bitrate=prof.audio_bitrate,
    )
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
    """Legacy whole-file retime (kept for rare pad/retime). Prefer in-cut speed."""
    ffmpeg = require_ffmpeg()
    src_mp4 = Path(src_mp4)
    out_mp4 = Path(out_mp4)
    if speed <= 0:
        raise FFmpegError(f"invalid speed: {speed}")
    if abs(speed - 1.0) < 0.01:
        if src_mp4.resolve() != out_mp4.resolve():
            shutil.copy2(src_mp4, out_mp4)
        return out_mp4

    vf = f"setpts=PTS/{speed:.5f}"
    af = _atempo_chain(speed)
    prof = get_render_profile("final")
    vcodec, v_extra = pick_video_encoder(profile=prof)
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
        vcodec,
        *v_extra,
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


def _part_fingerprint(
    *,
    t0: int,
    t1: int,
    speed: float,
    tw: int,
    th: int,
    fps: int,
    fade: float,
    profile: str,
    vcodec: str,
    video_fade: float = 0.0,
    audio_fade: float = 0.0,
    crop_mode: str = "cover",
    join_overlap_ms: int = 0,
) -> str:
    # include join_overlap so caches invalidate when cut windows expand
    raw = (
        f"{t0}|{t1}|{speed:.5f}|{tw}x{th}|{fps}|{fade:.3f}|"
        f"vf{video_fade:.3f}|af{audio_fade:.3f}|ov{join_overlap_ms}|"
        f"{profile}|{vcodec}|{crop_mode}"
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def render_plan(
    video: str | Path,
    segments: list[tuple[int, int]],
    out_mp4: str | Path,
    work_dir: str | Path | None = None,
    *,
    smooth: bool = True,
    crossfade_s: float = 0.0,
    edge_fade_s: float | None = None,
    playback_speed: float = 1.0,
    profile: str | RenderProfile = "final",
    reuse_parts: bool = True,
) -> Path:
    """
    Render timeline segments to one mp4.

    P0: playback_speed applied inside each cut (no second full-file speed encode)
    P1: profile draft|final (resolution/fade/crf)
    P2: optional h264_nvenc when available
    P3: reuse unchanged part_*.mp4 via fingerprint cache
    """
    del crossfade_s  # no long dissolve
    prof = profile if isinstance(profile, RenderProfile) else get_render_profile(str(profile))
    video = Path(video)
    out_mp4 = Path(out_mp4)
    if work_dir is None:
        work_dir = out_mp4.parent / f"_parts_{prof.name}"
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    src_w, src_h = _probe_stream_size(video)
    tw, th = _fit_target_size(src_w, src_h, max_edge=prof.max_edge)
    speed = float(playback_speed) if playback_speed and playback_speed > 0 else 1.0
    if abs(speed - 1.0) < 0.01:
        speed = 1.0
    # edge_fade_s legacy arg: audio-only; never applies video black fade
    a_fade = float(prof.audio_fade_s if edge_fade_s is None else edge_fade_s)
    if a_fade > 0.06:
        a_fade = 0.04
    if not smooth:
        a_fade = min(a_fade, 0.03)
    v_fade = float(prof.video_fade_s)  # expected 0
    handle = int(prof.smooth_handle_ms if smooth else 0)
    # 1–2 frame micro-overlap between adjacent cuts (source ms, speed-aware)
    overlap_ms = join_overlap_source_ms(
        frames=int(getattr(prof, "join_overlap_frames", 0) or 0),
        fps=int(prof.fps),
        playback_speed=speed,
    )

    vcodec, v_extra = pick_video_encoder(profile=prof)
    cache_path = work_dir / "parts_cache.json"
    old_cache: dict[str, Any] = {}
    if reuse_parts and cache_path.exists():
        try:
            old_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(old_cache, dict):
                old_cache = {}
        except Exception:
            old_cache = {}

    # prepare segment specs (handle pad + 1–2 frame join overlap)
    raw_handles: list[tuple[int, int]] = []
    for t0, t1 in segments:
        t0i, t1i = int(t0), int(t1)
        if t1i - t0i < 280:
            t1i = t0i + 280
        if handle > 0:
            t0i = max(0, t0i - handle)
            t1i = t1i + int(handle * 1.15)
        raw_handles.append((t0i, t1i))
    segs_out = apply_join_overlaps(raw_handles, overlap_ms=overlap_ms)

    specs: list[tuple[int, int, int, Path, str]] = []
    new_cache: dict[str, Any] = {
        "profile": prof.name,
        "speed": speed,
        "size": f"{tw}x{th}",
        "fps": prof.fps,
        "video_fade": v_fade,
        "audio_fade": a_fade,
        "join_overlap_ms": overlap_ms,
        "crop": "cover",
        "vcodec": vcodec,
        "parts": {},
    }
    keep_files: set[str] = set()
    for i, (t0i, t1i) in enumerate(segs_out):
        fp = _part_fingerprint(
            t0=t0i,
            t1=t1i,
            speed=speed,
            tw=tw,
            th=th,
            fps=prof.fps,
            fade=a_fade,
            profile=prof.name,
            vcodec=vcodec,
            video_fade=v_fade,
            audio_fade=a_fade,
            crop_mode="cover",
            join_overlap_ms=overlap_ms,
        )
        # Name by content fingerprint only so unchanged windows reuse across reorders.
        part = work_dir / f"part_{fp}.mp4"
        keep_files.add(part.name)
        specs.append((i, t0i, t1i, part, fp))
        new_cache["parts"][str(i)] = {"t0": t0i, "t1": t1i, "fp": fp, "file": part.name}

    def _needs_cut(part: Path, fp: str) -> bool:
        """Reuse when the fingerprint-named file already exists (content key)."""
        if not reuse_parts:
            return True
        try:
            if part.exists() and part.stat().st_size >= 1:
                return False
        except OSError:
            pass
        return True

    to_cut = [sp for sp in specs if _needs_cut(sp[3], sp[4])]

    def _cut_one(spec: tuple[int, int, int, Path, str]) -> tuple[int, Path]:
        i, t0, t1, part, _fp = spec
        cut_segment(
            video,
            t0,
            t1,
            part,
            reencode=True,
            edge_fade_s=a_fade,
            target_w=tw,
            target_h=th,
            fps=prof.fps,
            zoom_style="none",
            playback_speed=speed,
            profile=prof,
            vcodec=vcodec,
            v_extra=v_extra,
        )
        return i, part

    workers = max(2, min(8, (os.cpu_count() or 4)))
    if vcodec == "h264_nvenc":
        # avoid flooding NVENC sessions
        workers = max(1, min(2, workers))
    parts_map: dict[int, Path] = {
        i: p for i, _a, _b, p, _f in specs if p.exists() and p.stat().st_size >= 1
    }
    if to_cut:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_cut_one, sp) for sp in to_cut]
            for fut in as_completed(futs):
                i, part = fut.result()
                parts_map[i] = part
    for i, _a, _b, part, _f in specs:
        if i not in parts_map:
            # forced cut if cache miss slipped through
            _, part2 = _cut_one((i, _a, _b, part, _f))
            parts_map[i] = part2

    parts = [parts_map[i] for i in range(len(specs))]
    # cleanup obsolete part files (keep cache json)
    if reuse_parts:
        for old in work_dir.glob("part_*.mp4"):
            if old.name not in keep_files:
                try:
                    old.unlink()
                except OSError:
                    pass
    try:
        cache_path.write_text(json.dumps(new_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # single concat — speed already in parts
    concat_segments(parts, out_mp4, crossfade_s=0.0)

    # Optional short pad only if material shortage (rare)
    try:
        final_ms = probe_duration_ms(out_mp4)
        if final_ms > 0 and final_ms < 56_500 and prof.name == "final":
            target_ms = 58_500
            factor = min(1.12, max(1.01, target_ms / final_ms))
            tmp = out_mp4.with_suffix(".retime.mp4")
            apply_playback_speed(out_mp4, tmp, speed=1.0 / factor)
            tmp.replace(out_mp4)
    except Exception:
        pass
    return out_mp4
