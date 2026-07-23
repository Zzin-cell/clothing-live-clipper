from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES = ROOT / "clothing-live-clipper" / "docs" / "RELEASE_v0.21.md"
REPO = "Zzin-cell/clothing-live-clipper"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print(">", " ".join(cmd[:8]), "...")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def create_or_edit(tag: str, title: str, body: str, target: str = "feature/web-video-workstation") -> None:
    # try create
    p = run(
        [
            "gh",
            "api",
            f"repos/{REPO}/releases",
            "-X",
            "POST",
            "-f",
            f"tag_name={tag}",
            "-f",
            f"target_commitish={target}",
            "-f",
            f"name={title}",
            "-f",
            f"body={body}",
            "-F",
            "draft=false",
            "-F",
            "prerelease=false",
        ]
    )
    if p.returncode == 0:
        data = json.loads(p.stdout)
        print("created", tag, data.get("html_url"))
        return
    print("create failed:", (p.stderr or p.stdout)[-500:])
    # if exists, find id and patch
    p2 = run(["gh", "api", f"repos/{REPO}/releases/tags/{tag}"])
    if p2.returncode != 0:
        print("cannot fetch existing release", tag, p2.stderr)
        raise SystemExit(1)
    rel = json.loads(p2.stdout)
    rid = rel["id"]
    p3 = run(
        [
            "gh",
            "api",
            f"repos/{REPO}/releases/{rid}",
            "-X",
            "PATCH",
            "-f",
            f"name={title}",
            "-f",
            f"body={body}",
        ]
    )
    if p3.returncode != 0:
        print(p3.stderr or p3.stdout)
        raise SystemExit(p3.returncode)
    data = json.loads(p3.stdout)
    print("updated", tag, data.get("html_url"))


def main() -> int:
    body21 = NOTES.read_text(encoding="utf-8")
    create_or_edit(
        "v0.21-docs-release",
        "v0.21 Xiaomian CapCut Docs & Capability Release",
        body21,
    )
    body20 = (
        "Xiaomian white UI + GPU ASR + manual reverse-cut + optional learning.\n\n"
        "See docs/CHANGELOG.md and README for details."
    )
    create_or_edit(
        "v0.20-xiaomian-capcut-ui",
        "v0.20 Xiaomian CapCut UI Milestone",
        body20,
    )
    p = run(["gh", "api", f"repos/{REPO}/releases", "--jq", ".[].tag_name + \" \" + .[].html_url"])
    print(p.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
