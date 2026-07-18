# 服装带货直播切片 Agent Skill 设计规格

| 项 | 内容 |
|----|------|
| 版本 | v1.0 |
| 日期 | 2026-07-18 |
| 状态 | 对话已确认 §1–§6，待用户审阅本文件 |
| 形态 | Agent Skill（`SKILL.md` + references + eval/cases） |
| 关联实现 | `clothing-live-clipper/`（编排调用，不在首版强制改 rank 逻辑） |
| 关联总规格 | `docs/superpowers/specs/2026-07-18-clothing-live-clipper-design.md` |

---

## 1. 产品定位

### 1.1 Skill 是什么

**名称：** `clothing-live-clip`（目录名 / skill name）

**一句话：** 指导 Agent 将服装带货直播**视频**制成「前 20 秒最强卖点、整片约 60 秒」的带货切片，并以口播认定版型/布料/特点等成分；硬排除报尺码与讲情怀；任务后通过案例库、人工反馈与金标评测持续改进规则。

### 1.2 触发场景（写入 description 的「何时用」，禁止在 description 里总结完整工作流）

- 服装/女装等带货直播回放需要切片带货
- 输入主要是视频，需要约 60s 成片且黄金开头约 20s
- 需要按口播抓版型、布料、卖点等特点，并排除尺码讲解与情怀铺垫
- 需要把切片过程沉淀成可回归、可改进的规则与案例

### 1.3 明确非目标（YAGNI）

- 不在 skill 内训练/微调 ASR 或视觉大模型权重
- 不首版实现声纹主播识别（文本启发式；与总规格 P0 声纹可并行演进）
- 不首版强绑剪映草稿 SDK（总规格 P1；本 skill 以 mp4 + plan 为主）
- 不自动把 draft 案例晋升为全局规则（必须 confirmed + 回归）
- 不负责电商挂车/后台 API

### 1.4 路线选择

**方案 A + 学习闭环：**

- **执行层：** 外置 ASR → 主播文本过滤 → 成分/硬排除 → 调用 `clothing-live-clipper` → ffmpeg 成片
- **校验层：** 黄金 20s、硬排除泄漏、时长、卖点存在性
- **记忆层：** `cases/` 正反例与误杀漏检
- **改进层：** 反馈入库 → 规则/词表/示例晋升 → 金标回归

不做纯 LLM 即兴重排双轨（避免与 clipper 两套真相）。

### 1.5 单次任务成功标准

- 产出 `final.mp4`（或明确失败：无 ASR / 无有效卖点 / 无 ffmpeg 等）
- 目标约 **60s**（容差 55–65s；素材不足可缩短并标注 `short_content`）
- **前 ~20s** 为高转化：卖点优先，最好带版型或布料；无尺码/情怀/纯寒暄
- `run_report.md` 含主播过滤比例、排除统计、校验结果
- 默认写 `cases/draft-{job_id}.md` 供确认

---

## 2. Skill 目录结构

```
skills/clothing-live-clip/
  SKILL.md                      # 主流程与硬门禁（短、可扫描）
  references/
    host-heuristics.md          # 主播文本识别
    claim-taxonomy.md           # 成分定义与硬排除
    timeline-rules.md           # 黄金20s / 信任 / 促单
    clipper-cli.md              # clothing-live-clipper 调用契约
    asr-adapters.md             # 外置 ASR → json/srt
    learning-loop.md            # 案例、反馈、评测、晋升
  assets/
    transcript.schema.json      # 转写字段约定
    case-template.md            # 案例模板
  eval/
    golden/                     # 金标 fixture（脱敏）
    metrics.md                  # 指标与出门线
```

**原则：**

- `description` 只写触发条件，不写步骤摘要（避免 Agent 只读 description 抄近路）
- 细则放 `references/`；`SKILL.md` 用编号步骤 + 决策门禁
- 学习材料与执行流程分离，防止上下文膨胀

