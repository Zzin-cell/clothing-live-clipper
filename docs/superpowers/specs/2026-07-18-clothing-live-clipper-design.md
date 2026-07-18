# 服装带货直播间智能切片系统设计规格

| 项 | 内容 |
|----|------|
| 版本 | v1.0 |
| 日期 | 2026-07-18 |
| 状态 | 已评审（对话确认 §1–§5） |
| 来源 | `视频剪辑skill/deepseek_markdown_20260718_1530a4.md` + 需求澄清 |
| 目标形态 | 本地编排 + 云端认知 API；成片 MP4 + 剪映专业版草稿；剪映侧精修的插件式工作流 |

---

## 1. 产品定位与分期

### 1.1 产品是什么

面向**服装带货直播回放**的本地工作流工具：输入整场直播视频与主播声纹样本，自动完成「主播讲解过滤 → 转写 → 单品切段 → 卖点成分提取 → 重要性评分 →（后续）约 60 秒高转化切片成片 + 剪映专业版草稿」。

核心体验承诺：生成切片的**前 20 秒即为最强转化信息**（黄金开头），整片默认约 **60 秒**，结构完整可发，并可在剪映中精修。

### 1.2 明确非目标（YAGNI）

- 不做实时直播流切片（仅回放文件）
- 不做复杂多机位 / 多主播混剪策略
- 不做电商后台或挂车 API 对接
- 不做云端 SaaS 多租户；默认本机编排 + 云端认知 API
- 不强依赖剪映官方插件 SDK（草稿导入式松耦合）

### 1.3 分期策略：理解质量优先（路线 C）

| 阶段 | 目标 | 可验收产出 |
|------|------|------------|
| **P0 理解中台** | 音频/文本理解准确、可评测 | 主播时间轴、带时间戳口播稿、单品区间、卖点片段图谱、评分与 `timeline_plan`、评测集与指标报告 |
| **P1 成片与剪映** | 能发出去、能精修 | 约 60s 成片（黄金 20s 结构）+ 字幕叠字 + 剪映专业版草稿 + 最小本地预览入口 |
| **P2 视觉与体验** | 更强转化与更好用 | 画面辅助识别加权、转场/合规增强、Web 端人工微调时间轴后重导出 |

**原则：** 金标评测达标前，不把「自动成片可直发」作为主承诺；P0 即产出剪辑预案 JSON，供抽查理解是否撑得起 60s 成片。

### 1.4 总成功标准

- **理解：** 主播过滤、产品切段、卖点标签在评测集上达到约定指标后，再放大自动成片。
- **成片：** 单品目标 60s；前 20s 为最高转化信息，并通过黄金 20s 合规校验。
- **工作流：** `final.mp4`（可直发或近似直发）+ 剪映专业版可打开草稿。

---

## 2. 系统架构与数据流

### 2.1 总体形态

- **本地编排（Python）：** 任务管理、媒资处理、时间轴、成片、草稿导出。
- **云端认知 API：** 说话人分离/声纹、ASR、LLM 成分提取与评分解释；（P2）多模态画面理解。
- **本地媒资：** ffmpeg / ffprobe。
- **交互：** P0 CLI；P1 CLI + 最小本地 Web；P2 增强审阅与重排。剪映负责精修与最终发版导出。

```
[直播视频] + [主播声纹样本] + [任务配置]
                │
                ▼
           Ingest 媒资
                │
                ▼
        AudioUnderstand（声纹过滤 → ASR → 主播口播时间轴）
                │
                ▼
        ProductSegment（单品讲解区间）
                │
                ▼
        ClaimExtract（8 类成分 + 时间戳）
                │
                ▼
        Score & Rank（权重 + timeline_plan + 黄金20s 计划校验）
                │
         P0：UnderstandingPackage JSON + 评测报告
                │
         P1：RenderMP4 + JianyingDraft
                │
         P2：VisionBoost + 审阅式入口增强
```

### 2.2 核心数据契约：`UnderstandingPackage`

阶段间**唯一真相**。成片与草稿只消费该结构化结果，禁止渲染阶段再次“即兴理解”。

