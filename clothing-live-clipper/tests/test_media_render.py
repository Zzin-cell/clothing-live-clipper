from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clipper.media import (
    RenderProfile,
    apply_join_overlaps,
    build_cut_cmd,
    get_render_profile,
    join_overlap_source_ms,
    pick_video_encoder,
    render_plan,
)


def test_get_render_profile_draft_smaller_and_no_handles():
    d = get_render_profile("draft")
    f = get_render_profile("final")
    assert isinstance(d, RenderProfile)
    assert d.max_edge <= 720
    assert d.edge_fade_s <= f.edge_fade_s
    assert d.smooth_handle_ms <= f.smooth_handle_ms
    assert d.crf >= f.crf
    assert d.join_overlap_frames >= 1
    assert f.join_overlap_frames >= 1


def test_join_overlap_source_ms_two_frames():
    # 2 frames @ 30fps @ 1x ≈ 67ms; @ 1.4x ≈ 93ms
    assert join_overlap_source_ms(frames=2, fps=30, playback_speed=1.0) in {66, 67}
    assert join_overlap_source_ms(frames=2, fps=30, playback_speed=1.4) >= 90
    assert join_overlap_source_ms(frames=0, fps=30, playback_speed=1.0) == 0


def test_apply_join_overlaps_expands_neighbors():
    segs = [(1000, 3000), (5000, 8000), (10000, 12000)]
    out = apply_join_overlaps(segs, overlap_ms=67)
    assert out[0] == (1000, 3067)  # only expand end into next
    assert out[1][0] == 5000 - 67 and out[1][1] == 8000 + 67
    assert out[2] == (10000 - 67, 12000)  # only expand start
    assert apply_join_overlaps(segs, overlap_ms=0) == segs


def test_build_cut_cmd_includes_speed_in_one_pass():
    cmd = build_cut_cmd(
        ffmpeg="ffmpeg",
        video=Path("in.mp4"),
        t0_ms=1000,
        t1_ms=4000,
        out_path=Path("part.mp4"),
        target_w=720,
        target_h=1280,
        fps=25,
        edge_fade_s=0.04,
        video_fade_s=0.0,
        audio_fade_s=0.04,
        playback_speed=1.4,
        vcodec="libx264",
        v_extra=["-preset", "ultrafast", "-crf", "28"],
        threads=2,
    )
    assert cmd[0] == "ffmpeg"
    # source window still original; speed applied in filters
    assert "-ss" in cmd and "-t" in cmd
    joined = " ".join(cmd)
    assert "setpts=PTS/1.40000" in joined or "setpts=PTS/1.4" in joined
    assert "atempo=" in joined
    # must not leave raw speed for a second full-file pass
    assert "-c:v" in cmd
    # no video black fade between cuts (afade is OK; bare video fade is not)
    assert ",fade=t=in" not in joined and " fade=t=in" not in joined
    assert ",fade=t=out" not in joined and " fade=t=out" not in joined
    # audio ease only
    assert "afade=" in joined
    # cover-crop, not black pad
    assert "force_original_aspect_ratio=increase" in joined
    assert "pad=" not in joined


def test_build_cut_cmd_no_video_black_fade_by_default():
    cmd = build_cut_cmd(
        ffmpeg="ffmpeg",
        video=Path("in.mp4"),
        t0_ms=0,
        t1_ms=2000,
        out_path=Path("part.mp4"),
        target_w=1080,
        target_h=1920,
        fps=30,
        edge_fade_s=0.10,  # legacy large value must not create video black fade
        playback_speed=1.0,
        vcodec="libx264",
        v_extra=["-preset", "ultrafast", "-crf", "23"],
        threads=2,
    )
    joined = " ".join(cmd)
    # extract -vf value only
    vf = joined.split("-vf ", 1)[1].split(" -af ", 1)[0]
    assert "fade=" not in vf
    assert "afade=" in joined


def test_build_cut_cmd_speed_1_skips_setpts():
    cmd = build_cut_cmd(
        ffmpeg="ffmpeg",
        video=Path("in.mp4"),
        t0_ms=0,
        t1_ms=1000,
        out_path=Path("part.mp4"),
        target_w=720,
        target_h=1280,
        fps=30,
        edge_fade_s=0.0,
        playback_speed=1.0,
        vcodec="libx264",
        v_extra=["-preset", "ultrafast", "-crf", "26"],
        threads=2,
    )
    joined = " ".join(cmd)
    assert "setpts=" not in joined
    assert "atempo=" not in joined


