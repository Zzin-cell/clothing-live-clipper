# Learning loop

## Loop

run job → auto validate → human feedback → cases → promote rules → eval regression

## Case statuses

draft → confirmed → promoted | rejected

Only confirmed may promote into references/assets lexicon.

## After every job

Write `output/{job_id}/cases/draft-{job_id}.md` using assets/case-template.md (short OK).

## On user correction

1. Upsert case with status confirmed and **copy into skill `cases/`** (durable store; see `cases/README.md`). Drafts stay under `output/{job_id}/cases/` only.
2. If lexicon-level: patch_suggestion then edit exclude-lexicon or heuristics AFTER user OK
3. If one-off: leave confirmed case in `cases/`, do not globalize

## Promotion discipline

1. case first, rule second — promote only from confirmed files in skill `cases/`
2. one issue class per promotion
3. re-check eval/golden G001–G003 and baseline S1–S6
4. one-line changelog at bottom of changed reference
5. never promote from draft only

## Changelog footer example

```
## Changelog
- 2026-07-18: add 胸围 to size exclude (case C012)
```
