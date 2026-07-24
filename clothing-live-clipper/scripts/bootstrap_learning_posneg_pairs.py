"""
Learn from paired folders:
  each subfolder contains:
    - shorter/smaller finished cut  -> POSITIVE (what to keep / hook)
    - longer/larger source or bad cut -> NEGATIVE (what to drop)

Pipeline per pair:
  ASR both videos
  filter clothing lines
  record_plan_feedback(before=neg_plan-ish, after=pos_plan)
  so positives get keep/hook boost, negatives get drop penalty.
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

os.environ.setdefault("CLIPPER_ASR_DEVICE", "cuda")
os.environ.setdefault("CLIPPER_ASR_COMPUTE_TYPE", "float16")
os.environ.setdefault("CLIPPER_ASR_QUALITY", "high")
small = Path(r"C:\Users\MR\AppData\grok\models\whisper-small")
if (small / "model.bin").exists():
    os.environ.setdefault("CLIPPER_LOCAL_WHISPER_MODEL", str(small))
os.environ.setdefault("CLIPPER_ASR_BEAM_SIZE", "3")
os.environ.setdefault("CLIPPER_ASR_BEST_OF", "3")

from agent_clip_video import asr_local, extract_wav  # type: ignore
from filter_transcript_v2 import classify  # type: ignore
from clipper.learning import learning_status, record_plan_feedback, seed_negative_live_phrases  # type: ignore

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".mts", ".m2ts"}

_FEATURE_HINTS = (
    "面料", "版型", "显瘦", "遮肉", "不透", "柔软", "超软", "软到", "垂感", "弹力",
    "收腰", "修身", "高腰", "梨形", "闭眼入", "天丝", "醋酸", "雪纺", "纯棉",
    "蕾丝", "雷丝", "破洞", "拼接", "凉感", "不起球", "可机洗", "抗皱", "显白",
    "独家", "专利", "限定", "百搭", "通勤", "牛仔", "裙子", "上衣", "外套", "无袖",
)

_LIVE_BAD = (
    "家人们", "老铁", "宝宝们", "姐妹们", "扣1", "扣一", "点关注", "双击", "直播间",
    "公屏", "弹幕", "福袋", "上链接", "小黄车", "欢迎", "过一下", "过一遍",
    "听得到", "在不在", "来了吗", "尺码", "建议穿", "M码", "L码", "券后", "只要",
    "包邮", "加购", "下单", "块钱", "199", "秒杀", "拍下",
)


def _score_line(text: str) -> int:
    t = text or ""
    dens = sum(1 for w in _FEATURE_HINTS if w in t)
    g = classify(t)
    score = dens * 10 + (30 if g == "strong" else 10 if g == "medium" else 0)
    for bad in _LIVE_BAD:
        if bad in t:
            score -= 25
    return score


def _to_slots(lines: list[dict], role: str, limit: int = 12) -> list[dict]:
    scored = []
    for u in lines:
        text = str(u.get("text") or "").strip()
        if not text:
            continue
        scored.append((_score_line(text), u))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for i, (sc, u) in enumerate(scored[:limit]):
        if role == "hook" and sc < 10:
            continue
        if role != "hook" and sc < 0:
            continue
        out.append(
            {
                "clip_id": f"{role}_{i:03d}",
                "role": "hook" if role == "hook" else role,
                "text": str(u.get("text") or "").strip(),
                "t0_ms": int(u.get("t0_ms") or 0),
                "t1_ms": int(u.get("t1_ms") or 0),
                "score": float(sc),
            }
        )
    return out


def _asr_video(video: Path, work: Path) -> list[dict]:
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "audio_16k.wav"
    if not wav.exists() or wav.stat().st_size < 1000:
        print("  extract", video.name)
        extract_wav(video, wav)
    print("  asr", video.name)
    raw = asr_local(wav)
    (work / "transcript_asr.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return raw


def _pair_in_folder(d: Path) -> tuple[Path, Path] | None:
    vids = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    if len(vids) < 2:
        if len(vids) == 1:
            # only one file: treat as positive only
            return vids[0], vids[0]
        return None
    vids.sort(key=lambda p: p.stat().st_size)
    # smallest = finished positive, largest = source/negative
    return vids[0], vids[-1]


def main() -> int:
    folder = Path(r"C:\Users\MR\Desktop\检查文件\学习2.0\新建文件夹 (18)\新建文件夹")
    if len(sys.argv) > 1:
        folder = Path(sys.argv[1])
    if not folder.exists():
        print("missing", folder)
        return 1

    # ensure baseline negatives exist
    seed_negative_live_phrases()

    work_root = ROOT / "output" / "learning_bootstrap" / "learn2_pairs"
    work_root.mkdir(parents=True, exist_ok=True)

    subs = sorted([p for p in folder.iterdir() if p.is_dir()], key=lambda x: x.name)
    print("pair folders", len(subs), folder)
    results = []

    for d in subs:
        pair = _pair_in_folder(d)
        if not pair:
            print("skip empty", d.name)
            continue
        pos, neg = pair
        print(f"\n=== {d.name}")
        print(" POS", pos.name, f"{pos.stat().st_size/1e6:.1f}MB")
        print(" NEG", neg.name, f"{neg.stat().st_size/1e6:.1f}MB")
        t0 = time.time()
        try:
            pos_raw = _asr_video(pos, work_root / d.name / "pos")
            if neg.resolve() != pos.resolve():
                neg_raw = _asr_video(neg, work_root / d.name / "neg")
            else:
                neg_raw = []

            pos_slots_all = _to_slots(pos_raw, "hook", limit=20)
            # split: top as golden, rest trust
            golden = [dict(s, role="hook") for s in pos_slots_all[:8]]
            trust = [
                dict(s, role="trust", clip_id=f"trust_{i:03d}")
                for i, s in enumerate(pos_slots_all[8:16])
            ]
            after = {"golden": golden, "trust": trust, "cta": []}

            # negatives: take low-quality / live-feeling lines from source
            neg_candidates = []
            for u in neg_raw:
                text = str(u.get("text") or "").strip()
                if not text:
                    continue
                sc = _score_line(text)
                live_hits = sum(1 for b in _LIVE_BAD if b in text)
                if live_hits > 0 or sc < 10:
                    neg_candidates.append((sc - live_hits * 10, u))
            neg_candidates.sort(key=lambda x: x[0])  # worst first
            before_trust = []
            for i, (_, u) in enumerate(neg_candidates[:20]):
                before_trust.append(
                    {
                        "clip_id": f"neg_{i:03d}",
                        "role": "trust",
                        "text": str(u.get("text") or "").strip(),
                        "t0_ms": int(u.get("t0_ms") or 0),
                        "t1_ms": int(u.get("t1_ms") or 0),
                        "score": 1,
                    }
                )
            # also put a few worst into golden-before so dropping them penalizes hook
            before_golden = before_trust[:5]
            for s in before_golden:
                s["role"] = "hook"
            before = {"golden": before_golden, "trust": before_trust[5:], "cta": []}

            prefs = record_plan_feedback(
                job_id=f"pair::{d.name}::{pos.name}",
                before_plan=before,
                after_plan=after,
                source="bootstrap_posneg_pairs",
            )
            dt = time.time() - t0
            item = {
                "folder": d.name,
                "pos": pos.name,
                "neg": neg.name,
                "pos_asr": len(pos_raw),
                "neg_asr": len(neg_raw),
                "after_hooks": len(after["golden"]),
                "after_trust": len(after["trust"]),
                "before_drop_like": len(before_golden) + len(before["trust"]),
                "events": (prefs.get("stats") or {}).get("events"),
                "elapsed": round(dt, 1),
            }
            print(
                f"  learned +hooks={item['after_hooks']} +trust={item['after_trust']} "
                f"-negish={item['before_drop_like']} events={item['events']} {dt:.1f}s"
            )
            results.append(item)
        except Exception as e:
            print("  FAIL", e)
            results.append({"folder": d.name, "error": str(e)})

    st = learning_status()
    summary = {
        "folder": str(folder),
        "results": results,
        "learning": {
            "events": st.get("events"),
            "kept_slots": st.get("kept_slots"),
            "dropped_slots": st.get("dropped_slots"),
            "hook_slots": st.get("hook_slots"),
            "top_hook": st.get("top_hook"),
            "top_drop": st.get("top_drop"),
            "updated_at": st.get("updated_at"),
        },
    }
    out = work_root / "summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print("events", st.get("events"), "kept", st.get("kept_slots"), "dropped", st.get("dropped_slots"))
    print("top_hook", st.get("top_hook")[:15])
    print("top_drop", st.get("top_drop")[:15])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
