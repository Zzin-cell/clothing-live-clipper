from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
notes = (ROOT / "clothing-live-clipper" / "docs" / "RELEASE_v0.21.md").read_text(encoding="utf-8")
repo = "Zzin-cell/clothing-live-clipper"

# get release id
p = subprocess.run(
    ["gh", "api", f"repos/{repo}/releases/tags/v0.21-docs-release"],
    capture_output=True,
    text=True,
    encoding="utf-8",
)
p.check_returncode()
rid = json.loads(p.stdout)["id"]
print("release_id", rid)

# patch via stdin json
payload = {
    "name": "v0.21 Xiaomian CapCut Docs Release",
    "body": notes,
}
proc = subprocess.run(
    ["gh", "api", "--method", "PATCH", f"repos/{repo}/releases/{rid}", "--input", "-"],
    input=json.dumps(payload, ensure_ascii=False),
    capture_output=True,
    text=True,
    encoding="utf-8",
)
print(proc.stdout[-500:])
print(proc.stderr[-500:])
proc.check_returncode()
data = json.loads(proc.stdout)
print("updated", data.get("html_url"))

# ensure v0.20 release exists
p2 = subprocess.run(
    ["gh", "api", f"repos/{repo}/releases/tags/v0.20-xiaomian-capcut-ui"],
    capture_output=True,
    text=True,
    encoding="utf-8",
)
if p2.returncode != 0:
    payload20 = {
        "tag_name": "v0.20-xiaomian-capcut-ui",
        "target_commitish": "feature/web-video-workstation",
        "name": "v0.20 Xiaomian CapCut UI Milestone",
        "body": "Xiaomian white UI + GPU ASR + reverse cut + optional learning.\n\nSee docs/CHANGELOG.md and README.",
        "draft": False,
        "prerelease": False,
    }
    proc2 = subprocess.run(
        ["gh", "api", "--method", "POST", f"repos/{repo}/releases", "--input", "-"],
        input=json.dumps(payload20, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(proc2.stderr[-300:])
    proc2.check_returncode()
    print("created v0.20", json.loads(proc2.stdout).get("html_url"))
else:
    print("v0.20 exists", json.loads(p2.stdout).get("html_url"))

# list
plist = subprocess.run(
    ["gh", "api", f"repos/{repo}/releases", "--jq", ".[].tag_name + \" \" + .[].html_url"],
    capture_output=True,
    text=True,
    encoding="utf-8",
)
print(plist.stdout)