**安装位置：** 用户 runtime 的 skills 目录（与 `writing-skills` 约定一致），本仓库可同时保留一份源文件便于版本管理；实现计划阶段确定是复制到 `.agents/skills` 还是仅文档仓。

---

## 3. 一次任务端到端流程

```
输入: 直播视频路径 (+ 可选 out 目录、可选已有转写)
  │
  ├─ 0. 预检: 视频 / ffmpeg / clipper 包路径
  ├─ 1. ASR: 外置转写 → transcript（t0/t1 毫秒）
  ├─ 2. 主播过滤: 文本启发式 → host_transcript + rejected
  ├─ 3. 成分标注: fit / fabric / selling_point / detail / scene / price / outfit …
  ├─ 4. 硬排除: size、sentiment（情怀）、纯 chitchat → 不送 clipper 成片稿
  ├─ 5. 调用 clipper: plan.json + final.mp4
  ├─ 6. 出门校验: 时长、黄金20s、硬排除泄漏、卖点
  ├─ 7. 交付物: mp4 / plan / review / run_report
  └─ 8. 学习钩子: cases/draft；有反馈则 confirmed 入库；复盘时晋升+回归
```

### 3.1 与 clothing-live-clipper 的边界

| Skill（Agent）负责 | clipper 负责 |
|--------------------|--------------|
| ASR 编排、主播启发式、硬排除预处理 | 规则打分、timeline 重排、ffmpeg 拼接 |
| 出片后合规复核 | `plan.json` / `review.md` / `final.mp4` |
| 案例库、评测驱动的规则改进 | 现有 CLI：`python -m clipper run ...` |
| 记录与规范冲突的 tool_gap | 代码修复单独立项（非 skill 文档必含） |

**关键适配：** 当前 clipper 仍可能给 `size` 信任段分。Skill **强制**在调用前从 transcript 删除尺码/情怀/纯寒暄句，从输入侧保证硬排除。

### 3.2 推荐输出目录

```
output/{job_id}/
  transcript_raw.json
  transcript_for_clipper.json
  excluded.json
  claims.json                 # 若已结构化
  plan.json
  review.md
  final.mp4                   # 成功且渲染时
  run_report.md
  cases/draft-{job_id}.md
```

### 3.3 依赖与降级

| 依赖 | 缺失时 |
|------|--------|
| 源视频 | 失败退出 |
| 转写（ASR） | 中止；写出 json/srt 格式与示例，不假装完成 |
| `clothing-live-clipper` | 中止；路径与安装说明 |
| ffmpeg | 允许 plan-only；`render_skipped`；不宣称成片成功 |
| 主播过滤后为空 | 降级全稿 + `host_filter=degraded`，黄金段建议人工复核 |
| 无任何卖点 | 失败或 `need_review`；禁止用情怀/尺码凑时长 |
| clipper/ffmpeg 错误 | 保留 plan 与错误；案例记 `tool_gap` |

---

## 4. 主播文本启发式识别

### 4.1 目标

无声纹时，尽量只保留「主播讲品」句，剔除助播、系统感谢腔、纯互动等。

输出：`host_transcript[]`（保留原时间戳）+ `rejected[]`（含 `reject_reason`）。

### 4.2 信号表

**强主播（倾向 KEEP）**

- 讲品指引：来看这件、上身、面料给你看、领口/袖型细节
- 卖点/版型/布料话术：显瘦、收腰、醋酸、梨形等
- 促单且含商品语境：链接挂上了、库存不多
- 第一人称试穿：我身上、看我腰这里

**强非主播/非讲品（倾向 REJECT）**

| 信号 | reject_reason |
|------|----------------|
| 助播附和 | `cohost` |
| 礼物/欢迎连串 | `system_or_spam` |
| 纯互动无商品 | `chitchat` |
| 读评论且无服装核心成分 | `off_product` |

**弱线索：** 邻域窗口投票（前后各约 2 句）；过短无信息（&lt;4 字且无关键词）剔除。

