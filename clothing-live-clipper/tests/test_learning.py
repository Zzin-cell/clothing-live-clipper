from clipper.learning import (
    clear_learning,
    learned_text_score,
    load_preferences,
    record_plan_feedback,
)


def test_record_feedback_boosts_kept_hook_and_penalizes_dropped(tmp_path, monkeypatch):
    # isolate learning store
    import clipper.learning as L

    monkeypatch.setattr(L, "LEARN_DIR", tmp_path)
    monkeypatch.setattr(L, "PREF_PATH", tmp_path / "preferences.json")
    monkeypatch.setattr(L, "EVENTS_PATH", tmp_path / "events.jsonl")

    before = {
        "golden": [
            {"text": "家人们扣1点关注", "t0_ms": 0, "t1_ms": 2000, "role": "hook"},
            {"text": "独家凉感面料显瘦不透", "t0_ms": 3000, "t1_ms": 6000, "role": "hook"},
        ],
        "trust": [],
        "cta": [],
    }
    after = {
        "golden": [
            {"text": "独家凉感面料显瘦不透", "t0_ms": 3000, "t1_ms": 6000, "role": "hook"},
            {"text": "收腰版型梨形闭眼入", "t0_ms": 7000, "t1_ms": 10000, "role": "hook"},
        ],
        "trust": [{"text": "通勤也好穿", "t0_ms": 11000, "t1_ms": 13000, "role": "trust"}],
        "cta": [],
    }
    prefs = record_plan_feedback(job_id="job_test", before_plan=before, after_plan=after)
    assert prefs["stats"]["events"] == 1
    # kept/added feature phrases should get positive hook boost
    assert learned_text_score("独家凉感面料显瘦不透", for_hook=True) > 0
    assert learned_text_score("收腰版型梨形闭眼入", for_hook=True) > 0
    # dropped live phrase should be penalized
    assert learned_text_score("家人们扣1点关注", for_hook=True) < 0

    # clear learning resets scores
    st = clear_learning(keep_events_backup=False)
    assert st["events"] == 0
    assert abs(learned_text_score("独家凉感面料显瘦不透", for_hook=True)) < 0.01
