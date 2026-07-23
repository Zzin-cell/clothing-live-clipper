import json
import subprocess

payload = {
    "tag_name": "v0.20-xiaomian-capcut-ui",
    "target_commitish": "feature/web-video-workstation",
    "name": "v0.20 Xiaomian CapCut UI Milestone",
    "body": (
        "Xiaomian white UI + GPU ASR + reverse cut + optional learning.\n\n"
        "See docs/CHANGELOG.md and README."
    ),
    "draft": False,
    "prerelease": False,
}
p = subprocess.run(
    [
        "gh",
        "api",
        "--method",
        "POST",
        "repos/Zzin-cell/clothing-live-clipper/releases",
        "--input",
        "-",
    ],
    input=json.dumps(payload),
    text=True,
    capture_output=True,
    encoding="utf-8",
)
print("code", p.returncode)
print(p.stdout[:1000] if p.stdout else "")
print(p.stderr[:1000] if p.stderr else "")
if p.returncode == 0:
    print(json.loads(p.stdout).get("html_url"))
else:
    # maybe exists now
    p2 = subprocess.run(
        ["gh", "api", "repos/Zzin-cell/clothing-live-clipper/releases/tags/v0.20-xiaomian-capcut-ui"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print("get", p2.returncode, (p2.stdout or p2.stderr)[:300])
