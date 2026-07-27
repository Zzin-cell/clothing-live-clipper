# Design: SiliconFlow 轻量 LLM（提速 + 连通修复）

**Date:** 2026-07-27  
**Status:** Approved for implementation planning  
**Priority goal:** 降低 LLM 规划延迟（速度优先）  
**Provider focus:** SiliconFlow 云端轻量模型（OpenAI 兼容）

---

## 1. Context

### 1.1 Problem

1. **连通失败**：UI 探测 SiliconFlow 返回 `HTTP 401` / `Token is invalid`（code `30014`）。  
   `openai_compat` 在失败时会尝试多鉴权 × 多端点 × 多 payload，错误信息冗长，失败路径也不够“干净”。
2. **延迟偏高**：规划路径把超长 system 导演规则 + 最多约 420 条 ASR 小句一次性送入 LLM，`max_tokens=4096`、timeout 偏长，即使换成轻量模型也难明显加速。
3. **产品诉求**：将 LLM API 切到**云端轻量模型**，在现有 OpenAI 兼容架构上**提速**；不引入本地 Ollama。

### 1.2 Current architecture (relevant)

```
Web UI → output/user_config/llm.json (base_url / model / api_key)
      → openai_compat.chat_completions
      → llm_plan (全量小句 + 长 SYSTEM → JSON keep)
      → plan.json → render
失败 → 规则 rank 回退
```

- LLM 密钥**仅**来自用户 UI 配置，不读 env 密钥。  
- 模型名是配置字符串，协议层已支持任意 OpenAI 兼容网关（含 `https://api.siliconflow.cn/v1`）。  
- **连通性技术上已就绪**；痛点在 401 体验、默认/推荐模型、以及请求过重导致的速度与小模型稳定性。

### 1.3 Non-goals

- 本地部署 LLM（Ollama / LM Studio）  
- 修改 ASR / ffmpeg 渲染主链路  
- 双模型自动升级（轻量失败再打大模型）  
- 保证成片质量与最强云端模型持平  
- “用代码修好无效 API Key”——无效 Token 只能校验与提示用户更换  

---

## 2. Goals & success criteria

### 2.1 Goals

1. **SiliconFlow 连通路径清晰**：默认/占位指向 SiliconFlow；401 立刻可读、立刻停重试。  
2. **默认偏好轻量快模型**（如 `Qwen/Qwen2.5-7B-Instruct`），自动匹配不偏向超大慢模型。  
3. **压缩规划请求**（短 system + 候选小句上限 + 更低 max_tokens + 更紧 timeout）。  
4. **减少盲目兼容重试**（尤其 401），成功路径优先走 `last_route`。  
5. 失败仍 **规则回退**，任务不卡死。

### 2.2 Success criteria (testable)

| ID | Criterion |
|----|-----------|
| S1 | 无效 Key：probe 在约 1s 内失败；文案含 Token 无效 / 需重新复制 Key；日志不再堆多条同质 401 |
| S2 | 有效 Key + 轻量模型：probe/规划请求相对“全量重 prompt”路径更轻（输入小句数显著下降） |
| S3 | 规划 `chat_completions` 优先 last_route；401 不扫满 endpoint×auth×payload 笛卡尔积 |
| S4 | LLM 失败任务仍产出 plan（`llm_fallback` / 规则路径） |
| S5 | 硬排除不回归：尺码 / 纯控场不稳定进入最终 plan（沿用 exclusion 机械检查或等价断言） |

---

## 3. Recommended defaults (SiliconFlow)

| Field | Value |
|-------|--------|
| Base URL | `https://api.siliconflow.cn/v1` |
| Default / preferred model | `Qwen/Qwen2.5-7B-Instruct` |
| Alternatives | `THUDM/glm-4-9b-chat`, `Qwen/Qwen2.5-14B-Instruct` |
| API Key | 用户在 UI 粘贴；必须有效；trim 后无空格换行 |

用户仍可填写任意其他 OpenAI 兼容 Base URL / Model；默认与自动选择策略偏向上述轻量模型。

---

## 4. Architecture changes