顶层字段：

| 字段 | 说明 |
|------|------|
| `job_id` | 任务 ID |
| `source_video` | 源视频路径 |
| `duration_ms` | 总时长（毫秒） |
| `anchor` | 声纹样本信息与匹配阈值 |
| `host_segments[]` | 主播有效讲解区间 |
| `transcript[]` | 句级（可选词级）文本 + `t0`/`t1` |
| `products[]` | 单品列表（见下） |
| `quality` | 步骤置信度、告警、错误、是否降级 |

每个 `product`：

| 字段 | 说明 |
|------|------|
| `product_id` | 单品 ID |
| `t0`, `t1` | 讲解区间 |
| `title_guess` | 标题/款名猜测 |
| `boundary_evidence[]` | 切分依据 |
| `claims[]` | 成分片段 |
| `clips[]` | 基础切片单元 + 分项分与最终权重 |
| `timeline_plan` | 黄金开头 / 信任建设 / 促单收尾入选序列 |
| `flags` | 如 `too_short`, `segment_uncertain`, `missing_price`, `publish_ready` |

**时间基准：** 全程毫秒；渲染时再对齐帧。  
**幂等：** 相同输入 + 配置可复跑；中间产物缓存，降低 API 费用。

### 2.3 Provider 抽象（可插拔，默认云端）

| 接口 | 职责 |
|------|------|
| `DiarizationSpeakerID` | 说话人分离 + 注册声纹比对 → host / other |
| `ASRProvider` | 中文转写 + 时间戳 |
| `LLMExtractor` | 成分抽取、切段辅助、评分解释 |
| `VisionProvider` | P2 镜头类型 / 特写检测 |
| `Renderer` | ffmpeg 成片 |
| `DraftExporter` | 剪映草稿 |

失败降级：LLM 不可用时用**服装领域规则词典**做 claim 抽取，并标记 `degraded=true`。厂商与模型名写入配置/附录，不写死在核心逻辑。

### 2.4 工程目录草图

```
clothing-live-clipper/
  docs/superpowers/specs/
  docs/superpowers/plans/
  src/
    ingest/
    audio/
    nlp/
    ranking/
    render/          # P1
    jianying/        # P1
    vision/          # P2
    app/             # CLI + 本地 Web
  tests/fixtures/
  configs/
```

---

## 3. P0 理解中台业务规则

### 3.1 输入要求

| 输入 | 要求 |
|------|------|
| 直播回放 | 常见容器（mp4/mov 等），含清晰人声 |
| 主播声纹 | ≥30s 清晰单人语音；可多段；可注册多条样本 |
| 任务配置 | `lang=zh`；目标成片时长默认 60s（P0 只生成 plan）；品类词典包（女装/男装/通用） |

### 3.2 主播声纹识别与过滤

1. 抽音频并标准化（建议 16k mono PCM）。
2. 云端说话人分离 → `speaker_id + [t0, t1]`。
3. 各 speaker 与注册声纹比对得相似度。
4. 标记 `host` / `co_host_or_other`（默认阈值建议 ≥0.75，以供应商校准为准，可配置）。
5. 仅保留 host；&lt;300ms 碎片合并或丢弃；相邻 host 间隔 &lt;500ms 可合并。
6. VAD 去除长静音；音乐床且无人声段不进入 transcript 主链路。

**输出：** `host_segments[]`、`rejected_ratio`。  
**告警：** host 覆盖过低（如有效人声 &lt;40%）→ `low_host_coverage`（警告，不一律失败）。

### 3.3 ASR 转写

- 中文；在自建服装口播评测集上目标字错率对应准确率 ≥95%（以评测集度量，不作无依据空口保证）。
- **至少句级时间戳**；有词级则保留。
- 优先仅 host 区间转写（或全量转写再 mask，取成本更优方案）。

**输出 `transcript[]`：** `{ utt_id, text, t0, t1, words?, confidence? }`  

**质检：** 置信度低或乱码比例高 → `asr_low_quality`，后续抽取降权并告警。

