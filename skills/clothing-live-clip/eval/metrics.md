# Metrics

## Gate metrics (must not regress)

| id | metric | pass line | how |
|----|--------|-----------|-----|
| M1 | hard_exclude_leak_rate | 0 | Mechanical: size+sentiment+chitchat via `scripts/check_plan_exclusions.py` on **plan.json** slot texts only (not review.md). Agent still owns semantic mixed_keep — substring may FP on mixed lines; prefer checking the filtered drop set. False LEAK on mixed_keep should not auto-fail publish if claim-taxonomy mixed rule applies (re-check excluded / escalate need_review). |
| M2 | golden_has_selling_or_strong_price | true | golden slots claim types |
| M3 | forbidden_in_golden | 0 | Semantic/golden-content gate: size/sentiment/chitchat must not appear in golden slots (agent + taxonomy). Distinct from M1 mechanical checker — M3 judges intended golden composition; M1 is the mechanical lexicon scan on whole plan.json. |
| M4 | duration_ok_when_enough_material | 55–65s or short_content | from plan total |
| M5 | publish_ready_requires_mp4_and_gates | boolean consistency | run_report |

## Quality metrics (track)

| id | metric | initial target |
|----|--------|----------------|
| Q1 | critical claim precision | >= 0.80 |
| Q2 | golden understandable buy-point | >= 0.80 |
| Q3 | false_exclude of good selling_point | monitor |

## Job report must include

- host kept/rejected counts
- excluded counts by reason
- golden20_passed, publish_ready, warnings
