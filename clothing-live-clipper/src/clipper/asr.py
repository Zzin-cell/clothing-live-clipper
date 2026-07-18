from __future__ import annotations

import json
import re
from pathlib import Path

from clipper.models import TranscriptUtterance

SRT_BLOCK = re.compile(
    r"(\d+)\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+([\s\S]*?)(?=\n\d+\s+\d{2}:|\Z)",
    re.MULTILINE,
)


def _ts_to_ms(ts: str) -> int:
    ts = ts.replace(",", ".")
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(".")
    return (
        int(hh) * 3_600_000
        + int(mm) * 60_000
        + int(ss) * 1000
        + int(ms.ljust(3, "0")[:3])
    )


def load_transcript_json(path: str | Path) -> list[TranscriptUtterance]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "transcript" in data:
        data = data["transcript"]
    out: list[TranscriptUtterance] = []
    for i, item in enumerate(data):
        if "t0_ms" in item:
            t0, t1 = int(item["t0_ms"]), int(item["t1_ms"])
        elif "t0" in item:
            # allow seconds
            t0, t1 = int(float(item["t0"]) * 1000), int(float(item["t1"]) * 1000)
        elif "start" in item:
            t0, t1 = int(float(item["start"]) * 1000), int(float(item["end"]) * 1000)
        else:
            raise ValueError(f"transcript item missing time fields: {item}")
        text = item.get("text") or item.get("content") or ""
        out.append(
            TranscriptUtterance(
                utt_id=str(item.get("utt_id") or item.get("id") or f"u{i:04d}"),
                text=str(text).strip(),
                t0_ms=t0,
                t1_ms=t1,
                confidence=item.get("confidence"),
            )
        )
    out.sort(key=lambda u: u.t0_ms)
    return out


def load_transcript_srt(path: str | Path) -> list[TranscriptUtterance]:
    raw = Path(path).read_text(encoding="utf-8-sig")
    out: list[TranscriptUtterance] = []
    # simpler line-based parse
    blocks = re.split(r"\n\s*\n", raw.strip())
    for i, block in enumerate(blocks):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # find time line
        time_line = None
        text_lines: list[str] = []
        for ln in lines:
            if "-->" in ln:
                time_line = ln
            elif time_line is not None:
                text_lines.append(ln)
            elif not ln.isdigit() and time_line is None and "-->" not in ln:
                continue
        if not time_line or not text_lines:
            continue
        left, right = [x.strip() for x in time_line.split("-->")]
        out.append(
            TranscriptUtterance(
                utt_id=f"s{i:04d}",
                text=" ".join(text_lines),
                t0_ms=_ts_to_ms(left),
                t1_ms=_ts_to_ms(right),
            )
        )
    return out


def load_transcript(path: str | Path) -> list[TranscriptUtterance]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    suf = path.suffix.lower()
    if suf == ".json":
        return load_transcript_json(path)
    if suf == ".srt":
        return load_transcript_srt(path)
    raise ValueError(f"unsupported transcript format: {suf} (use .json or .srt)")