### 3.4 产品讲解区间划分

**信号（规则 + LLM）：**

- 标志话术词表：如「下一个」「看这件」「来这件」「换一件」「上链接」等（可配置）。
- 长停顿：约 ≥1.5–2.0s 且前后话题漂移。
- LLM 边界标注：边界句 index + 理由。
- 可选：颜色/品类突变词。

**规则：**

- 区间不重叠，覆盖有效 host 讲解为主。
- 单品有效口播 &lt;20s → `too_short`（P1 默认可跳过成片）。
- 无法可靠切分 → 整场一个 product + `segment_uncertain=true`。

### 3.5 服装核心成分（8 类 claims）

| type | 含义 | 示例 |
|------|------|------|
| `fit` | 版型 | 收腰、A 字、oversize、修身 |
| `fabric` | 布料/材质 | 醋酸、凉感、羊毛、雪纺 |
| `selling_point` | 核心卖点/痛点 | 显瘦 10 斤、梨形友好、遮肉 |
| `detail` | 设计细节 | 袖型、开叉、口袋、抽绳 |
| `scene` | 穿着场景 | 通勤、约会、度假、孕妇可穿 |
| `price` | 价格/优惠 | 原价、券后价、买赠 |
| `size` | 尺码建议 | 偏大偏小、选码方法 |
| `outfit` | 搭配建议 | 配牛仔裤、小白鞋 |

**每条 claim 必须：**

- 可映射回 transcript 的精确 `t0`–`t1`（禁止无时间悬空文案）。
- 经 JSON Schema 校验；一句可多标签。
- 过滤寒暄、无信息重复、纯控场话术。

**降级：** LLM 失败 → 规则词典抽取 + `degraded=true`。

### 3.6 基础切片单元（clips）

- 来源：含有效 claim 的完整语句；过碎则可合并连续 1–3 句。
- 时长：**0.5s–15s**，句意完整，禁止词中切开。
- 字段：`clip_id, t0, t1, text, claim_types[], source`（`speech` | `vision`），附 `score_breakdown` 与 `weight`。

### 3.7 重要性评分模型

对每个 clip 计分后在单品内归一化为 `weight`。

| 分项 | 分值 | 说明 |
|------|------|------|
| A. 核心卖点分 | 0–40 | 痛点/效果/人群匹配最高；空泛形容词低 |
| B. 结构价值分 | 0–20 | fit/fabric/selling_point；**组合加分**：卖点+(版型\|布料) 额外 +10（计入 B 的上限策略在实现中配置，需在 breakdown 中单列 `combo_bonus`） |
| C. 促单分 | 0–15 | 价格锚点、折扣、库存紧迫、行动指令 |
| D. 具体性 | 0–15 | 具体材质名/数据/场景；拒绝万能水词 |
| E. 置信与可用性 | 0–10 | ASR 置信、host、时长合法 |
| F. 视觉冲击 | 0–15 | **P0/P1 默认 0**；P2 启用 |

**惩罚：**

- 寒暄/无关互动：剔除或 ×0。
- 与已入选 clip 高度重复：−30%～−70%。
- 纯负面无转机：不得进入黄金开头。

**最终：** `raw = A+B+C+D+E+F` 经惩罚后，单品内 min-max 或 softmax 得 `weight`；保留完整 `score_breakdown`。

### 3.8 `timeline_plan`（P0 即产出，不渲染）

目标总长 **60s**（允许计划层 55–65s）；素材不足则缩短并 `short_content=true`。

1. **黄金开头 0–20s**  
   - 权重最高的 1–3 个 clip 填满约 20s。  
   - 优先覆盖 `selling_point + (fit|fabric)`；强价格炸点可竞争开头。  
   - **禁止**寒暄、泛泛而谈、无关互动、冗长铺垫。  
   - **校验：** 开头 clip 权重和 ≥ 本片全部入选 clip 总权重的 **60%**；成分完整度不达标则重排；仍失败 → `fail_golden20` / 供人工 review。

