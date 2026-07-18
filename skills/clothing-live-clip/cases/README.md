# Confirmed cases home

Durable store for **confirmed** clip cases used by the learning loop.

## Retention: draft → confirmed

| status | where |
|--------|--------|
| `draft` | `output/{job_id}/cases/draft-{job_id}.md` only — ephemeral job output |
| `confirmed` | **Copy here** under `cases/` after human confirm |
| `promoted` / `rejected` | stay in `cases/` with status updated |

**Never promote rules from draft only.** Confirm first, then copy the case into this directory, then propose reference/lexicon patches and run eval regression.

## Layout example

```
cases/
  README.md
  C012-size-leak-chest.md
  C015-mixed-sentiment-keep.md
```

Use `assets/case-template.md` fields. Name with a stable id when confirming.
