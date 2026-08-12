from __future__ import annotations

from pathlib import Path

from clipper.media import humanize_media_error, validate_input_video


def test_humanize_moov_missing_wechat():
    raw = (
        "[mov,mp4,m4a,3gp,3g2,mj2 @ 0x] moov atom not found "
        "Error opening input file uploads/webwxgetvideo.mp4. "
        "Invalid data found when processing input"
    )
    msg = humanize_media_error(raw, path="uploads/webwxgetvideo.mp4")
    assert "不完整" in msg or "moov" in msg.lower()
    assert "微信" in msg or "webwx" in msg.lower()


def test_validate_too_small_file(tmp_path: Path):
    p = tmp_path / "webwxgetvideo.mp4"
    p.write_bytes(b"\x00" * 100)
    chk = validate_input_video(p)
    assert chk["ok"] is False
    assert chk["error_class"] in {"too_small", "incomplete_mp4", "unreadable"}
    assert chk["error"]


def test_validate_missing_file(tmp_path: Path):
    chk = validate_input_video(tmp_path / "nope.mp4")
    assert chk["ok"] is False
    assert chk["error_class"] == "missing"