### 4.3 决策顺序

```
对每条 utterance:
  if 硬排除类（size / sentiment / 纯 chitchat）→ REJECT（成片链路），可记 excluded
  if 强非主播且无核心成分 → REJECT
  if 强主播或核心成分 → KEEP
  if 模糊 → 邻域投票；仍模糊 → REJECT（宁缺毋滥，保黄金开头）
```

全场无法区分主播 → 整稿当作主播讲品，标 `host_filter=degraded`，任务不中止。

### 4.4 run_report 必写

- kept / rejected 句数与占比
- 是否 degraded
- reject_reason Top3

---

## 5. 成分体系与硬排除

### 5.1 可进成片的主要成分（口播认定）

| type | 含义 | 时间轴角色 |
|------|------|------------|
| `fit` | 版型 | 黄金优先 / 信任 |
| `fabric` | 布料/材质/功能手感 | 黄金优先 / 信任 |
| `selling_point` | 特点/痛点/效果/人群 | **黄金核心** |
| `detail` | 设计细节 | 信任 |
| `scene` | 场景 | 信任 |
| `outfit` | 搭配 | 信任 |
| `price` | 价格/优惠/CTA | **促单**；强价格可竞黄金 |

每条 claim 必须映射转写 `t0_ms`–`t1_ms`，禁止无时间戳悬空文案。

### 5.2 硬排除（永不进入 plan / 不送 clipper 成片稿）

| type | 含义 | 处理 |
|------|------|------|
| `size` | 报尺码、选码、围度建议 | 硬排除 |
| `sentiment` | 情怀：品牌故事、创业史、感谢陪伴、无商品信息的情感铺垫 | 硬排除 |
| `chitchat` | 纯寒暄控场 | 硬排除 |

**硬排除三动作：**

1. 不写入 `transcript_for_clipper.json`
2. 出片后扫描 `plan.json` 文本；命中尺码/情怀词表 → 校验失败 → 加词重跑或 `need_review`
3. 写入 `excluded.json` 供学习，不进 timeline

### 5.3 情怀判定（降误杀）

- **排除：** 故事/情绪为主，不承载版型/布料/卖点/价格
- **保留：** 情感话术但有明确卖点（如「心疼自己就收腰显瘦这件」）→ `selling_point`
- **混合句：** 同时含情怀 + 核心成分 → **整句 KEEP**，标核心成分；案例可标 `mixed_keep` 防止规则过猛

### 5.4 优先级（规范层）

`selling_point` > `fit`/`fabric` 组合 > 强 `price` > `detail` / `scene` / `outfit`  
`size` / `sentiment` / 纯 `chitchat` 视为不可入选（输入过滤保证）。

---

## 6. 时间轴规则（黄金 20s + 约 60s）

### 6.1 结构

| 段 | 时间 | 内容 |
|----|------|------|
| 黄金开头 | 0–20s（约 18–22s） | 最强转化；优先卖点 +（版型\|布料）；强价格可竞争 |
| 信任建设 | ~20–50s | 细节 → 布料/版型展开 → 场景/搭配（**无尺码**） |
| 促单收尾 | 最后 ~10s | 价格/优惠/行动指令；无价格则次高卖点 + `missing_price` |

- 总长目标 **60s**，容差 **55–65s**
- 素材不足 → 缩短 + `short_content=true`
- **禁止**用尺码/情怀/寒暄凑时长

### 6.2 黄金 20s

**必须：** 听完能懂为什么买；完整句；优先 `selling_point+(fit|fabric)`  

**禁止：** size / sentiment / chitchat / 冗长铺垫 / 纯负面无转机  

**校验：**

1. golden 非空  
2. 黄金与全片 plan 无硬排除泄漏  
3. 含 selling_point 或强 price（推荐同时有 fit/fabric）  
4. 记录 `golden_weight_ratio`（规范目标 ≥60%；与 clipper 软实现并存时以听感可懂买点 + 无违禁为出门主依据，ratio 写入报告）  
5. 失败 → 重滤/重跑；仍失败 → 可保留产物但 `publish_ready=false`，`need_review`