```
UI (SiliconFlow 默认 + 401 友好文案)
        │
        ▼
user_config/llm.json
        │
        ▼
openai_compat.chat_completions
  · 401：快停 + 错误分类
  · 优先 last_route
  · 收紧重试矩阵
        │
        ▼
llm_plan.call_llm_for_plan
  · 轻量 SYSTEM
  · 候选小句 ≤ LIGHT_MAX_CLAUSES（默认 150）
  · max_tokens ↓、timeout ↓
  · 仍 JSON + 现有 timeline 映射
        │
        ▼
plan.json → render / 失败 → 规则 rank
```

### 4.1 Connectivity & auth (`openai_compat` + UI)

1. **401 快停**  
   - 收到 `HTTP 401`（或 body 明确 `Token is invalid` / SiliconFlow `30014`）时：  
     - 最多再试 **一种** 备选鉴权头（可选），然后**终止**整轮 endpoint/payload 扫描。  
   - 禁止在确认 Token 无效后继续穷举 payload 变体。

2. **错误分类 / 映射**  
   - 对外稳定字段建议：`error_class=auth_invalid`、`provider_hint=siliconflow`（当 base 含 siliconflow）、`message` 中文：  
     `Token 无效：请到 SiliconFlow 控制台重新复制 API Key 后保存并重试`  
   - 保留底层 detail 截断，便于调试。

3. **UI**  
   - Base URL placeholder / 说明改为 SiliconFlow 示例。  
   - Model placeholder 提示轻量模型 ID。  
   - probe 失败时优先展示映射文案，而非原始 `all_compat_attempts_failed` 长串。

4. **Key 校验（已有则加固）**  
   - trim；拒绝 URL 形态 Key；拒绝空白字符；过短 Key 报错。

### 4.2 Model preference

- 扩展 `pick_default_model`（及 SiliconFlow `/models` 结果排序）：  
  - 优先匹配：`7B` / `9B` / `turbo` / `mini` / `Instruct` 中的轻量 chat。  
  - 明确偏好列表含：`Qwen/Qwen2.5-7B-Instruct`、`THUDM/glm-4-9b-chat` 等。  
  - 避免仅因列表顺序选中超大推理模型作为默认。

### 4.3 Request slimming (`llm_plan`)

| Item | Current | Target |
|------|---------|--------|
| System prompt | 长导演规章 | **轻量版**：硬规则清单（钩子、顺序、完整句、去尺码/控场、仅用输入 id、只输出 JSON） |
| Clauses sent | ≤ ~420 | **`LIGHT_MAX_CLAUSES` 默认 150**（常量，可调） |
| text per clause | 160 chars | **100–120** |
| max_tokens | 4096 | **2048**（规划）；probe 保持极小 |
| timeout | 120s 级 | **45–60s**；超时 → 规则回退 |
| temperature | 0.2 | 不变 |
| force_json | true | 保留；不支持时仅做 **payload** 回退（401 不做） |

#### Clause trim rules (must not invent timestamps)

After `expand_lines_to_clauses`:

1. **Drop**: empty, pure particles, clear live-room control / greetings / 扣1, long size advice.  
2. **Dedupe**: repeated selling points → keep one clearest.  
3. **Prefer**: fit / fabric / slim-look / detail / experience keywords.  
4. **Cap**: ≤ 150; if too few remain, **time-cover fill** from remaining clauses (head/mid/tail), never invent `t0_ms`/`t1_ms`/`id`.  
5. **Meta**: record `clauses_raw`, `clauses_sent`, trim stats in `llm_plan.json` / `_meta`.

### 4.4 Retry policy

| Case | Behavior |
|------|----------|
| Known last_route | Prefer single path first（规划默认接近 fast 优先） |
| HTTP 401/403 auth | Stop；surfaced as auth_invalid |
| HTTP 400 format | Next payload only |
| HTTP 404 endpoint | Next endpoint；auth×payload 上限收紧 |
| Timeout / 5xx | Fail planning → rule fallback |
| Bad JSON / empty keep | Existing raise → worker fallback |

### 4.5 Files to touch

