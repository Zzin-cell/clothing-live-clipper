"""OpenAI-compatible Whisper ASR: video → timestamped transcript JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from clipper.media import FFmpegError, require_ffmpeg, run_cmd
from clipper.models import TranscriptUtterance


class ASRError(RuntimeError):
    pass


def extract_audio_wav(video: str | Path, wav_out: str | Path) -> Path:
    """Extract mono 16k PCM wav for transcription."""
    ffmpeg = require_ffmpeg()
    video = Path(video)
    wav_out = Path(wav_out)
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_out),
    ]
    try:
        run_cmd(cmd)
    except FFmpegError as e:
        raise ASRError(f"抽音频失败: {e}") from e
    if not wav_out.exists() or wav_out.stat().st_size < 100:
        raise ASRError("抽音频结果为空，请检查视频是否含音轨")
    return wav_out


def _api_config() -> tuple[str, str, str]:
    from clipper.config import resolve_api_key, resolve_asr_base_url, resolve_asr_model

    key = resolve_api_key()
    base = resolve_asr_base_url()
    model = resolve_asr_model()
    if not key:
        raise ASRError(
            "未配置 ASR API Key。请在设置中填写，或 .env 设置 CLIPPER_ASR_API_KEY / OPENAI_API_KEY"
        )
    return key, base, model


def _verbose_to_utterances(payload: dict[str, Any]) -> list[TranscriptUtterance]:
    segs = payload.get("segments") or []
    out: list[TranscriptUtterance] = []
    if segs:
        for i, seg in enumerate(segs):
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            t0 = float(seg.get("start") or 0.0)
            t1 = float(seg.get("end") or t0)
            out.append(
                TranscriptUtterance(
                    utt_id=f"w{i:04d}",
                    text=text,
                    t0_ms=max(0, int(round(t0 * 1000))),
                    t1_ms=max(0, int(round(t1 * 1000))),
                    confidence=None,
                )
            )
    else:
        text = str(payload.get("text") or "").strip()
        if text:
            out.append(
                TranscriptUtterance(
                    utt_id="w0000",
                    text=text,
                    t0_ms=0,
                    t1_ms=max(1000, int(round(float(payload.get("duration") or 1) * 1000))),
                )
            )
    out.sort(key=lambda u: u.t0_ms)
    if not out:
        raise ASRError("ASR 未返回任何口播文本")
    return out


def transcribe_wav(
    wav_path: str | Path,
    *,
    language: str = "zh",
    timeout_s: float = 600.0,
) -> list[TranscriptUtterance]:
    key, base, model = _api_config()
    url = f"{base}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {key}"}
    data = {
        "model": model,
        "response_format": "verbose_json",
        "language": language,
    }
    # some proxies prefer timestamp_granularities
    files = {
        "file": (Path(wav_path).name, Path(wav_path).read_bytes(), "audio/wav"),
    }
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, headers=headers, data=data, files=files)
    except httpx.HTTPError as e:
        raise ASRError(f"ASR 请求失败: {e}") from e
    if resp.status_code >= 400:
        raise ASRError(f"ASR HTTP {resp.status_code}: {resp.text[:800]}")
    try:
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        raise ASRError(f"ASR 返回非 JSON: {resp.text[:400]}") from e
    if not isinstance(payload, dict):
        raise ASRError("ASR 返回格式异常")
    return _verbose_to_utterances(payload)


def save_transcript_json(utts: list[TranscriptUtterance], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "utt_id": u.utt_id,
            "text": u.text,
            "t0_ms": u.t0_ms,
            "t1_ms": u.t1_ms,
            **({"confidence": u.confidence} if u.confidence is not None else {}),
        }
        for u in utts
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def transcribe_video_to_json(
    video: str | Path,
    transcript_json: str | Path,
    *,
    work_dir: str | Path | None = None,
    language: str = "zh",
) -> Path:
    """Full path: video → wav → Whisper API → transcript.json (ms timestamps)."""
    video = Path(video)
    transcript_json = Path(transcript_json)
    wd = Path(work_dir) if work_dir else transcript_json.parent
    wd.mkdir(parents=True, exist_ok=True)
    wav = wd / "audio_16k.wav"
    extract_audio_wav(video, wav)
    utts = transcribe_wav(wav, language=language)
    return save_transcript_json(utts, transcript_json)