### 6.3 重排决策顺序（Agent）

1. 从 host 且非硬排除稿得到 clips（完整句；可合并 1–3 句；建议 0.5–15s）  
2. 概念打分：卖点与组合最高 → 价格 → 具体细节 → 场景搭配  
3. 先锁黄金 ~20s → 再锁 CTA ~10s → 剩余信任段并去重  
4. 调用 clipper；以 clipper `plan.json` 为成片时间轴真相  
5. 不在渲染阶段即兴改理解

### 6.4 clipper 调用（逻辑约定）

```text
python -m clipper run --video <视频> --transcript <transcript_for_clipper.json> --out <out>
```

工作目录与 `PYTHONPATH` 以 `references/clipper-cli.md` 为准。

---

## 7. 出片校验与 publish_ready

### 7.1 校验清单

- [ ] 要求成片时 `final.mp4` 存在  
- [ ] 时长 ∈ [55, 65]s 或合法 `short_content`  
- [ ] 黄金 ~20s 无 size/sentiment/chitchat  
- [ ] 全片 plan 无硬排除泄漏  
- [ ] 至少 1 条 selling_point（否则 need_review/失败）  
- [ ] `run_report.md` 完整  

### 7.2 publish_ready

`publish_ready=true` 当且仅当：

- 成片成功（未 skip render）  
- 黄金校验通过  
- 无硬排除泄漏  
- 至少 1 条 selling_point  
- 无致命 `asr_low_quality`（除非用户 overlay）  

`missing_price`：默认 **警告仍可 ready**（与总规格一致）。

---

## 8. 学习闭环

### 8.1 总览

```
执行任务 → 自动校验 → 人工点检/改稿 → cases 入库
                ↑                            │
                └──── 规则/词表晋升 ← 复盘 ←─┘
                            ↑
                     eval/golden 回归
```

### 8.2 案例库字段

| 字段 | 说明 |
|------|------|
| `case_id` / 日期 | 唯一 |
| `source` | job_id、品类 |
| `snippet` | 原文 + 时间 |
| `label` | `good_hook` / `bad_hook` / `false_host` / `missed_host` / `false_exclude` / `missed_exclude` / `leak_size` / `leak_sentiment` / `good_claim` / `bad_claim` / `mixed_keep` / `tool_gap` |
| `expected` / `actual` | 期望与当时行为 |
| `patch_suggestion` | 拟改词表或规则（一句） |
| `status` | `draft` → `confirmed` → `promoted` \| `rejected` |

仅 `confirmed` 可参与晋升。

### 8.3 反馈吸收

用户指出错判时，Agent 必须：

1. 写入/更新案例（不能只改当次成片）  
2. 词表级错误 → `patch_suggestion`，用户同意后改 `references`  
3. 个例 → 留 case，不急改全局  

### 8.4 评测指标（初值可校准）

| 指标 | 初项目标 |
|------|----------|
| 硬排除泄漏率 | **0**（门禁） |
| 黄金段可懂买点 | ≥ 0.80 |
| 关键 claim 精确率（fit/fabric/selling_point/price） | ≥ 0.80 |
| 好卖点被当情怀误杀率 | 监控；升高则收紧情怀规则 |
| 有素材时时长合格率 | ≥ 0.90 |

### 8.5 晋升纪律

1. 先案例，后规则  
2. 一次只晋升一类问题  
3. 改完跑金标或相关 case  
4. references 留一行 changelog  
5. **禁止** draft 未确认直接改全局  

### 8.6 任务末尾义务

- 每次任务写 `cases/draft-*.md`（可短）  
- 用户说判错 → 走反馈入库  
- 用户说复盘/改进规则 → 按 `learning-loop.md` 聚类、提补丁、回归、再改文件  

---

