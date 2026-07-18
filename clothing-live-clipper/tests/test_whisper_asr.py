from __future__ import annotations

from clipper.whisper_asr import _verbose_to_utterances


def test_verbose_segments_to_ms():
    payload = {
        "text": "收腰显瘦 券后129",
        "segments": [
            {"id": 0, "start": 1.2, "end": 3.5, "text": " 收腰显瘦"},
            {"id": 1, "start": 4.0, "end": 5.2, "text": "券后129"},
        ],
    }
    utts = _verbose_to_utterances(payload)
    assert len(utts) == 2
    assert utts[0].text == "收腰显瘦"
    assert utts[0].t0_ms == 1200
    assert utts[0].t1_ms == 3500
    assert utts[1].t0_ms == 4000