2. **信任建设 20s–50s**  
   - 细节、材质、尺码、场景、搭配；逻辑序建议：版型/布料展开 → 细节 → 尺码 → 场景/搭配。  
   - 去重，保持最小叙事连贯（允许 plan 中标注 `transition: soft_cut`）。

3. **促单收尾 50–60s（最后约 10s）**  
   - 价格/优惠/行动指令；若无 `price` claim → 次高卖点复述 + `missing_price`。

### 3.9 P0 评测与出门标准

**金标集：** 建议先 5–10 段 3–10 分钟级脱敏切片（真实服装口播）。

| 指标 | 说明 |
|------|------|
| 主播区间 | IoU / 纯度 |
| ASR | 领域字错率 |
| 产品边界 | F1（时间容差 ±2s） |
| Claim | 关键类（fit/fabric/selling_point/price）精确率/召回 |
| 黄金 20s plan | 是否含核心组合、寒暄违规率、权重占比是否 ≥60% |

**初始目标（实现前用 1–2 条样本校准后可修订）：**

- 关键 claim 精确率 ≥ 0.80  
- 产品边界 F1 ≥ 0.75（±2s）  
- 黄金 20s plan 人工抽检「听前 20s 能懂买点」≥ 0.80  

达标后方将 P1「可直发」作为默认承诺。

---

## 4. P1 成片与剪映草稿

### 4.1 输入

- P0 `UnderstandingPackage`（含 `timeline_plan`；若人工改过 JSON，以最新为准）
- 源视频、字幕样式、导出目录

### 4.2 成片结构（默认 60s）

| 段 | 时间 | 规则 |
|----|------|------|
| 黄金开头 | 0–20s | 最高权 1–3 段；直击痛点；优先卖点+(版型\|布料) 或强价格；禁寒暄 |
| 信任建设 | 20–50s | 细节→材质→尺码→场景/搭配；去重 |
| 促单收尾 | 50–60s | 价格/优惠/CTA；无价格则警告 |

- 总时长容差：**55–65s**。  
- 开头长度目标约 18–22s；总长极短时按比例收缩，但不把寒暄填进开头。

### 4.3 渲染流程

1. 渲染前再次执行**黄金 20s 合规校验**（与 P0 相同规则 + 成片入选集合）。  
2. 按 ASR 句边界裁切；可向两侧微扩 50–120ms 防咬字；优先关键帧对齐。  
3. 接缝：默认硬切，可选 0.08–0.15s 交叉淡化。  
4. 字幕烧录（成片）+ 草稿内可编辑字幕轨；关键词（面料名、显瘦、券后价）可高亮。  
5. 音频：简单响度归一；默认无 BGM（可配置垫乐，不得压过人声）。  
6. 输出 `final.mp4` + `sidecar.json`（clip 列表与校验结果）+ `review.md`。

### 4.4 剪映专业版草稿

**目标：** 剪映打开可见时间线、字幕、素材引用，可精修再导出。

**最低集：**

- 视频轨：按成片顺序的片段引用或等价时间线  
- 字幕轨：可编辑文本  
- 可选文本贴纸：卖点词  
- 媒体路径策略与「勿移动素材」说明  
- `draft_version` 可配置；格式漂移时回退「仅成片」

**输出目录：**

```
output/{job_id}/{product_id}/
  final.mp4
  timeline_plan.json
  sidecar.json
  review.md
  jianying_draft/
```

### 4.5 `publish_ready=true` 门槛

同时满足：

- 黄金 20s 校验通过  
- 总时长在容差内  
- 无致命级 `asr_low_quality`（除非人工 override）  
- 至少 1 条 `selling_point`  
- `missing_price` 默认仅警告仍可直发（配置可改为强制）

### 4.6 P1 验收

- 金标成片时长与开头听检  
- 剪映打开草稿、改字幕、导出正常  
- 对比「原序 60s」vs「重排 60s」的卖点前置率  

---

## 5. P2、入口形态、可靠性

### 5.1 VisionBoost（P2）