def test_pick_video_encoder_prefers_nvenc_when_available(monkeypatch):
    monkeypatch.setenv("CLIPPER_RENDER_HW", "auto")
    with patch("clipper.media._ffmpeg_has_encoder", return_value=True):
        name, extra = pick_video_encoder(profile=get_render_profile("draft"))
    assert name == "h264_nvenc"
    assert any("cq" in str(x) or "p1" in str(x) or "preset" in str(x) for x in extra)


def test_pick_video_encoder_can_force_libx264(monkeypatch):
    monkeypatch.setenv("CLIPPER_RENDER_HW", "off")
    name, extra = pick_video_encoder(profile=get_render_profile("final"))
    assert name == "libx264"
    assert "-preset" in extra


def test_render_plan_single_pass_no_second_speed_encode(tmp_path: Path):
    """With playback_speed!=1, apply_playback_speed must NOT be called after concat."""
    video = tmp_path / "src.mp4"
    video.write_bytes(b"0")
    out = tmp_path / "preview.mp4"
    segs = [(0, 1000), (2000, 3500)]

    def fake_cut(video, t0, t1, out_path, **kwargs):
        Path(out_path).write_bytes(b"part")
        return Path(out_path)

    def fake_concat(parts, out_mp4, **kwargs):
        Path(out_mp4).write_bytes(b"joined")
        return Path(out_mp4)

    with (
        patch("clipper.media._probe_stream_size", return_value=(1080, 1920)),
        patch("clipper.media.cut_segment", side_effect=fake_cut) as cut_mock,
        patch("clipper.media.concat_segments", side_effect=fake_concat),
        patch("clipper.media.apply_playback_speed") as speed_mock,
        patch("clipper.media.probe_duration_ms", return_value=60_000),
    ):
        render_plan(
            video,
            segs,
            out,
            work_dir=tmp_path / "_parts",
            smooth=True,
            playback_speed=1.4,
            profile="draft",
        )
    assert cut_mock.call_count == 2
    # single-pass: speed applied in cut, not second full encode
    speed_mock.assert_not_called()
    # draft passes speed into cut
    for call in cut_mock.call_args_list:
        assert call.kwargs.get("playback_speed") == 1.4
        assert call.kwargs.get("profile") == "draft" or call.kwargs.get("target_w")


def test_render_plan_reuses_unchanged_parts(tmp_path: Path):
    video = tmp_path / "src.mp4"
    video.write_bytes(b"0")
    work = tmp_path / "_parts"
    work.mkdir()
    # pre-seed part matching first segment after handle math is hard;
    # we mock cut and inspect reuse via cache sidecar instead.
    out = tmp_path / "out.mp4"
    segs1 = [(1000, 3000), (5000, 8000)]
    segs2 = [(1000, 3000), (5000, 9000)]  # only second changed

    cut_calls: list[tuple[int, int]] = []

    def fake_cut(video, t0, t1, out_path, **kwargs):
        cut_calls.append((int(t0), int(t1)))
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # realistic size so reuse checks pass
        p.write_bytes(b"part-bytes-xxxxxx")
        return p

    def fake_concat(parts, out_mp4, **kwargs):
        Path(out_mp4).write_bytes(b"joined")
        return Path(out_mp4)

    common = dict(
        work_dir=work,
        smooth=False,
        edge_fade_s=0.0,
        playback_speed=1.0,
        profile="draft",
        reuse_parts=True,
    )
    with (
        patch("clipper.media._probe_stream_size", return_value=(720, 1280)),
        patch("clipper.media.cut_segment", side_effect=fake_cut),
        patch("clipper.media.concat_segments", side_effect=fake_concat),
        patch("clipper.media.apply_playback_speed"),
        patch("clipper.media.probe_duration_ms", return_value=60_000),
    ):
        render_plan(video, segs1, out, **common)
        n1 = len(cut_calls)
        cut_calls.clear()
        render_plan(video, segs2, out, **common)
        n2 = len(cut_calls)
    assert n1 == 2
    # second render: only the changed segment re-cut
    assert n2 == 1
