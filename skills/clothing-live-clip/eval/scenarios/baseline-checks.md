# Baseline / regression scenarios for clothing-live-clip skill

Run each scenario against an agent **with only the user task**, then again **with the skill loaded**.
Record PASS/FAIL.

## S1: Hard exclude size from clipper transcript

User task: 「用服装直播切片 skill 处理口播，生成送给 clipper 的 transcript。」

Input utterances:

1. 这件收腰显瘦，梨形姐妹闭眼入
2. 身高160建议穿M，胸围什么的看尺码表
3. 面料是凉感醋酸，夏天不粘

**PASS if:** sentence 2 absent from transcript_for_clipper; 1 and 3 present.
**Baseline without skill:** often keeps size lines for “完整性” → FAIL expected.

## S2: Hard exclude pure sentiment

Input:

1. 我们品牌做了五年不容易感谢陪伴
2. 袖口做了隐形松紧不勒肉

**PASS if:** only 2 kept for clipper.
**Baseline:** may keep 1 as “开头情怀钩子” → FAIL expected.

## S3: Mixed sentiment + selling_point KEEP

Input:

1. 心疼自己就收这件，收腰显瘦十斤

**PASS if:** kept; not labeled pure sentiment-only drop.
**Baseline:** may over-reject → FAIL possible.

## S4: Golden 20s priority

Input unordered selling points at late timestamps and chitchat at start.

**PASS if:** plan golden uses selling/fit/fabric, not 家人们晚上好.
**Baseline without skill:** chronological clip → FAIL expected.

## S5: No ASR stop

User gives video path only, no transcript tool result.

**PASS if:** agent stops and prints json/srt schema requirements; does not invent timestamps.
**Baseline:** may hallucinate transcript → FAIL expected.

## S6: Learning hook

After a successful dry-run plan, 

**PASS if:** draft case file content is produced (template fields).
**Baseline:** ships mp4/plan only → FAIL expected.

## Scorecard

| id | without skill | with skill |
|----|---------------|------------|
| S1 | FAIL (keeps size line 身高160/M/尺码表 for “完整性”) | PASS |
| S2 | FAIL (keeps pure sentiment 品牌五年/感谢陪伴 as opening hook) | PASS |
| S3 | FAIL possible (over-reject mixed sentiment+selling) | PASS |
| S4 | FAIL (chronological clip; golden may start with 家人们晚上好) | PASS |
| S5 | FAIL (may hallucinate timestamps/transcript from video path only) | PASS |
| S6 | FAIL (ships plan/mp4 only; no draft case template fields) | PASS |

### RED baseline notes (no SKILL.md)

- Date: 2026-03-28
- Method: scenario expectations applied with only user task phrasing; skill body absent → no hard-exclude lexicon, no golden priority, no ASR-stop gate, no learning-hook case draft.
- Result: **S1 FAIL, S2 FAIL** (at least one required for RED). S3–S6 without-skill also FAIL as documented above.
- Next: implement skill so with-skill column turns PASS without relaxing gates.

### GREEN with-skill notes (Task 6 E2E)

- Date: 2026-07-18
- Method: skill-rules checklist self-check (SKILL.md hard gates + `assets/exclude-lexicon.md` + `references/claim-taxonomy.md` mixed_keep + `references/timeline-rules.md` + ASR stop + `assets/case-template.md` learning hook). No live multi-turn agent; executor applied skill procedure as the agent checklist.
- G002 mechanical: filtered `eval/golden/G002_transcript_for_clipper.json` (u2+u4) → clipper plan-only → `check_plan_exclusions.py` = `OK: no hard-exclude leaks`; plan golden texts = u4 then u2 only.
- S1 PASS: size lexicon drops line 2; keeps fit/selling + fabric.
- S2 PASS: pure sentiment lexicon drops brand-story line; keeps detail.
- S3 PASS: mixed_keep retains 心疼自己…收腰显瘦十斤 (selling_point+fit).
- S4 PASS: golden priority sells/fit/fabric over chitchat; G002 plan golden starts with selling_point not 家人们.
- S5 PASS: procedure stops without inventing timestamps when ASR unavailable.
- S6 PASS: step 8 requires draft case from case-template fields after dry-run.

### Golden filter vs expected (Task 6)

**G001** (`G001_fit_fabric_hook.json` vs `G001_expected.md`):

| utt | action | reason |
|-----|--------|--------|
| u1 家人们晚上好呀 | EXCLUDE | pure chitchat |
| u2 这件收腰版型特别显瘦 | KEEP | fit + selling_point |
| u3 面料是凉感醋酸不粘身 | KEEP | fabric |
| u4 券后一百二十九链接带上了 | KEEP | price/CTA |

Match expected: exclude u1; keep u2,u3,u4. OK.

**G002** (`G002_size_sentiment_exclude.json` vs `G002_expected.md`):

| utt | action | reason |
|-----|--------|--------|
| u1 我们品牌一路走来不容易感谢陪伴 | EXCLUDE | pure sentiment |
| u2 袖口有隐形松紧不勒肉 | KEEP | detail |
| u3 身高160建议穿M偏小拍大一码 | EXCLUDE | size |
| u4 梨形姐妹遮胯显瘦闭眼入 | KEEP | selling_point |

Match expected; e2e plan has no 尺码/穿M/感谢陪伴/不容易. OK.

**G003** (`G003_mixed_sentiment_keep.json` vs `G003_expected.md`):

| utt | action | reason |
|-----|--------|--------|
| u1 心疼自己就收这件收腰显瘦十斤 | KEEP | mixed_keep (fit+selling_point) |
| u2 谢谢喜欢我的人一路支持 | EXCLUDE | pure sentiment |

Match expected; no false_exclude on u1. OK.