## 9. SKILL.md 内容骨架（实现时展开，非运行时代码）

```markdown
---
name: clothing-live-clip
description: Use when clipping clothing livestream VODs into short selling videos,
  needing host-speech-based fit/fabric/selling-point extraction, hard-excluding
  size charts and sentimental storytelling, targeting ~60s cuts with a strongest
  first ~20s, or when improving those rules via cases and eval.
---

# Clothing Live Clip

## Overview
...

## Required inputs
- video path; ASR external; clipper repo; ffmpeg for render

## Procedure
0 preflight → 1 ASR → 2 host filter → 3 claims → 4 hard exclude
→ 5 clipper → 6 validate → 7 deliver → 8 learning hook

## Hard gates
- never send size/sentiment/pure chitchat to clipper transcript
- never pad duration with excluded types
- publish_ready rules...

## When improving
- REQUIRED: learning-loop.md; no promote without confirmed + regression
```

（正文步骤与 references 链接在 writing-skills / 实现计划中落盘；description 保持触发条件 only。）

---

## 10. 错误处理与质量标记（汇总）

| 标记 | 含义 |
|------|------|
| `host_filter=degraded` | 无法区分主播，用全稿 |
| `asr_low_quality` | 转写质差，降权/警告 |
| `short_content` | 素材不够 60s |
| `missing_price` | 无价格 claim |
| `fail_golden20` | 黄金开头校验失败 |
| `publish_ready` | 可直发门槛 |
| `need_review` | 需人工 |
| `tool_gap` | clipper/规范不一致或工具失败 |

---

## 11. 与总规格映射

| 本 Skill | 总规格 clipper design |
|----------|----------------------|
| 文本主播启发式 | 补充路径；总规格 §3.2 声纹仍为完整产品 P0 能力 |
| 8 类成分 + 硬排除 size/情怀 | §3.5；本 skill 将 size/情怀从「可进信任」改为成片硬排除 |
| 黄金 20s + 60s | §3.8 / §4.2 |
| 调用本地 clipper 成片 | 对齐现有 MVP README；剪映为后续 |
| 学习闭环 | 总规格评测 §3.9 的 skill 化运营机制 |

**已知差异（有意）：** 用户确认尺码与情怀 **硬排除**；总规格曾将 size 列入信任段。以本 skill 规格为准驱动 Agent 行为；clipper 代码侧 size 打分通过输入过滤屏蔽，代码删除 size 信任权重可单独立项。

---

## 12. 已确认决策记录

1. 形态：Agent Skill 文档，非先改一坨新服务  
2. 主播识别：文本启发式  
3. 尺码 + 情怀：硬排除  
4. 产出：plan + `final.mp4`  
5. 输入：仅视频 → 必须先外置 ASR  
6. 执行：编排现有 clipper + 外置 ASR  
7. 可学习：评测 + 人工反馈 + 案例库组合  
8. 路线：A（编排）+ 学习闭环  
9. 设计 §1–§6 对话确认 OK  

---

## 13. 下一步

1. 用户审阅本规格，提出修改  
2. 批准后用 **writing-plans** 写实现计划：落盘 skill 文件、金标样例、与 clipper 的对接检查清单  
3. 实现期按 writing-skills：先基线失败场景再写 SKILL 正文（TDD for skills）  

---

## 14. 规格自检记录

| 检查项 | 结果 |
|--------|------|
| 占位符 / TBD | 无阻断性 TBD；金标数值为初值可校准 |
| 内部一致性 | 60s/20s、硬排除、clipper 边界、学习晋升纪律一致 |
| 范围 | 单 skill 规格；不包含剪映草稿与声纹实现 |
| 歧义 | 毫秒时间戳；mixed 情怀句 KEEP；missing_price 可 ready；description 不写工作流摘要 |
| 与现有代码 | 明确用输入过滤克服 size 计分；不假装 clipper 已改 |

（自检通过，待用户审阅本文件。）
