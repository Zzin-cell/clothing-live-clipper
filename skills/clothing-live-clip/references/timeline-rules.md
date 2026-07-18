# Timeline rules (~60s, golden ~20s)

## Structure

| segment | time | content |
|---------|------|---------|
| golden | 0–20s (18–22) | best selling_point; prefer +fit or +fabric; strong price allowed |
| trust | ~20–50s | detail → fabric/fit expand → scene/outfit (**no size**) |
| cta | last ~10s | price/offer/CTA（含小黄车、N号链接、弹窗、加购下单）; else best selling_point + missing_price |

Total target 60s, tolerance 55–65s. short_content=true if not enough material.
NEVER pad with size/sentiment/chitchat.

## Golden hard rules

Must: understandable buy reason; full sentences.
Forbid in golden: size, sentiment, chitchat, long brand story, pure negative without fix.

## Validate

1. golden non-empty
2. no hard-exclude lexicon in any plan slot text
3. golden has selling_point or strong price (prefer also fit|fabric)
4. record golden_weight_ratio from clipper if present
5. on fail: tighten filter and rerun once; still fail → publish_ready=false, need_review

## Selection order before clipper

1. clips from host & non-excluded lines (0.5–15s, merge 1–3 short sentences OK)
2. lock golden ~20s → cta ~10s → fill trust, dedupe
3. clipper plan.json is render timeline source of truth
