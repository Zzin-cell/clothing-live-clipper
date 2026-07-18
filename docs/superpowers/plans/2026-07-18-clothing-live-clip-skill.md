# Clothing Live Clip Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落盘可发现、可执行、可学习改进的 Agent Skill `clothing-live-clip`：编排外置 ASR + 文本主播过滤 + 硬排除尺码/情怀 + 调用 `clothing-live-clipper` 产出约 60s（黄金 20s）成片，并带案例/评测闭环。

**Architecture:** Skill 以 `SKILL.md` 为决策主流程，细则在 `references/`，契约与模板在 `assets/`，金标与指标在 `eval/`。执行真相来自过滤后的 transcript + clipper 的 `plan.json`/`final.mp4`；学习真相来自 `cases/`（draft→confirmed→promoted）与金标回归。不在本计划内改 clipper 打分代码；用调用前过滤保证硬排除。

**Tech Stack:** Agent Skill（Markdown/YAML frontmatter）、现有 Python 包 `clothing-live-clipper`、ffmpeg、外置 ASR（产出 json/srt）、pytest 仅用于 clipper 回归（若触及）；skill 本身用场景脚本/清单做合规验证。

**Spec:** `docs/superpowers/specs/2026-07-18-clothing-live-clip-skill-design.md`

## Global Constraints

- Skill 安装根目录：`C:\Users\MR\.agents\skills\clothing-live-clip\`
- clipper 仓库路径（本机）：`C:\Users\MR\AppData\grok\clothing-live-clipper\`
- 成片目标 **60s**，容差 **55–65s**；黄金开头 **0–20s**（约 18–22s）
- **硬排除：** `size`、`sentiment`（情怀）、纯 `chitchat` 不得进入 `transcript_for_clipper` 与最终 plan
- 主要成分以口播认定：`fit` / `fabric` / `selling_point` / `detail` / `scene` / `outfit` / `price`
- 主播识别：**文本启发式 only**（无声纹）
- 输入默认**仅视频** → 必须先外置 ASR；无转写则中止并给格式
- `description` **只写触发条件**，禁止总结工作流步骤（SDO）
- 案例晋升：仅 `confirmed` + 回归后可改 `references/`
- 提交信息避免依赖 shell 拆分复杂引号；Windows cmd 下 commit message 可用连字符句

---

## File Structure

```
C:\Users\MR\.agents\skills\clothing-live-clip\
  SKILL.md
  references\
    host-heuristics.md
    claim-taxonomy.md
    timeline-rules.md
    clipper-cli.md
    asr-adapters.md
    learning-loop.md
  assets\
    transcript.schema.json
    case-template.md
    exclude-lexicon.md
  eval\
    metrics.md
    golden\
      G001_fit_fabric_hook.json
      G001_expected.md
      G002_size_sentiment_exclude.json
      G002_expected.md
      G003_mixed_sentiment_keep.json
      G003_expected.md
    scenarios\
      baseline-checks.md
  scripts\
    check_plan_exclusions.py