- 在 claim 触发点抽帧/短窗送多模态 API。  
- 标签示例：`closeup_fabric`、`try_on`、`stretch_test`、`before_after`、`label_tag`、`full_body`。  
- 命中则写入评分项 F（0–15）；可提升信任段入选率。  
- **黄金开头仍以文本高转化主约束为准**，视觉为加分项。  
- API 失败 → F=0，行为回退 P1。

### 5.2 本地入口（剪映/本地工具插件式）

| 阶段 | 入口 |
|------|------|
| P0 | CLI：`understand` 产出 JSON/报告 |
| P1 | CLI 导出成片+草稿；**最小本地 Web**（选视频、看 products/claims/timeline、触发导出） |
| P2 | Web 支持通过/剔除片段、轻量重排后重导出；一键打开输出目录以便进入剪映草稿箱 |

分工：**理解与重排在本工具，精修与发布在剪映**。

### 5.3 任务状态与错误

- 状态：`queued` → `processing` → `need_review` | `success` | `success_partial` | `failed`  
- 步骤错误分类：可重试（限流/超时）vs 不可恢复（无音轨/文件损坏）  
- 云端：指数退避 + 结果缓存  
- 部分单品失败不阻塞其他单品成功产物

### 5.4 安全与隐私

- API Key：环境变量或本地 `.env`，禁止入库  
- 默认不上传完整视频做多模态，除非用户显式开启 Vision  
- 日志脱敏；评测素材脱敏  

### 5.5 技术栈约定

- Python 3.11+  
- ffmpeg / ffprobe  
- 云端 ASR + 声纹/分离 + LLM（配置化）  
- P1 Web：FastAPI + 轻量前端即可  
- 测试：pytest + fixtures 金标 JSON  

---

## 6. 原需求映射

| 原规范书 | 本设计 |
|----------|--------|
| 3.1 声纹识别与过滤 | §3.2；P0 |
| 3.2 ASR 与产品分段 | §3.3–3.4；P0 |
| 3.3 八类成分提取 | §3.5；P0 |
| 3.4 画面辅助 | §5.1；P2（评分项 F） |
| 3.5 切片与评分 | §3.6–3.7（补全原缺失分项） |
| 4.1–4.2 重组与黄金 20s | §3.8 计划 + §4 渲染 |
| 5 工作流全景（原文截断） | §2 架构 + 本分期工作流 |
| 成片 + 剪映草稿（澄清新增） | §4.3–4.4 |
| 简单界面/插件式（澄清） | §5.2 |
| 云端 API 优先（澄清） | §2.3 |
| 目标 60s（澄清） | §3.8、§4.2 |
| 理解质量优先（澄清路线 C） | §1.3 |

---

## 7. UnderstandingPackage JSON Schema 草案

```json
{
  "job_id": "string",
  "source_video": "string",
  "duration_ms": 0,
  "anchor": {
    "sample_paths": ["string"],
    "similarity_threshold": 0.75
  },
  "host_segments": [
    { "t0": 0, "t1": 0, "similarity": 0.0 }
  ],
  "transcript": [
    {
      "utt_id": "string",
      "text": "string",
      "t0": 0,
      "t1": 0,
      "confidence": 0.0,
      "words": [{ "w": "string", "t0": 0, "t1": 0 }]
    }
  ],
  "products": [
    {
      "product_id": "string",
      "t0": 0,
      "t1": 0,
      "title_guess": "string",
      "boundary_evidence": ["string"],
      "claims": [
        {
          "claim_id": "string",
          "type": "fit|fabric|selling_point|detail|scene|price|size|outfit",
          "text": "string",
          "t0": 0,
          "t1": 0,
          "polarity": "positive|neutral|negative"
        }
      ],
      "clips": [
        {
          "clip_id": "string",
          "t0": 0,
          "t1": 0,
          "text": "string",
          "claim_types": ["selling_point"],
          "source": "speech",
          "score_breakdown": {
            "A_selling": 0,
            "B_structure": 0,
            "combo_bonus": 0,
            "C_conversion": 0,
            "D_specificity": 0,
            "E_confidence": 0,
            "F_vision": 0,
            "penalty": 0,
            "raw": 0
          },
          "weight": 0.0
        }
      ],
      "timeline_plan": {
        "target_duration_s": 60,
        "golden": [{ "clip_id": "string", "role": "hook" }],
        "trust": [{ "clip_id": "string", "role": "detail" }],
        "cta": [{ "clip_id": "string", "role": "price_cta" }],
        "golden20_check": {
          "passed": true,
          "weight_ratio": 0.0,
          "coverage": ["selling_point", "fit"],
          "failures": []
        }
      },
      "flags": {
        "too_short": false,
        "segment_uncertain": false,
        "missing_price": false,
        "short_content": false,
        "fail_golden20": false,
        "publish_ready": false
      }
    }
  ],
  "quality": {
    "warnings": ["string"],
    "errors": ["string"],
    "degraded": false,
    "rejected_ratio": 0.0
  }
}
```

