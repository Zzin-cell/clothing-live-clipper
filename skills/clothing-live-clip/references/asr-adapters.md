# External ASR adapters

## Rule

Video-only input → obtain aligned transcript BEFORE claims/ranking.
Do not invent timestamps.

## Accepted formats

### JSON array

```json
[
  {"utt_id": "u1", "text": "收腰显瘦", "t0_ms": 12000, "t1_ms": 18000}
]
```

### SRT

Standard SRT; convert to ms when calling clipper (clipper loads .srt).

## Quality checks

- empty transcript → fail job
- garbage/garbled ratio high → asr_low_quality warning
- timestamps must be monotonic enough to cut video

## Agent behavior

1. If user already provides transcript path → use it
2. Else run user-preferred local/cloud ASR tool if configured in environment
3. Else STOP with this file's JSON example and ask user to supply transcript

## Output location

`output/{job_id}/transcript_raw.json`
