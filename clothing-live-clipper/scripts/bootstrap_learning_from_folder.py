"""
Bootstrap Plan-D learning from good example videos (human-like cuts).

For each video:
  extract audio → ASR → clothing filter → treat kept lines as preferred plan
  → record_plan_feedback(before=empty, after=preferred)

This accelerates iteration without waiting for many manual re-cuts.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

ffbin = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin"
if ffbin.exists():
    os.environ["PATH"] = str(ffbin) + os.pathsep + os.environ.get("PATH", "")

# prefer GPU + small for better quality seeds
os.environ.setdefault("CLIPPER_ASR_DEVICE", "cuda")
os.environ.setdefault("CLIPPER_ASR_COMPUTE_TYPE", "float16")
os.environ.setdefault("CLIPPER_ASR_QUALITY", "high")
small = Path(r"C:\Users\MR\AppData\grok\models\whisper-small")
if (small / "model.bin").exists():
    os.environ.setdefault("CLIPPER_LOCAL_WHISPER_MODEL", str(small))
os.environ.setdefault("CLIPPER_ASR_BEAM_SIZE", "3")
os.environ.setdefault("CLIPPER_ASR_BEST_OF", "3")

from agent_clip_video import asr_local, extract_wav  # type: ignore
from filter_transcript_v2 import classify, filter_for_duration  # type: ignore
from clipper.learning import learning_status, record_plan_feedback  # type: ignore

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".mts", ".m2ts"}


def _iter_videos(folder: Path) -> list[Path]:
    vids: list[Path] = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXT:
            vids.append(p)
    return sorted(vids, key=lambda x: x.name)


_FEATURE_HINTS = (
    "面料", "版型", "显瘦", "遮肉", "不透", "柔软", "超软", "软到", "垂感", "弹力",
    "收腰", "修身", "高腰", "梨形", "闭眼入", "天丝", "醋酸", "雪纺", "纯棉",
    "蕾丝", "雷丝", "破洞", "拼接", "凉感", "不起球", "可机洗", "抗皱", "显白",
    "独家", "专利", "限定", "百搭", "通勤", "牛仔", "裙子", "上衣", "外套",
)


def _feature_density(text: str) -> int:
    t = text or ""
    return sum(1 for w in _FEATURE_HINTS if w in t)


def _lines_to_plan(lines: list[dict]) -> dict:
    """
    Put strongest clothing feature lines into golden, rest into trust.
    Empty before-plan means all after lines count as positive 'added'.
    """
    scored: list[tuple[int, dict]] = []
    for u in lines:
        text = str(u.get("text") or "").strip()
        if not text:
            continue
        g = classify(text)
        dens = _feature_density(text)
        # require at least some product signal
        if g == "drop" and dens <= 0:
            continue
        score = dens * 10 + (30 if g == "strong" else 10 if g == "medium" else 0)
        # demote live-ish words even if slipped through
        for bad in ("宝贝", "姐妹", "家人们", "老铁", "直播", "链接", "尺码", "M码", "L码"):
            if bad in text:
                score -= 20
        scored.append((score, u))

    scored.sort(key=lambda x: x[0], reverse=True)
    hooks = [u for sc, u in scored if sc >= 20][:8]
    rest = [u for sc, u in scored if u not in hooks and sc >= 5][:12]

    def slot(u: dict, role: str, i: int) -> dict:
        return {
            "clip_id": f"seed_{role}_{i:03d}",
            "role": role,
            "text": str(u.get("text") or "").strip(),
            "t0_ms": int(u.get("t0_ms") or 0),
            "t1_ms": int(u.get("t1_ms") or 0),
            "score": 50 if role == "hook" else 20,
        }

    golden = [slot(u, "hook", i) for i, u in enumerate(hooks)]
    trust = [slot(u, "trust", i) for i, u in enumerate(rest)]
    return {"golden": golden, "trust": trust, "cta": []}


def process_one(video: Path, work_root: Path) -> dict:
    name = video.stem
    work = work_root / name
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "audio_16k.wav"
    t0 = time.time()
    print(f"\n=== {video.name}")
    if not wav.exists() or wav.stat().st_size < 1000:
        print(" extract wav…")
        extract_wav(video, wav)
    print(" asr…")
    raw = asr_local(wav)
    (work / "transcript_asr.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f" asr_segments={len(raw)}")
    kept = filter_for_duration(raw, target_ms=90_000, min_ms=20_000, max_ms=120_000)
    (work / "transcript_kept.json").write_text(
        json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f" kept={len(kept)}")
    after = _lines_to_plan(kept)
    # empty before => all kept/hook lines become positive seeds
    prefs = record_plan_feedback(
        job_id=f"seed::{video.name}",
        before_plan={"golden": [], "trust": [], "cta": []},
        after_plan=after,
        source="bootstrap_folder",
    )
    dt = time.time() - t0
    print(
        f" learned hooks={len(after['golden'])} trust={len(after['trust'])} "
        f"events={prefs.get('stats', {}).get('events')} elapsed={dt:.1f}s"
    )
    return {
        "video": str(video),
        "raw": len(raw),
        "kept": len(kept),
        "hooks": len(after["golden"]),
        "trust": len(after["trust"]),
        "elapsed": round(dt, 1),
    }


def main() -> int:
    folder = Path(r"C:\Users\MR\Desktop\检查文件\学习文件\新建文件夹")
    if len(sys.argv) > 1:
        folder = Path(sys.argv[1])
    if not folder.exists():
        print("folder not found:", folder)
        return 1
    vids = _iter_videos(folder)
    print("videos", len(vids), "in", folder)
    if not vids:
        return 1
    work_root = ROOT / "output" / "learning_bootstrap"
    work_root.mkdir(parents=True, exist_ok=True)
    results = []
    # process all; GPU should make this acceptable
    for v in vids:
        try:
            results.append(process_one(v, work_root))
        except Exception as e:
            print("FAIL", v.name, e)
            results.append({"video": str(v), "error": str(e)})
    st = learning_status()
    summary = {
        "folder": str(folder),
        "videos": len(vids),
        "results": results,
        "learning": {
            "events": st.get("events"),
            "kept_slots": st.get("kept_slots"),
            "hook_slots": st.get("hook_slots"),
            "top_hook": st.get("top_hook"),
            "updated_at": st.get("updated_at"),
        },
    }
    out = work_root / "bootstrap_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print("events", st.get("events"), "kept", st.get("kept_slots"), "hooks", st.get("hook_slots"))
    print("top_hook", st.get("top_hook")[:15])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
