---
name: clothing-live-clip
description: Use when clipping clothing livestream VODs into short selling videos, when the user says 处理队列 or process queue for the Web job queue, when host-speech-based fit/fabric/selling-point extraction is needed, when size charts and sentimental storytelling must stay out of the cut, or when targeting about 60s clips with the strongest first ~20s.
---

# Clothing Live Clip

## Overview

Turn a clothing livestream **video** into a ~60s selling cut whose **first ~20s** carry the strongest buy reasons. Judge components from **host speech** (fit / fabric / selling points). **Hard-exclude** size advice and pure sentiment. Orchestrate smart-speech timeline + `clothing-live-clipper`.  

**Web architecture:** Browser only submits jobs. **You (the Agent) are the worker.** When the user says **处理队列** / **process queue**, claim queued Web jobs and process them end-to-end.

**REQUIRED details:** read files under `references/` when executing the matching step. **REQUIRED learning rules:** `references/learning-loop.md`.

## When to use

- User says **处理队列** / process queue (Web uploaded videos waiting)
- Clothing livestream VOD → short selling clip
- Need golden hook ~20s + total ~60s
- Need speech-based 版型/布料/卖点 and no 尺码/情怀 in the cut

## Process queue (Web worker mode)

Default Web base: `http://127.0.0.1:8787`

1. `GET /api/agent/next`  
   - If `job` is null → tell user queue empty  
   - Else note `paths.job_dir`, `paths.video`, `job.target_seconds`, `job.render_requested`
2. **Smart speech timeline** from `paths.video` (do **not** invent timestamps):  
   - Use any working ASR you have (local whisper, other APIs, tools).  
   - Midpoint provider without whisper models is OK to skip; use what works.  
   - Write `job_dir/transcript_asr.json` as array:  
     `[{ "utt_id", "text", "t0_ms", "t1_ms" }, ...]`
3. Host filter + claims + hard exclude (this skill steps 2–4)  
   - Write `transcript_for_clipper.json` into `job_dir`
4. Run clipper into **same** `job_dir`:

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
set PATH=%LOCALAPPDATA%\ffmpeg\bin;%PATH%
python -m clipper run --video "{paths.video}" --transcript "{job_dir}\transcript_for_clipper.json" --out "{job_dir}" --target-seconds {target_seconds}
```

If render not requested, add `--no-render`.

5. Validate plan exclusions (scripts/check_plan_exclusions.py on `job_dir/plan.json`)
6. Complete:

```bat
curl -s -X POST http://127.0.0.1:8787/api/agent/jobs/{job_id}/complete -H "Content-Type: application/json" -d "{\"status\":\"success\",\"transcript_source\":\"agent_skill\"}"
```

On failure:

```bat
curl -s -X POST http://127.0.0.1:8787/api/agent/jobs/{job_id}/fail -H "Content-Type: application/json" -d "{\"error\":\"...\"}"
```

7. Loop `GET /api/agent/next` until queue empty. Summarize for user.

**Do not** call Web's in-process Whisper path for queue jobs — Web only stores files.

## Hard gates (never violate)

1. Do not send `size` / pure `sentiment` / pure `chitchat` lines into clipper transcript.
2. Do not pad duration with excluded types.
3. Do not invent ASR timestamps.
4. Do not mark `publish_ready=true` if mp4 missing (when render requested), golden fails, leak found, or no selling_point.
5. Do not promote rules from `draft` cases without confirm + regression.

## Procedure

### 0. Preflight

- Video path exists
- Locate clipper per `references/clipper-cli.md`
- ffmpeg required for mp4; else plan-only and say so

### 1. ASR

Follow `references/asr-adapters.md`.  
Write `output/{job_id}/transcript_raw.json`. Stop if unavailable.

### 2. Host filter

Follow `references/host-heuristics.md`.  
Emit kept host lines + rejected reasons into run_report.

### 3. Claims

Follow `references/claim-taxonomy.md`.  
Tag fit/fabric/selling_point/detail/scene/outfit/price with timestamps.

### 4. Hard exclude

Apply `assets/exclude-lexicon.md` + taxonomy mixed-line rule.  
Write `excluded.json` and `transcript_for_clipper.json` (only kept lines).

### 5. Clipper

Run commands in `references/clipper-cli.md` with **filtered** transcript.  
Read `plan.json` / `review.md` / `final.mp4`.

### 6. Validate

Follow `references/timeline-rules.md` and `eval/metrics.md`.  
Run mechanical M1 on **plan.json only** (not review.md):

```bat
python scripts/check_plan_exclusions.py output/{job_id}/plan.json
```

(Use skill-absolute path to script.) Exit 1 → fix filter and rerun once or `need_review`.  
Note: checker is substring mechanical (size+sentiment+chitchat). False LEAK on legitimate mixed_keep should not auto-fail publish if claim-taxonomy mixed rule applies — re-check `excluded.json` / drop set and escalate `need_review` rather than blind fail.

### 7. Deliver

Ensure folder layout:

```
output/{job_id}/
  transcript_raw.json
  transcript_for_clipper.json
  excluded.json
  plan.json
  review.md
  final.mp4
  run_report.md
  cases/draft-{job_id}.md
```

`run_report.md` must include: host stats, exclude stats, golden20_passed, publish_ready, warnings.

### 8. Learning hook

Always write draft case. On user corrections follow `references/learning-loop.md`.  
Confirmed cases live under skill `cases/` (copy from draft after confirm; never promote from draft only — see `cases/README.md`).  
When user asks to improve rules: cluster confirmed cases in `cases/` → propose patch → regression on `eval/golden` + `eval/scenarios/baseline-checks.md` → edit references → changelog line.

## publish_ready

true only if: mp4 present (if required), golden ok, no hard-exclude leaks, >=1 selling_point, no fatal asr_low_quality.  
`missing_price` → warn only, still can be ready.

## Rationalizations (ignore these)

| Excuse | Reality |
|--------|---------|
| "Size helps trust segment" | Spec hard-excludes size from the cut |
| "Sentiment hook boosts completion" | Pure sentiment never enters clipper transcript |
| "I'll filter after clipper" | Filter before clipper; post-scan is backup gate only |
| "No ASR so I'll guess lines" | Stop and request transcript |
| "Draft case is enough to edit globals" | confirmed + regression required |