---

## 8. 评分与结构默认参数（可配置）

| 参数 | 默认 |
|------|------|
| 成片目标时长 | 60s |
| 成片时长容差 | 55–65s |
| 黄金开头 | 0–20s（约 18–22s） |
| 信任建设 | 20–50s |
| 促单收尾 | 最后 ~10s |
| 黄金权重占比阈值 | ≥60% |
| clip 时长 | 0.5–15s |
| host 相似度阈值 | 0.75（待校准） |
| host 碎片 | &lt;300ms 处理；间隔 &lt;500ms 合并 |
| 切段长停顿 | 1.5–2.0s |
| 单品过短 | 有效口播 &lt;20s |
| 裁切微扩 | 50–120ms |
| 淡化（可选） | 0.08–0.15s |
| BGM | 默认关 |

---

## 9. 风险与开放校准项

| 风险 | 缓解 |
|------|------|
| 剪映草稿格式随版本变化 | `draft_version` 配置；保底仅 MP4；文档记录适配版本 |
| 云端 API 费用与限流 | 缓存、仅 host 送转写、评测用短样本 |
| 方言/嘈杂直播间 ASR | 领域评测集；低质告警；人工 review 通路 |
| 声纹阈值不泛化 | 可配置 + 每主播校准样本 |
| 产品切分误差 | 规则+LLM；`segment_uncertain`；Web 端 P2 可改边界（若实现） |
| 原需求 3.5 评分原稿不完整 | 已由 §3.7 补全；上线前用金标再调权 |

**开放项（不阻塞规格冻结，实现期校准）：** 具体云厂商选型表、剪映目标版本号、金标阈值微调用真实样本更新。

---

## 10. 已确认决策记录

1. 先补全需求/设计文档，再写代码。  
2. 交付含**成片 + 剪映草稿**。  
3. 工作流定位：剪映/本地工具**插件式**（松耦合草稿，非官方 SDK 强绑）。  
4. 界面：简单本地入口（CLI → 最小 Web），精修在剪映。  
5. 认知能力：**优先云端 API**。  
6. 能力全集进总规格，**分阶段 P0/P1/P2 实现**。  
7. 路线：**理解质量优先（C）**。  
8. 单品成片目标时长：**60 秒**。  
9. MVP 能力清单在规格层全覆盖；实施严格按 P0→P1→P2。  

---

## 11. 下一步

1. 用户审阅本规格文件，提出修改。  
2. 批准后，使用 **writing-plans** 编写 **P0 实现计划**（理解中台 + 评测；不含成片渲染承诺）。  
3. P0 出门标准达成后，再分别计划 P1 / P2。  

---

## 12. 规格自检记录

| 检查项 | 结果 |
|--------|------|
| 占位符 / TBD | 无阻断性 TBD；厂商与金标数值列为「开放校准项」 |
| 内部一致性 | 60s 结构、黄金 20s、8 类 claim、评分 A–F、分期职责一致 |
| 范围 | 单产品规格；实现按 P0 子计划切开，避免一次做完 |
| 歧义 | 时间单位统一毫秒；publish_ready 与 missing_price 策略已写明；视觉不覆盖文本黄金约束 |

（自检通过，待用户审阅本文件。）
