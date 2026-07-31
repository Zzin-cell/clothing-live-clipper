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
    max_edge: int | None  # None = keep source size; for 1080P export use 1080
    force_height: int | None  # e.g. 1080 for final export
    force_width: int | None
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
    # tiny safety trim from each cut tail to drop trailing silence/hold frames
    tail_trim_ms: int
    crf: int
    x264_preset: str
    nvenc_preset: str
    nvenc_cq: int
    # "recommend" ~ CapCut-style default bitrate for 1080p30 H.264
    video_bitrate: str | None
    max_video_bitrate: str | None
    audio_bitrate: str
    container: str  # mp4
    vcodec_family: str  # h264


def get_render_profile(name: str = "final") -> RenderProfile:
    """
    draft  = fast preview
    final  = 2K-class publish export for ~60s clothing cuts:
      portrait ~1440x2560 when source allows, MP4, 30fps, H.264 + AAC,
      bitrate ~7.5Mbps so ~60s ≈ 50–60MB.
    """
    n = (name or "final").strip().lower()
    if n in {"draft", "preview", "fast"}:
        return RenderProfile(
            name="draft",
            max_edge=720,
            force_height=None,
            force_width=None,
            fps=30,  # keep 30 for timeline consistency with export
            edge_fade_s=0.0,
            video_fade_s=0.0,
            # No mid-cut audio fades (they make every join "hitch").
            # Tiny optional fade is applied only at whole-file ends if ever needed.
            audio_fade_s=0.0,
            smooth_handle_ms=16,
            join_overlap_frames=2,
            tail_trim_ms=60,
            crf=26,
            x264_preset="veryfast",
            nvenc_preset="p2",
            nvenc_cq=26,
            video_bitrate="4M",
            max_video_bitrate="6M",
            audio_bitrate="128k",
            container="mp4",
            vcodec_family="h264",
        )
    # Final export: 2K-class vertical + higher bitrate for fabric detail.
    # 60s * 7.5Mbps ≈ 56MB class.
    return RenderProfile(
        name="final",
        max_edge=1440,  # 2K class (1440p long edge)
        force_height=1440,
        force_width=None,
        fps=30,
        edge_fade_s=0.0,
        video_fade_s=0.0,
        audio_fade_s=0.0,
        smooth_handle_ms=24,
        join_overlap_frames=2,
        tail_trim_ms=80,
        crf=18,
        x264_preset="fast",
        nvenc_preset="p5",
        nvenc_cq=17,
        video_bitrate="7.5M",
        max_video_bitrate="10M",
        audio_bitrate="192k",
        container="mp4",
        vcodec_family="h264",
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
    Export family is H.264 (libx264 / h264_nvenc).
    CLIPPER_RENDER_HW=auto|off|nvenc
    """
    mode = (os.environ.get("CLIPPER_RENDER_HW") or "auto").strip().lower()
    use_bitrate = bool(profile.video_bitrate)

    def x264_args() -> list[str]:
        # Douyin/MP4 friendly: yuv420p + +faststart set by callers; high profile.
        args = [
            "-preset",
            profile.x264_preset,
            "-profile:v",
            "high",
            "-level",
            "4.1",
        ]
        if use_bitrate:
            args += [
                "-b:v",
                str(profile.video_bitrate),
                "-maxrate",
                str(profile.max_video_bitrate or profile.video_bitrate),
                "-bufsize",
                str(profile.max_video_bitrate or profile.video_bitrate),
            ]
            # still allow CRF ceiling for complex scenes
            args += ["-crf", str(profile.crf)]
        else:
            args += ["-crf", str(profile.crf)]
        return args

    def nvenc_args() -> list[str]:
        args = [
            "-preset",
            profile.nvenc_preset,
            "-rc",
            "vbr",
            "-cq",
            str(profile.nvenc_cq),
            "-profile:v",
            "high",
            "-level",
            "4.1",
        ]
        if use_bitrate:
            args += [
                "-b:v",
                str(profile.video_bitrate),
                "-maxrate",
                str(profile.max_video_bitrate or profile.video_bitrate),
                "-bufsize",
                str(profile.max_video_bitrate or profile.video_bitrate),
            ]
        else:
            args += ["-b:v", "0"]
        return args

    if mode in {"off", "0", "false", "cpu", "libx264"}:
        return "libx264", x264_args()
    want_nvenc = mode in {"auto", "nvenc", "gpu", "h264_nvenc", "1", "true", "on"}
    if want_nvenc and _ffmpeg_has_encoder("h264_nvenc"):
        return "h264_nvenc", nvenc_args()
    return "libx264", x264_args()


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
    """
    Compute output width/height for export.

    Final goal: sharper ~2K when source supports it.
      - portrait 9:16 with long edge high enough → 1440x2560
      - portrait 1080x1920 sources keep 1080x1920 (no heavy fake upscale)
      - never invent resolution with large upscale
    """
    sw, sh = max(2, int(src_w)), max(2, int(src_h))
    ratio = sw / float(sh)
    portrait = sh >= sw
    src_long = max(sw, sh)

    # Explicit fixed canvas if both forced and source roughly matches
    if force_w and force_h:
        fw = max(2, int(force_w) - (int(force_w) % 2))
        fh = max(2, int(force_h) - (int(force_h) % 2))
        if portrait and 0.48 <= ratio <= 0.70 and src_long >= 1280:
            return fw, fh
        force_w = force_h = None

    # Desired long edge from profile
    want_long = None
    if force_h and portrait:
        want_long = int(force_h)
    elif force_w and not portrait:
        want_long = int(force_w)
    elif max_edge:
        want_long = int(max_edge)

    # Do not invent detail: cap target by source long edge * 1.08
    if want_long is not None:
        want_long = min(want_long, max(src_long, int(round(src_long * 1.08))))

    # Snap common 9:16 social sizes — prefer 2K-class when source allows
    if portrait and 0.52 <= ratio <= 0.62 and want_long:
        if want_long >= 1440 and src_long >= 2200:
            w, h = 1440, 2560  # 2K-class vertical
        elif src_long >= 1800:
            w, h = 1080, 1920  # solid 1080p vertical
        elif src_long >= 1280:
            w, h = 720, 1280
        else:
            h = want_long
            w = max(2, int(round(sw * (h / float(sh)))))
    else:
        # general scale to long-edge target
        if want_long and src_long > 0:
            if portrait:
                h = want_long
                w = max(2, int(round(sw * (h / float(sh)))))
            else:
                w = want_long
                h = max(2, int(round(sh * (w / float(sw)))))
        else:
            w, h = sw, sh

    # final safety: no >8% upscale
    if w > sw * 1.08 or h > sh * 1.08:
        scale = min(sw * 1.08 / float(w or 1), sh * 1.08 / float(h or 1), 1.0)
        w = max(2, int(round(w * scale)))
        h = max(2, int(round(h * scale)))

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
    # Force CFR early so every part has identical timebase before concat (reduces hitch).
    vf_parts = [
        f"fps={int(fps)}",
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic",
        f"crop={w}:{h}",
        "setsar=1",
    ]
    af_parts: list[str] = ["aresample=44100:async=1:first_pts=0"]
    if speed != 1.0:
        # Critical: setpts alone can leave a longer container timeline padded with black.
        # Always trim BOTH streams to the real output duration after speed.
        vf_parts.append(f"setpts=PTS/{speed:.5f}")
        vf_parts.append(f"trim=duration={out_dur:.5f}")
        vf_parts.append("setpts=PTS-STARTPTS")
        af_parts.append(_atempo_chain(speed))
        af_parts.append(f"atrim=duration={out_dur:.5f}")
        af_parts.append("asetpts=PTS-STARTPTS")
    else:
        vf_parts.append("setpts=PTS-STARTPTS")
        af_parts.append("asetpts=PTS-STARTPTS")
    # Do not fade audio at every cut edge — that makes the whole cut "一卡一卡".
    # Only allow fade if profile explicitly requests a meaningful value (>0).
    if a_fade > 0.001:
        a_fade = min(a_fade, max(0.01, min(0.04, out_dur / 12.0)))
        a_fade_out_st = max(0.0, out_dur - a_fade)
        af_parts.append(f"afade=t=in:st=0:d={a_fade:.3f}")
        af_parts.append(f"afade=t=out:st={a_fade_out_st:.3f}:d={a_fade:.3f}")
    # Video fade intentionally off by default (v_fade==0)
    if v_fade > 0:
        v_out_st = max(0.0, out_dur - v_fade)
        vf_parts.append(f"fade=t=in:st=0:d={v_fade:.3f}")
        vf_parts.append(f"fade=t=out:st={v_out_st:.3f}:d={v_fade:.3f}")

    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{ss:.3f}",
        "-i",
        str(video),
        # input-side: take only source window
        "-t",
        f"{src_dur:.3f}",
        "-vf",
        ",".join(vf_parts),
        "-af",
        ",".join(af_parts),
        "-r",
        str(int(fps)),
        "-vsync",
        "cfr",
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
        "2",
        # output-side hard stop: this is the key fix against black gaps after 1.4x
        # (setpts shortens motion but encoder can still write a longer empty timeline)
        "-t",
        f"{out_dur:.5f}",
        "-shortest",
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts",
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
    profile: RenderProfile | None = None,
) -> Path:
    """
    Seamless join with continuous timestamps (no dissolve / no black pad).
    Uses filter_complex n= concat so A/V stay locked; falls back to demuxer.
    """
    del crossfade_s
    return _concat_filter_complex(segment_paths, Path(out_mp4), profile=profile)


def _concat_filter_complex(
    segment_paths: list[Path],
    out_mp4: Path,
    *,
    profile: RenderProfile | None = None,
) -> Path:
    """
    n-input concat filter → one continuous timeline.

    More reliable than concat demuxer for mixed encoder sessions / NVENC parts
    (avoids micro freezes at every join).
    """
    ffmpeg = require_ffmpeg()
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not segment_paths:
        raise FFmpegError("no segments")
    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], out_mp4)
        return out_mp4

    prof = profile or get_render_profile("draft")
    fps = int(prof.fps or 30)
    threads = str(max(2, min(8, (os.cpu_count() or 4))))
    vcodec, v_extra = pick_video_encoder(profile=prof)

    # ffmpeg argv limits: when many parts, chunk pairwise into trees of 12
    paths = [Path(p) for p in segment_paths]
    if len(paths) > 12:
        with tempfile.TemporaryDirectory(prefix="clipper_concat_chunks_") as td:
            td_path = Path(td)
            chunk_outs: list[Path] = []
            for ci in range(0, len(paths), 12):
                chunk = paths[ci : ci + 12]
                cout = td_path / f"chunk_{ci:03d}.mp4"
                _concat_filter_complex(chunk, cout, profile=prof)
                chunk_outs.append(cout)
            return _concat_filter_complex(chunk_outs, out_mp4, profile=prof)

    cmd: list[str] = [ffmpeg, "-y"]
    for p in paths:
        cmd += ["-i", str(p.resolve())]
    n = len(paths)
    # Reset pts per input then n concat (v=1 a=1) for A/V lock
    chains: list[str] = []
    labels: list[str] = []
    for i in range(n):
        chains.append(
            f"[{i}:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p,setsar=1[v{i}]"
        )
        chains.append(
            f"[{i}:a]asetpts=PTS-STARTPTS,aresample=44100:async=1:first_pts=0,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    fc = ";".join(chains) + ";" + "".join(labels) + f"concat=n={n}:v=1:a=1[vout][aout]"
    cmd += [
        "-filter_complex",
        fc,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-r",
        str(fps),
        "-vsync",
        "cfr",
        "-c:v",
        vcodec,
        *v_extra,
        "-threads",
        threads,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        prof.audio_bitrate or "160k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    try:
        run_cmd(cmd)
    except FFmpegError:
        # Fallback: concat demuxer re-encode (older ffmpeg / filter graph fail)
        return _concat_demuxer_reencode(paths, out_mp4, profile=prof)
    return out_mp4


def _concat_demuxer_reencode(
    segment_paths: list[Path],
    out_mp4: Path,
    *,
    profile: RenderProfile | None = None,
) -> Path:
    ffmpeg = require_ffmpeg()
    prof = profile or get_render_profile("draft")
    fps = int(prof.fps or 30)
    with tempfile.TemporaryDirectory(prefix="clipper_concat_") as td:
        list_file = Path(td) / "list.txt"
        lines = []
        for p in segment_paths:
            ap = Path(p).resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{ap}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        threads = str(max(2, min(8, (os.cpu_count() or 4))))
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-fflags",
            "+genpts",
            "-i",
            str(list_file),
            "-vf",
            f"setpts=PTS-STARTPTS,fps={fps},setsar=1",
            "-af",
            "asetpts=PTS-STARTPTS,aresample=44100:async=1:first_pts=0",
            "-r",
            str(fps),
            "-vsync",
            "cfr",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-threads",
            threads,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            prof.audio_bitrate or "160k",
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
    crop_mode: str = "cover_trim_outt",
    join_overlap_ms: int = 0,
) -> str:
    # include join_overlap so caches invalidate when cut windows expand
    # cover_trim_outt = cover + post-speed filter trim + output -t hard stop
    # bump tag when cut/audio layout changes so old hitchy parts are not reused
    raw = (
        f"{t0}|{t1}|{speed:.5f}|{tw}x{th}|{fps}|{fade:.3f}|"
        f"vf{video_fade:.3f}|af{audio_fade:.3f}|ov{join_overlap_ms}|"
        f"{profile}|{vcodec}|{crop_mode}|smooth_join_v2"
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
    tw, th = _fit_target_size(
        src_w,
        src_h,
        max_edge=prof.max_edge,
        force_w=prof.force_width,
        force_h=prof.force_height,
    )
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

    # prepare segment specs (tiny handles + micro join overlap + tail trim)
    raw_handles: list[tuple[int, int]] = []
    tail_trim = max(0, int(getattr(prof, "tail_trim_ms", 0) or 0))
    for t0, t1 in segments:
        t0i, t1i = int(t0), int(t1)
        if t1i - t0i < 280:
            t1i = t0i + 280
        # Nibble trailing hold/silence that ASR often overshoots by 50–150ms
        if tail_trim > 0 and (t1i - t0i) > tail_trim + 400:
            t1i = t1i - tail_trim
        if handle > 0:
            # Prefer slight pre-roll only; avoid large post-roll (creates blackish holds)
            t0i = max(0, t0i - handle)
            t1i = t1i + max(0, handle // 3)
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
        "crop": "cover_trim_outt",
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
            crop_mode="cover_trim_outt",
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

    # single concat — speed already in parts; never whole-file retime (causes hitch)
    concat_segments(parts, out_mp4, crossfade_s=0.0, profile=prof)
    return out_mp4