| File | Change |
|------|--------|
| `clothing-live-clipper/src/clipper/openai_compat.py` | 401 快停、错误分类、last_route/快路径、model 偏好 |
| `clothing-live-clipper/src/clipper/llm_plan.py` | 轻量 system、裁剪、max_tokens/timeout、meta |
| `clothing-live-clipper/src/clipper/user_llm.py` | 如需默认 base/model 提示或错误文案辅助 |
| `clothing-live-clipper/src/clipper/static/index.html` | placeholder / 简短说明 |
| `clothing-live-clipper/src/clipper/static/app.js` | 401/探测文案展示 |
| `clothing-live-clipper/tests/test_openai_compat.py` | 401 快停、错误映射 |
| `clothing-live-clipper/tests/test_llm_plan.py` | 裁剪与 payload 约束 |

可选文档：`docs/ARCHITECTURE.md` 补一句 SiliconFlow 轻量默认与请求裁剪（非阻塞）。

---

## 5. Data flow

```
1) UI load → 回填 llm.json；无配置则空 Key
2) 保存/探测 → PUT config → POST probe
     成功：remember_successful_route + latency
     401：映射文案，立即返回
3) job_worker LLM 阶段
     a. expand + trim → ≤150 clauses
     b. light SYSTEM + user payload
     c. chat_completions（last_route 优先；401 快停；timeout 45–60s）
     d. parse keep → llm_obj_to_timeline → plan.json
     e. write llm_plan.json (model, endpoint, clause counts)
4) On failure → meta.llm_fallback=true → build_timeline_plan → continue
```

---

## 6. Error handling matrix

| Error | User-facing | System |
|-------|-------------|--------|
| 401 Token invalid | Token 无效，请到 SiliconFlow 控制台重新复制 API Key | 停重试；规划规则回退 |
| Missing key/model | 配置未就绪 | 跳过 LLM |
| 400 response_format | （内部） | 仅换 payload |
| Timeout / 5xx | LLM 超时或服务异常，已回退规则排片 | 规则回退 |
| JSON / empty keep | （状态/日志） | 规则回退 |
| Unknown model | 检查 Model 或自动匹配 | 不重复无效 model 穷举 |

---

## 7. Testing plan

1. **Unit: 401 fast-fail** — mock 401 → 调用次数有上界，远小于全矩阵。  
2. **Unit: error mapping** — body 含 `Token is invalid` / `30014` → `auth_invalid` + 稳定文案。  
3. **Unit: clause trim** — 300+ 句 → sent ≤ 150；id/时间不被改写；控场/尺码优先丢。  
4. **Unit: payload** — max_tokens 下调；system 明显短于旧版（或 feature 常量可断言长度上界）。  
5. **Fallback** — LLM raise 时 job 仍有 plan。  
6. **Manual**  
   - 无效 Key：探测文案与耗时  
   - 有效 Key + `Qwen/Qwen2.5-7B-Instruct`：probe 延迟  
   - 一条短直播样片端到端（LLM 成功或干净回退）

---

## 8. Implementation order

1. `openai_compat`：401 快停 + 错误分类  
2. `llm_plan`：轻量 prompt + 裁剪 + max_tokens/timeout  
3. 默认模型偏好 + UI 文案  
4. 单测  
5. 有效 SiliconFlow Key 手测  

---

## 9. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| 裁句漏卖点 | 关键词保留 + 时间覆盖补齐；可调 `LIGHT_MAX_CLAUSES` |
| 7B JSON 不稳 | 规则回退；不以 100% LLM 成功为门禁 |
| 用户 Key 仍无效 | 产品无法代修复；只保证失败干净、提示明确 |
| 其他网关受 401 快停影响 | 401 语义全局正确；非 401 仍保留有限兼容回退 |

---

## 10. Decisions locked

- Approach: **云端轻量（SiliconFlow）+ 请求瘦身 + 减少盲目重试**（方案 C）  
- Speed over maximal plan quality  
- No local LLM in this change  
- No dual-model upgrade ladder in this change  
- Key remains user-UI-only storage  

---

## 11. Approval

Design sections reviewed and approved by user in brainstorming session (2026-07-27) before this document was written.