```

| 路径 | 职责 |
|------|------|
| `SKILL.md` | 触发、步骤 0–8、硬门禁、改进入口 |
| `references/host-heuristics.md` | 主播 KEEP/REJECT 规则 |
| `references/claim-taxonomy.md` | 成分定义、情怀操作定义、混合句 |
| `references/timeline-rules.md` | 60s 三段与黄金校验 |
| `references/clipper-cli.md` | Windows 下如何跑 clipper |
| `references/asr-adapters.md` | 转写格式与质检 |
| `references/learning-loop.md` | 案例状态机与晋升 |
| `assets/*` | schema、案例模板、排除词表 |
| `eval/*` | 指标、金标口播、期望、场景检查清单 |
| `scripts/check_plan_exclusions.py` | 扫描 plan/review 是否泄漏硬排除词（机械门禁） |

---

### Task 1: Scaffold + 场景基线（RED）

**Files:**
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\scenarios\baseline-checks.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\metrics.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\assets\case-template.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\assets\transcript.schema.json`

**Interfaces:**
- Produces: 可执行的合规检查清单（无 skill 正文时预期失败）；案例模板字段与设计规格 §8.2 一致

- [ ] **Step 1: 创建目录**

```bat
mkdir C:\Users\MR\.agents\skills\clothing-live-clip\references
mkdir C:\Users\MR\.agents\skills\clothing-live-clip\assets
mkdir C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden
mkdir C:\Users\MR\.agents\skills\clothing-live-clip\eval\scenarios
mkdir C:\Users\MR\.agents\skills\clothing-live-clip\scripts
```

Expected: 无报错，目录存在。

- [ ] **Step 2: 写入 `eval/metrics.md`（完整内容）**

```markdown
# Metrics

## Gate metrics (must not regress)

| id | metric | pass line | how |
|----|--------|-----------|-----|
| M1 | hard_exclude_leak_rate | 0 | plan/review text hits size or sentiment lexicon → fail |
| M2 | golden_has_selling_or_strong_price | true | golden slots claim types |
| M3 | forbidden_in_golden | 0 | size/sentiment/chitchat in golden |
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
```

- [ ] **Step 3: 写入 `assets/case-template.md`**

```markdown
# Case {{case_id}}

- date:
- source_job:
- category:
- status: draft | confirmed | promoted | rejected

## Snippet

- t0_ms:
- t1_ms:
- text: |

## Labels

- label: good_hook | bad_hook | false_host | missed_host | false_exclude | missed_exclude | leak_size | leak_sentiment | good_claim | bad_claim | mixed_keep | tool_gap

## Expected vs actual

- expected:
- actual:

## Patch suggestion

- (one line; empty if none)
```

- [ ] **Step 4: 写入 `assets/transcript.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ClothingLiveTranscript",
  "type": "array",
  "items": {
    "type": "object",
    "required": ["utt_id", "text", "t0_ms", "t1_ms"],
    "properties": {
      "utt_id": { "type": "string" },
      "text": { "type": "string" },
      "t0_ms": { "type": "integer", "minimum": 0 },
      "t1_ms": { "type": "integer", "minimum": 0 },
      "confidence": { "type": ["number", "null"] }
    }
  }
}
```

- [ ] **Step 5: 写入 `eval/scenarios/baseline-checks.md`（技能未完成前的失败基线）**

```markdown
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
| S1 | | |
| S2 | | |
| S3 | | |
| S4 | | |
| S5 | | |
| S6 | | |
```

- [ ] **Step 6: 确认 RED（无 SKILL.md 时）**

人工或子 Agent：只根据用户一句「做服装直播切片」处理 S1–S2 口播。  
Expected: **S1/S2 至少一项 FAIL**（保留尺码或情怀）。在 `baseline-checks.md` 的 Scorecard `without skill` 列填入结果。  
若意外全 PASS，仍继续写 skill，但要收紧 skill 内反例说明。

- [ ] **Step 7: Commit scaffold**

```bat
cd /d C:\Users\MR\AppData\grok
git add docs\superpowers\plans\2026-07-18-clothing-live-clip-skill.md
git status
```

Note: skill 目录在 `.agents` 下可能不在 grok 仓库内。若 `.agents` 无 git：

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git init
git add eval assets
git commit -m "test: scaffold clothing-live-clip skill RED baseline"
```

若用户希望 skill 纳入 grok 仓，可额外复制到 `C:\Users\MR\AppData\grok\skills\clothing-live-clip\` 并 git add；**默认以 `.agents\skills` 为运行时位置**。

---

### Task 2: 排除词表 + 成分与主播 reference

**Files:**
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\assets\exclude-lexicon.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\references\claim-taxonomy.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\references\host-heuristics.md`

**Interfaces:**
- Consumes: 设计规格 §4–§5
- Produces: Agent 可执行的 KEEP/REJECT 与 claim 标注规则；`exclude-lexicon.md` 供 scripts 与人工扫描共用

- [ ] **Step 1: 写入 `assets/exclude-lexicon.md`**

```markdown
# Hard-exclude lexicon (substring match, Chinese)

## size (never in clipper transcript / plan)

尺码, 选码, 偏大, 偏小, 胸围, 腰围, 臀围, 身高, 穿M, 穿S, 穿L, 穿XL, 均码, 加大码, 码数, 建议穿

## sentiment pure (never in clipper transcript / plan if no core claim co-occurs)

做了五年, 不容易, 感谢陪伴, 创业, 初心, 故事是这样, 一路走来, 谢谢支持我, 喜欢我的人

## chitchat pure

家人们, 老铁们, 听得到吗, 扣1, 扣一, 点点关注, 双击, 晚上好啊, 来了吗

## Notes

- If line also contains fit/fabric/selling_point/price core keywords, do NOT pure-exclude (mixed_keep).
- Expand only via confirmed cases + regression.
```

- [ ] **Step 2: 写入 `references/claim-taxonomy.md`**

```markdown
# Claim taxonomy

## Keep types (may enter timeline)

| type | meaning | timeline role |
|------|---------|---------------|
| fit | 版型 | golden preferred / trust |
| fabric | 布料材质手感功能 | golden preferred / trust |
| selling_point | 特点痛点效果人群 | golden core |
| detail | 设计细节 | trust |
| scene | 场景 | trust |
| outfit | 搭配 | trust |
| price | 价格优惠CTA | cta; strong price may compete golden |

Every claim MUST map to transcript t0_ms–t1_ms.

## Hard exclude types (never enter transcript_for_clipper)

| type | rule |
|------|------|
| size | size lexicon or size-advice intent |
| sentiment | emotional/brand story without core keep-type payload |
| chitchat | pure interaction / hello / 扣1 with no product claim |

## Mixed line rule

If sentiment-like words AND any keep type signal in same utterance → KEEP whole line, tag keep types, optional label mixed_keep in cases. Do not drop.

## Priority for selection

selling_point > fit/fabric combo > strong price > detail/scene/outfit

## Core keyword hints (non-exhaustive)

- fit: 收腰, 修身, oversize, A字, 廓形, 高腰, 版型
- fabric: 醋酸, 凉感, 雪纺, 羊毛, 纯棉, 面料, 透气, 垂感
- selling_point: 显瘦, 遮肉, 梨形, 闭眼入, 显腿长, 不挑人
- price: 券后, 只要, 原价, 链接, 库存, 拍下
```

- [ ] **Step 3: 写入 `references/host-heuristics.md`**

```markdown
# Host text heuristics (no voiceprint)

## Goal

Build host_transcript for product talk. Reject cohost/system/pure chat when possible.

## KEEP signals

- product demo language: 来看这件, 上身, 面料给你看
- fit/fabric/selling_point/price language
- first-person try-on: 我身上, 看我腰

## REJECT reasons

| reason | signals |
|--------|---------|
| cohost | 老板说得对, 给老板点赞 |
| system_or_spam | 谢谢舰, 欢迎来到直播间 spam chains |
| chitchat | 听得到吗, 家人们在吗 with no product |
| off_product | read-comment logistics only, no clothing claims |
| hard_exclude | size / pure sentiment / pure chitchat |

## Decision order

1. hard_exclude → reject from clipper path (log excluded.json)
2. strong non-host and no keep-type → reject
3. strong host or keep-type → keep
4. ambiguous → neighborhood vote ±2 utterances; still ambiguous → reject

## Degraded mode

If almost everything would reject or no host signal exists: keep all non-hard-exclude lines, set host_filter=degraded in run_report.

## Report fields

kept_count, rejected_count, rejected_ratio, degraded, top_reject_reasons
```

- [ ] **Step 4: 用 S1–S3 对 reference 做桌面走查**

打开 `baseline-checks.md` S1–S3，只依据 Task 2 三份文件手算 KEEP/REJECT。  
Expected: S1 删尺码、S2 删纯情怀、S3 保留混合句。  
若不一致，立刻改词表/规则后再往下。

- [ ] **Step 5: Commit**

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git add assets\exclude-lexicon.md references\claim-taxonomy.md references\host-heuristics.md
git commit -m "docs: claim host and exclude references"
```

---

### Task 3: 时间轴 / ASR / clipper CLI references

**Files:**
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\references\timeline-rules.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\references\asr-adapters.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\references\clipper-cli.md`

**Interfaces:**
- Consumes: clipper README 与 CLI（`clothing-live-clipper\README.md`, `src\clipper\cli.py`）
- Produces: Agent 可复制的 Windows 命令与时间轴硬规则

- [ ] **Step 1: 写入 `references/timeline-rules.md`**

```markdown
# Timeline rules (~60s, golden ~20s)

## Structure

| segment | time | content |
|---------|------|---------|
| golden | 0–20s (18–22) | best selling_point; prefer +fit or +fabric; strong price allowed |
| trust | ~20–50s | detail → fabric/fit expand → scene/outfit (**no size**) |
| cta | last ~10s | price/offer/CTA; else best selling_point + missing_price |

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
```

- [ ] **Step 2: 写入 `references/asr-adapters.md`**

```markdown
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
```

- [ ] **Step 3: 写入 `references/clipper-cli.md`**

```markdown
# clothing-live-clipper CLI

## Paths

- Repo: `C:\Users\MR\AppData\grok\clothing-live-clipper`
- Module: `python -m clipper` with `PYTHONPATH=src`

## Setup (once)

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run (plan + render)

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
.venv\Scripts\python -m clipper run --video PATH\TO\video.mp4 --transcript PATH\TO\transcript_for_clipper.json --out PATH\TO\output\job_id
```

## Plan only

```bat
.venv\Scripts\python -m clipper run --transcript PATH\TO\transcript_for_clipper.json --out PATH\TO\output\job_id --no-render
```

## Required precondition

`transcript_for_clipper.json` must already have host filter + hard excludes applied.
Never pass raw livestream transcript with size/sentiment/chitchat lines.

## Outputs to read

- plan.json
- review.md
- final.mp4 (if render)
- clips.json

## ffmpeg

If missing, clipper skips render; treat as incomplete when user asked for mp4.
```

- [ ] **Step 4: 在本机 dry-run clipper plan-only（验证文档命令）**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
if exist .venv\Scripts\python (.venv\Scripts\python -m clipper run --transcript tests\fixtures\sample_transcript.json --out output\_skill_doc_check --no-render) else (python -m clipper run --transcript tests\fixtures\sample_transcript.json --out output\_skill_doc_check --no-render)
```

Expected: 生成 `output\_skill_doc_check\plan.json` 与 `review.md`。  
若失败：先修环境再改 `clipper-cli.md` 命令。

- [ ] **Step 5: Commit**

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git add references\timeline-rules.md references\asr-adapters.md references\clipper-cli.md
git commit -m "docs: timeline asr clipper references"
```

---

### Task 4: 学习闭环 reference + 金标 fixtures

**Files:**
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\references\learning-loop.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G001_fit_fabric_hook.json`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G001_expected.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G002_size_sentiment_exclude.json`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G002_expected.md`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G003_mixed_sentiment_keep.json`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G003_expected.md`

**Interfaces:**
- Produces: 晋升纪律；三套金标口播与期望（供人工/Agent 对照，不必先自动化全绿）

- [ ] **Step 1: 写入 `references/learning-loop.md`**

```markdown
# Learning loop

## Loop

run job → auto validate → human feedback → cases → promote rules → eval regression

## Case statuses

draft → confirmed → promoted | rejected

Only confirmed may promote into references/assets lexicon.

## After every job

Write `output/{job_id}/cases/draft-{job_id}.md` using assets/case-template.md (short OK).

## On user correction

1. Upsert case with status confirmed
2. If lexicon-level: patch_suggestion then edit exclude-lexicon or heuristics AFTER user OK
3. If one-off: leave case, do not globalize

## Promotion discipline

1. case first, rule second
2. one issue class per promotion
3. re-check eval/golden G001–G003 and baseline S1–S6
4. one-line changelog at bottom of changed reference
5. never promote from draft only

## Changelog footer example

```
## Changelog
- 2026-07-18: add 胸围 to size exclude (case C012)
```
```

- [ ] **Step 2: 写入 G001 金标**

`G001_fit_fabric_hook.json`:

```json
[
  {"utt_id": "u1", "text": "家人们晚上好呀", "t0_ms": 0, "t1_ms": 3000},
  {"utt_id": "u2", "text": "这件收腰版型特别显瘦", "t0_ms": 4000, "t1_ms": 9000},
  {"utt_id": "u3", "text": "面料是凉感醋酸不粘身", "t0_ms": 10000, "t1_ms": 15000},
  {"utt_id": "u4", "text": "券后一百二十九链接带上了", "t0_ms": 50000, "t1_ms": 56000}
]
```

`G001_expected.md`:

```markdown
# G001 expected

- reject/exclude from clipper: u1 chitchat
- keep: u2, u3, u4
- golden should prioritize u2/u3 over u1
- cta prefers u4
- claims: u2 fit+selling_point; u3 fabric; u4 price
```

- [ ] **Step 3: 写入 G002 金标**

`G002_size_sentiment_exclude.json`:

```json
[
  {"utt_id": "u1", "text": "我们品牌一路走来不容易感谢陪伴", "t0_ms": 0, "t1_ms": 5000},
  {"utt_id": "u2", "text": "袖口有隐形松紧不勒肉", "t0_ms": 6000, "t1_ms": 11000},
  {"utt_id": "u3", "text": "身高160建议穿M偏小拍大一码", "t0_ms": 12000, "t1_ms": 18000},
  {"utt_id": "u4", "text": "梨形姐妹遮胯显瘦闭眼入", "t0_ms": 20000, "t1_ms": 26000}
]
```

`G002_expected.md`:

```markdown
# G002 expected

- exclude u1 sentiment
- exclude u3 size
- keep u2 detail, u4 selling_point
- plan must not contain 尺码/穿M/感谢陪伴/不容易
- golden prefers u4 then u2
```

- [ ] **Step 4: 写入 G003 金标**

`G003_mixed_sentiment_keep.json`:

```json
[
  {"utt_id": "u1", "text": "心疼自己就收这件收腰显瘦十斤", "t0_ms": 0, "t1_ms": 6000},
  {"utt_id": "u2", "text": "谢谢喜欢我的人一路支持", "t0_ms": 7000, "t1_ms": 11000}
]
```

`G003_expected.md`:

```markdown
# G003 expected

- KEEP u1 (mixed_keep): has selling_point+fit
- EXCLUDE u2 pure sentiment
- false_exclude on u1 is a regression
```

- [ ] **Step 5: Commit**

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git add references\learning-loop.md eval\golden
git commit -m "docs: learning loop and golden fixtures"
```

---

### Task 5: 硬排除检查脚本 + SKILL.md（GREEN 主体）

**Files:**
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\scripts\check_plan_exclusions.py`
- Create: `C:\Users\MR\.agents\skills\clothing-live-clip\SKILL.md`

**Interfaces:**
- Consumes: `plan.json` 或任意 UTF-8 文本；词表内嵌与 `exclude-lexicon.md` 对齐
- Produces: exit code 0=无泄漏，1=泄漏；SKILL 主流程供 Agent 加载

- [ ] **Step 1: 写失败用例数据并先跑脚本（RED）**

创建临时文件 `C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\_leak_plan_sample.json`:

```json
{
  "golden": [{"text": "身高160建议穿M"}],
  "trust": [],
  "cta": []
}
```

写入 `scripts/check_plan_exclusions.py`：

```python
# -*- coding: utf-8 -*-
"""Fail if plan/review text contains hard-exclude size/sentiment markers."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SIZE = [
    "尺码",
    "选码",
    "偏大",
    "偏小",
    "胸围",
    "腰围",
    "臀围",
    "身高",
    "穿M",
    "穿S",
    "穿L",
    "穿XL",
    "均码",
    "加大码",
    "码数",
    "建议穿",
]
SENTIMENT = [
    "做了五年",
    "不容易",
    "感谢陪伴",
    "创业",
    "初心",
    "故事是这样",
    "一路走来",
    "谢谢支持我",
    "喜欢我的人",
]


def iter_texts(obj) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, str):
                out.append(v)
            else:
                out.extend(iter_texts(v))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(iter_texts(x))
    elif isinstance(obj, str):
        out.append(obj)
    return out


def find_leaks(texts: list[str]) -> list[str]:
    hits: list[str] = []
    for t in texts:
        for w in SIZE + SENTIMENT:
            if w.lower() in t.lower():
                hits.append(f"{w} <= {t}")
    return hits


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_plan_exclusions.py <plan.json|review.md>")
        return 2
    path = Path(argv[1])
    raw = path.read_text(encoding="utf-8")
    texts: list[str]
    if path.suffix.lower() == ".json":
        texts = iter_texts(json.loads(raw))
    else:
        texts = [raw]
    hits = find_leaks(texts)
    if hits:
        print("LEAKS:")
        for h in hits:
            print(" -", h)
        return 1
    print("OK: no hard-exclude leaks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Run:

```bat
python C:\Users\MR\.agents\skills\clothing-live-clip\scripts\check_plan_exclusions.py C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\_leak_plan_sample.json
```

Expected: exit code **1**, print LEAKS with 身高/穿M.

- [ ] **Step 2: 对干净 plan 跑脚本（GREEN for script）**

```bat
python C:\Users\MR\.agents\skills\clothing-live-clip\scripts\check_plan_exclusions.py C:\Users\MR\AppData\grok\clothing-live-clipper\tests\fixtures\sample_transcript.json
```

若 sample 含排除词则换用自建干净 json：

```json
[{"utt_id":"u1","text":"收腰显瘦醋酸面料","t0_ms":0,"t1_ms":3000}]
```

Expected: `OK: no hard-exclude leaks`, exit 0.

- [ ] **Step 3: 写入 `SKILL.md`（完整）**

```markdown
---
name: clothing-live-clip
description: Use when clipping clothing livestream VODs into short selling videos, when host-speech-based fit/fabric/selling-point extraction is needed, when size charts and sentimental storytelling must stay out of the cut, when targeting about 60s clips with the strongest first ~20s, or when improving those clipping rules via cases and eval.
---

# Clothing Live Clip

## Overview

Turn a clothing livestream **video** into a ~60s selling cut whose **first ~20s** carry the strongest buy reasons. Judge components from **host speech** (fit / fabric / selling points). **Hard-exclude** size advice and pure sentiment. Orchestrate external ASR + `clothing-live-clipper`. Improve via cases, feedback, and golden eval.

**REQUIRED details:** read files under `references/` when executing the matching step. **REQUIRED learning rules:** `references/learning-loop.md`.

## When to use

- Clothing livestream VOD → short selling clip
- Need golden hook ~20s + total ~60s
- Need speech-based 版型/布料/卖点 and no 尺码/情怀 in the cut
- Need to record mistakes into cases and promote rules safely

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
Run:

```bat
python scripts/check_plan_exclusions.py output/{job_id}/plan.json
```

(Use skill-absolute path to script.) Exit 1 → fix filter and rerun once or `need_review`.

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
When user asks to improve rules: cluster confirmed cases → propose patch → regression on `eval/golden` + `eval/scenarios/baseline-checks.md` → edit references → changelog line.

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
```

- [ ] **Step 4: Commit**

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git add scripts\check_plan_exclusions.py SKILL.md eval\golden\_leak_plan_sample.json
git commit -m "feat: clothing-live-clip SKILL and leak checker"
```

---

### Task 6: 端到端对照金标 + 场景 GREEN + 可选 clipper 串联

**Files:**
- Modify: `C:\Users\MR\.agents\skills\clothing-live-clip\eval\scenarios\baseline-checks.md`（填 with skill 列）
- Create (optional job out): `C:\Users\MR\AppData\grok\clothing-live-clipper\output\G002_skill_e2e\`

**Interfaces:**
- Consumes: G002 json → 人工/Agent 过滤 → clipper plan-only → check_plan_exclusions.py

- [ ] **Step 1: 手工/Agent 按 SKILL 从 G002 生成 `transcript_for_clipper.json`**

Expected content essentially:

```json
[
  {"utt_id": "u2", "text": "袖口有隐形松紧不勒肉", "t0_ms": 6000, "t1_ms": 11000},
  {"utt_id": "u4", "text": "梨形姐妹遮胯显瘦闭眼入", "t0_ms": 20000, "t1_ms": 26000}
]
```

Save as:
`C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G002_transcript_for_clipper.json`

- [ ] **Step 2: clipper plan-only**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
python -m clipper run --transcript C:\Users\MR\.agents\skills\clothing-live-clip\eval\golden\G002_transcript_for_clipper.json --out output\G002_skill_e2e --no-render
```

Expected: `plan.json` exists; selected texts are subset of u2/u4.

- [ ] **Step 3: 泄漏检查**

```bat
python C:\Users\MR\.agents\skills\clothing-live-clip\scripts\check_plan_exclusions.py C:\Users\MR\AppData\grok\clothing-live-clipper\output\G002_skill_e2e\plan.json
```

Expected: `OK: no hard-exclude leaks`

- [ ] **Step 4: 对 G001/G003 做同样过滤对照 `*_expected.md`**

不必都跑 clipper；至少文档化 kept/excluded 列表与 expected 一致。

- [ ] **Step 5: 重跑场景 S1–S6（with skill）**

用加载本 skill 的 Agent（或执行者自检清单）填写 `baseline-checks.md` scorecard `with skill` 列。  
Expected: **S1–S6 全部 PASS**（S5 为行为检查：无转写会停）。

- [ ] **Step 6: 若有 FAIL，最小改动 skill/references 后只回归失败项**

禁止无关大重构。

- [ ] **Step 7: Commit**

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git add eval
git commit -m "test: green baseline and golden e2e filters"

cd /d C:\Users\MR\AppData\grok
git add docs\superpowers\plans\2026-07-18-clothing-live-clip-skill.md
git commit -m "docs: clothing-live-clip skill implementation plan"
```

---

### Task 7: 发现性与收尾检查

**Files:**
- Verify: `C:\Users\MR\.agents\skills\clothing-live-clip\SKILL.md` frontmatter
- Optional Create: `C:\Users\MR\AppData\grok\skills\clothing-live-clip\README.md` 指向 `.agents` 路径（仅当需要仓内索引）

- [ ] **Step 1: description SDO 检查**

Read `SKILL.md` frontmatter.  
Expected: starts with `Use when`；**没有**逐步工作流摘要（无 “then call clipper after filter…” 长链条）。

- [ ] **Step 2: 文件齐全检查**

```bat
dir /s /b C:\Users\MR\.agents\skills\clothing-live-clip
```

Expected 至少包含：SKILL.md、6 个 references、assets 3、eval metrics+scenarios+golden、scripts\check_plan_exclusions.py

- [ ] **Step 3: Spec coverage 快速勾选**

对照设计规格：主播启发式、硬排除、60s/20s、ASR、clipper、publish_ready、学习闭环、产物目录 — 均有文件落点。

- [ ] **Step 4: Final commit（若有 README 或修补）**

```bat
cd /d C:\Users\MR\.agents\skills\clothing-live-clip
git add -A
git status
git commit -m "chore: finalize clothing-live-clip skill packaging"
```

---

## Self-Review (plan author)

### Spec coverage

| Spec area | Task |
|-----------|------|
| Skill 目录与 SKILL 主流程 | T1 scaffold, T5 SKILL.md |
| 文本主播启发式 | T2 host-heuristics |
| 成分 + 硬排除 + 混合句 | T2 claim-taxonomy + exclude-lexicon |
| 黄金 20s / 60s | T3 timeline-rules |
| 仅视频 + 外置 ASR | T3 asr-adapters；T5 gates |
| 编排 clipper 出片 | T3 clipper-cli；T6 e2e |
| 校验 / publish_ready | T5 SKILL + script；T3 timeline |
| 案例/反馈/评测/晋升 | T1 template+metrics；T4 learning-loop+golden；T6 scenarios |
| 与 clipper size 分差异 | 全局约束 + filter-before-clipper（不改 rank.py） |

### Placeholder scan

无 TBD/“implement later”；命令与文件内容均为可粘贴全文。

### Type/path consistency

- job 输出字段名全程：`transcript_raw.json`、`transcript_for_clipper.json`、`excluded.json`、`run_report.md`
- 硬排除类型名：`size` / `sentiment` / `chitchat`
- clipper 路径与设计一致

### Out of scope (explicit)

- 不改 `clothing-live-clipper` 内 size 计分
- 不做声纹、剪映草稿、云 ASR 厂商锁死
- 不做在线模型微调

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-18-clothing-live-clip-skill.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每个 Task 新开子代理，任务间两段式审查，迭代快  
2. **Inline Execution** — 本会话用 executing-plans 按任务推进并设检查点  

**Which approach?**
