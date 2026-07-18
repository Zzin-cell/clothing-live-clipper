# 服装切片 Web 工作台设计规格（视频优先）

| 项 | 内容 |
|----|------|
| 版本 | v1.0 |
| 日期 | 2026-07-18 |
| 状态 | 对话已确认 §1–§4，待用户审阅本文件 |
| 路线 | 方案 A：增强现有 FastAPI + static Web |
| 代码位置 | `clothing-live-clipper/src/clipper/web.py`, `static/*` |
| 关联 | clipper MVP pipeline；skill 规格（并行，不阻塞 Web） |

---

## 1. 产品定位

### 1.1 是什么

本地 Web 工作台：用户以**直播视频**为主入口提交任务，系统处理后在同一页面**查看**时间轴与成片预览，产物**自动落盘**并可反复打开、**下载保存**到本机。

### 1.2 已确认决策

| 项 | 选择 |
|----|------|
| 相对现网缺口 | 视频为主 + 体验打磨 |
| 无转写 | 允许只交视频；ASR 可配置、**下一期**实现；首版补传转写闭环 |
| 保存含义 | 任务产物自动落盘 + 在线预览/下载 |
| 实现路线 | A：演进现有 FastAPI Web，不新开前端工程 |

### 1.3 非目标（本版）

- 公网多租户 / 登录账号
- 完整 ASR 厂商对接（仅配置与状态预留）
- 剪映草稿
- 片库收藏/重命名/批量清理（二期）
- 强制异步任务队列（超大文件风险文档说明；二期可线程池）

### 1.4 成功标准

- 只传视频可创建任务且文件不丢（`needs_transcript`）
- 视频 + 转写 + ffmpeg → 页内可播 `final.mp4` 并可下载
- 历史任务可再次打开同一结果
- 下载 `final.mp4` / `plan.json` / `review.md` 可用
- UI 文案体现视频优先，而非「必须先有转写」

---

## 2. 信息架构

### 2.1 单页三区

| 区域 | 职责 |
|------|------|
| 新建 | 视频（主）、转写（可选）、目标秒数、是否渲染、提交 |
| 详情/结果 | 状态、徽章、预览、黄金/信任/CTA、review、下载、补传转写行动区 |
| 历史 | 倒序任务列表；点击加载详情 |

### 2.2 主路径

```
上传视频（±转写）
  → 有转写/示例：processing → success | success_partial | failed
  → 无转写且 ASR 未就绪：needs_transcript（保留视频）→ 补传转写 → processing → …
  → 无转写且 ASR 已实现（二期）：transcribing → processing → …
```

---

## 3. 任务状态机

| status | 含义 |
|--------|------|
| `queued` | 预留：已接收未开跑 |
| `needs_transcript` | 有视频、无转写、ASR 不可用或未实现 |
| `transcribing` | ASR 中（二期） |
| `processing` | 切片/渲染中 |
| `success` | 完成（有 plan；有成片更佳） |
| `success_partial` | 有 plan，渲染跳过/失败 |
| `failed` | 失败 |

本版主路径：`needs_transcript` →（补传）→ `processing` → 终态。

---

## 4. 保存目录（自动落盘）

```
output/web_jobs/{job_id}/
  job_meta.json
  uploads/video.*
  uploads/transcript.json|srt
  plan.json
  review.md
  clips.json
  claims.json
  transcript.json
  final.mp4          # 若渲染成功
  result.json
```

- 历史默认不自动删除  
- 预览与下载均读此目录  

---

## 5. API

### 5.1 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | `ok`, `ffmpeg`, `sample_transcript`, `asr_configured`, `time` |
| GET | `/api/jobs` | 列表；含 status / has_video / has_final 等 |
| GET | `/api/jobs/{id}` | 详情 + plan + review_md + files 标志 |
| GET | `/api/jobs/{id}/files/{name}` | 白名单文件；mp4 可预览/下载 |
| POST | `/api/jobs` | 创建任务（视频为主） |
| POST | `/api/jobs/{id}/transcript` | **新增** 补传转写并处理 |

### 5.2 POST `/api/jobs` 表单

| 字段 | 必填 | 说明 |
|------|------|------|
| `video` | 推荐 | 主入口；容器 mp4/mov/mkv/webm/avi |
| `transcript` | 否 | json/srt |
| `use_sample` | 否 | 示例转写演示 |
| `target_seconds` | 否 | 默认 60，范围 15–180 |
| `render` | 否 | 默认 true |

**校验：**

- 无 video 且无 transcript 且非 use_sample → 400  
- 仅 video、无转写、ASR 未就绪 → **200** + `needs_transcript`（不是错误）  
- 有 transcript 或 use_sample → 同步 `run_pipeline`  

兼容：允许无视频仅转写出 plan（UI 弱化，提示无法预览成片）。

### 5.3 POST `/api/jobs/{id}/transcript`

- 仅 `status == needs_transcript`  
- 保存转写到 `uploads/`  
- 使用已存视频跑 pipeline  
- 更新 meta 与产物，返回与详情一致的 payload  

### 5.4 `job_meta.json` 关键字段

