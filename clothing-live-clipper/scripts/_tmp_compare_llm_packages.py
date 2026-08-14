# -*- coding: utf-8 -*-
"""Compare LLM-related config/code between (3) package and new full package."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

HOME = Path(os.environ["USERPROFILE"])
DESK = HOME / "Desktop"

PKG3 = DESK / "小面CapCut-便携版 (3)" / "小面CapCut-便携版"
PKG_NEW = DESK / "小面CapCut-便携版"
ZIP3 = DESK / "小面CapCut-便携版 (3).zip"
ZIP_NEW = DESK / "小面CapCut-便携版.zip"


def sha1(p: Path, limit: int = 0) -> str:
    if not p.exists():
        return "MISSING"
    h = hashlib.sha1()
    with p.open("rb") as f:
        if limit:
            h.update(f.read(limit))
        else:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                h.update(b)
    return h.hexdigest()[:12]


def load_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e)}


def mask(k: str) -> str:
    k = (k or "").strip()
    if not k:
        return "(empty)"
    return (k[:4] + "..." + k[-4:]) if len(k) > 8 else "***"


def show_llm(label: str, root: Path):
    print(f"\n======== {label} ========")
    print("root exists:", root.exists(), root)
    paths = [
        root / "output" / "user_config" / "llm.json",
        root / "clothing-live-clipper" / "output" / "user_config" / "llm.json",
    ]
    for p in paths:
        d = load_json(p)
        print(f"\n{p.relative_to(root) if root.exists() and p.exists() else p}")
        if d is None:
            print("  (missing)")
            continue
        if "_error" in d:
            print("  error", d["_error"])
            continue
        for k in (
            "enabled",
            "plan_enabled",
            "base_url",
            "model",
            "organization",
            "api_style",
            "last_endpoint",
            "last_auth_variant",
            "last_payload_variant",
            "last_ok_ms",
        ):
            v = d.get(k)
            print(f"  {k}: {v}")
        print("  api_key:", mask(str(d.get("api_key") or "")))
        # extra headers keys only
        eh = d.get("extra_headers") or {}
        print("  extra_headers keys:", list(eh.keys()) if isinstance(eh, dict) else eh)


def compare_files(a: Path, b: Path, rels: list[str]):
    print("\n======== file hash compare (3 vs NEW) ========")
    for rel in rels:
        pa, pb = a / rel, b / rel
        sa, sb = sha1(pa), sha1(pb)
        same = sa == sb and sa != "MISSING"
        print(f"{'SAME' if same else 'DIFF':4} {rel}")
        print(f"     (3) {sa} exists={pa.exists()} size={pa.stat().st_size if pa.exists() else 0}")
        print(f"     new {sb} exists={pb.exists()} size={pb.stat().st_size if pb.exists() else 0}")


def scan_openai_defaults(root: Path):
    """Peek key LLM defaults from source if present."""
    files = {
        "user_llm.py": root / "clothing-live-clipper" / "src" / "clipper" / "user_llm.py",
        "llm_plan.py": root / "clothing-live-clipper" / "src" / "clipper" / "llm_plan.py",
        "openai_compat.py": root / "clothing-live-clipper" / "src" / "clipper" / "openai_compat.py",
        "system_status.py": root / "clothing-live-clipper" / "src" / "clipper" / "system_status.py",
        "app.js": root / "clothing-live-clipper" / "src" / "clipper" / "static" / "app.js",
    }
    print(f"\n======== source peeks: {root.name} ========")
    for name, p in files.items():
        if not p.exists():
            print(name, "MISSING")
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        print(f"\n--- {name} size={len(t)} ---")
        for key in (
            "DEFAULT_LLM_BASE_URL",
            "DEFAULT_LLM_MODEL",
            "LIGHT_MAX_CLAUSES",
            "PLAN_TIMEOUT_S",
            "PLAN_MAX_TOKENS",
            "timeout",
            "fast=True",
            "last_endpoint",
            "siliconflow",
            "enable_thinking",
            "max_tokens",
        ):
            if key in t:
                # print a few nearby lines
                lines = t.splitlines()
                hits = [i for i, ln in enumerate(lines) if key in ln]
                print(f"  hits {key}: {len(hits)}")
                for i in hits[:4]:
                    print(f"    L{i+1}: {lines[i].strip()[:140]}")


def job_llm_stats(root: Path):
    jobs = root / "clothing-live-clipper" / "output" / "web_jobs"
    if not jobs.exists():
        print("\nno web_jobs in", root)
        return
    lats = []
    models = {}
    paths = {}
    statuses = {}
    n = 0
    for d in jobs.iterdir():
        if not d.is_dir():
            continue
        meta = d / "job_meta.json"
        if not meta.exists():
            continue
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        n += 1
        st = str(m.get("llm_status") or "?")
        statuses[st] = statuses.get(st, 0) + 1
        ms = m.get("llm_latency_ms")
        if isinstance(ms, (int, float)):
            lats.append(float(ms))
        md = str(m.get("llm_model") or "?")
        models[md] = models.get(md, 0) + 1
        pt = str(m.get("llm_path") or m.get("planner") or "?")
        paths[pt] = paths.get(pt, 0) + 1
    print(f"\n======== job LLM stats: {root} jobs={n} ========")
    print("llm_status", statuses)
    print("models", models)
    print("paths/planner sample", dict(list(paths.items())[:12]))
    if lats:
        lats.sort()
        print(
            "llm_latency_ms count",
            len(lats),
            "min",
            int(min(lats)),
            "p50",
            int(lats[len(lats)//2]),
            "p90",
            int(lats[int(len(lats)*0.9)]),
            "max",
            int(max(lats)),
            "avg",
            int(sum(lats)/len(lats)),
        )
    else:
        print("no llm_latency_ms fields in metas")


def main():
    print("ZIP3", ZIP3.exists(), ZIP3.stat().st_size if ZIP3.exists() else 0)
    print("ZIP_NEW", ZIP_NEW.exists(), ZIP_NEW.stat().st_size if ZIP_NEW.exists() else 0)
    print("PKG3", PKG3.exists())
    print("PKG_NEW", PKG_NEW.exists())

    show_llm("PACKAGE (3)", PKG3)
    show_llm("PACKAGE NEW", PKG_NEW)

    if PKG3.exists() and PKG_NEW.exists():
        compare_files(
            PKG3,
            PKG_NEW,
            [
                "clothing-live-clipper/src/clipper/user_llm.py",
                "clothing-live-clipper/src/clipper/llm_plan.py",
                "clothing-live-clipper/src/clipper/openai_compat.py",
                "clothing-live-clipper/src/clipper/system_status.py",
                "clothing-live-clipper/src/clipper/static/app.js",
                "clothing-live-clipper/src/clipper/web.py",
            ],
        )
        scan_openai_defaults(PKG3)
        scan_openai_defaults(PKG_NEW)
        job_llm_stats(PKG3)
        job_llm_stats(PKG_NEW)


if __name__ == "__main__":
    main()
