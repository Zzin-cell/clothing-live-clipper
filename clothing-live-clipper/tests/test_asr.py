from pathlib import Path

from clipper.asr import load_transcript, load_transcript_srt

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


def test_load_json_fixture():
    tr = load_transcript(FIXTURE)
    assert len(tr) >= 10
    assert tr[0].t0_ms == 0
    assert "家人们" in tr[0].text


def test_load_srt(tmp_path: Path):
    srt = tmp_path / "a.srt"
    srt.write_text(
        """1
00:00:01,000 --> 00:00:03,500
收腰显瘦好穿

2
00:00:04,000 --> 00:00:06,000
券后只要99
""",
        encoding="utf-8",
    )
    tr = load_transcript_srt(srt)
    assert len(tr) == 2
    assert tr[0].t0_ms == 1000
    assert tr[0].t1_ms == 3500
    assert "显瘦" in tr[0].text