```
job_id, status, created_at, finished_at?
video_source, transcript_source
target_seconds, render_requested
has_video, has_final, output_mp4
render_skipped, render_error?
golden20_passed, duration_s, selected_clips, warnings[]
error?
asr_configured, asr_note?
```

### 5.5 文件白名单

保持并包含：`plan.json`, `review.md`, `clips.json`, `claims.json`, `transcript.json`, `result.json`, `final.mp4`, `job_meta.json`。

---

## 6. UI / 体验

### 6.1 文案

- 副标题：上传**直播视频**，重排黄金 20 秒 → 约 60 秒；转写可选/可后补  
- 视频字段：主入口  
- 转写字段：可选  
- 主按钮：处理中 loading；无转写提交时语义为保存视频并进入待补转写  

### 6.2 结果区

- 状态色：success / success_partial / needs_transcript / failed  
- `needs_transcript`：补传转写控件 +「继续处理」  
- success：`<video>` 预览 + 下载 final/plan/review  
- success_partial：展示未渲染原因 + 时间轴  
- 保留黄金 / 信任 / CTA + review  

### 6.3 历史列表

- job_id、状态徽章、有视频/有成片、时长、黄金 20、创建时间  
- 点击加载详情  

### 6.4 Health

展示 ffmpeg、示例转写、**ASR 是否配置**。

### 6.5 视觉

沿用现有 CSS；只调字段顺序、徽章与待补转写行动区，不重做设计系统。

---

## 7. ASR 预留

### 7.1 环境变量（首版可读）

| 变量 | 默认 | 说明 |
|------|------|------|
| `CLIPPER_ASR_ENABLED` | false | 总开关 |
| `CLIPPER_ASR_PROVIDER` | none | 占位名 |
| 密钥类 | 空 | 二期 |

`asr_configured` = enabled 且 provider 非 none 且必要配置存在（实现期定义）。

### 7.2 首版硬规则

即使将来 `asr_configured=true`，若 provider **未实现完整调用**：

- fallback `needs_transcript`
- meta：`asr_note: not_implemented`  
- **禁止**假装 `transcribing` 进度  

二期实现 provider 后再进入 `transcribing`。

---

## 8. 处理模型与风险

- 有转写：HTTP 请求内**同步** `run_pipeline`（与现网一致）  
- 仅视频：快速返回，不长阻塞  
- 大文件同步超时风险：README 写明建议时长与本机资源；二期异步  
- 本机绑定：`127.0.0.1` 默认，数据不出本机目录  

### 8.1 启动

```bat
cd clothing-live-clipper
set PYTHONPATH=src
uvicorn clipper.web:app --host 127.0.0.1 --port 8787
```

打开 `http://127.0.0.1:8787/`

---

## 9. 与 pipeline / skill 边界

| Web | pipeline | skill |
|-----|----------|--------|
| 上传、任务、预览下载 | 打分重排渲染 | Agent 编排/硬排除/学习 |
| 调 `run_pipeline` | 不变则可先接 Web | 不强制 Web 加载 skill |
| 日后可共享过滤模块 | | 过滤规则可回灌代码 |

本版 Web **直接**调现有 pipeline；skill 硬排除不阻塞 Web 上线。

---

## 10. 测试要点

| 场景 | 期望 |
|------|------|
| 仅视频创建 | 200，`needs_transcript`，uploads 有视频 |
| 补传转写 | 进入处理并产出 plan；有 ffmpeg+render 则 final |
| 视频+转写+render | success 或 success_partial；详情可预览或说明原因 |
| 列表示例 | 新任务出现在历史，点击可回看 |
| 下载 | files API 返回 final/plan/review |
| 非法空提交 | 400 |
| health | 含 asr_configured |

实现期：pytest 覆盖 API（临时目录/fixture）；UI 手工点验预览与下载。

---

## 11. 实现文件清单（预期）

| 文件 | 变更 |
|------|------|
| `src/clipper/web.py` | 状态机、创建校验、补传 API、meta 字段、health |
| `src/clipper/static/index.html` | 视频优先表单与结果/补传区 |
| `src/clipper/static/app.js` | 提交逻辑、补传、列表字段、状态展示 |
| `src/clipper/static/styles.css` | 徽章/行动区少量样式 |
| `tests/test_web_*.py` | API 测试（新建） |
| `README.md` | Web 启动与路径说明 |

---

## 12. 已确认决策记录

1. 方案 A：增强现有 Web  
2. 视频为主 + 体验打磨  
3. 无转写可提交；ASR 下期；首版补传闭环  
4. 保存 = 自动落盘 + 预览/下载  
5. 设计 §1–§4 对话确认 OK  

---

## 13. 下一步

1. 用户审阅本规格  
2. 批准后 **writing-plans** 写实现计划  
3. 按计划改 web/static/tests 并提交推送  

---

## 14. 规格自检

| 检查项 | 结果 |
|--------|------|
| 占位符 | 无阻断 TBD；ASR 明确二期 |
| 一致性 | 状态机、API、目录、UI 一致 |
| 范围 | 单工作台增量，不含新前端/真 ASR |
| 歧义 | 仅视频 200+needs_transcript；同步处理；fallback 禁止假转写中 |

（自检通过，待用户审阅。）
