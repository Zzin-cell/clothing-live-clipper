# 架构说明

## 1. 总览

```
┌────────────┐     ┌──────────────┐     ┌─────────────┐
│  小面 Web  │────▶│  job_worker  │────▶│  final.mp4  │
│ static UI  │     │  后台线程    │     │ plan/review │
└────────────┘     └──────┬───────┘     └─────────────┘
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
     extract_wav      asr_local      filter + rank
     (ffmpeg)      (faster-whisper)   (规则+学习)
                          │
                          ▼
                     render_plan
                      (ffmpeg)
```

## 2. 主链路（自动）

1. **上传视频** → `output/web_jobs/{job_id}/uploads/`  
2. **抽音频** → 16k mono wav（可选降噪）  
3. **ASR** → `transcript_asr.json`（本地 faster-whisper medium）  
4. **LLM 逻辑排片（优先）** → `llm_plan.json` + `plan.json`  
   - 输入：ASR 口播句 + 学习偏好提示  
   - 输出：按逻辑顺序的 keep 时间轴（role=story）  
   - 然后 **反剪渲染** `final.mp4`  
5. **失败回退规则路径**：  
   - 过滤 → `transcript_for_clipper.json`  
   - `build_timeline_plan` 逻辑排序 → `plan.json`  
   - 渲染 `final.mp4`  
6. **摘要** → `review.md` / `learning_debug.json`  

开关：`CLIPPER_LLM_PLAN=true` 且配置 `CLIPPER_LLM_API_KEY` 时走 LLM；否则规则。

## 3. 反剪链路（人工）

1. 前端编辑 `planEdit`（时间/删小段/删整段/替换/重排）  
2. `PUT /api/jobs/{id}/plan`  
   - 写入 `plan.json` / `plan_edited.json`  
   - 可选 `learn=true` 写入学习偏好  
3. `start_render_plan_async`  
4. `render_from_plan_only` 严格按 plan 时间窗裁切  
5. 生成 `render_segments.json` + 新 `final.mp4`

## 4. 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| Web API | `src/clipper/web.py` | 上传、任务、反剪、学习接口 |
| Worker | `src/clipper/job_worker.py` | 异步流水线 / 重渲 |
| ASR | `scripts/agent_clip_video.py` | faster-whisper（CUDA/CPU） |
| 增强 | `scripts/asr_enhance.py` | 服装域纠错与句合并 |
| 过滤 | `scripts/filter_transcript_v2.py` | 衣服相关保留、去噪 |
| 标签 | `src/clipper/extract.py` | claim 类型词表 |
| 排序 | `src/clipper/rank.py` | 黄金20s / 后移换装 / 学习加权 |
| 媒体 | `src/clipper/media.py` | cut/concat/speed |
| 学习 | `src/clipper/learning.py` | 人机偏好记忆 |
| 前端 | `src/clipper/static/*` | 小面 UI |

## 5. 数据产物

```
output/web_jobs/{job_id}/
  uploads/video.*
  asr_work/audio_16k.wav
  transcript_asr.json
  transcript_for_clipper.json
  plan.json
  plan_edited.json
  render_segments.json
  final.mp4
  review.md
  job_meta.json
  cases/                 # 学习反馈快照
```

学习全局库：

```
output/learning/
  preferences.json
  events.jsonl
```

## 6. 关键设计决策

1. **时间驱动成片**：渲染只认 `t0_ms/t1_ms`，口播文字是标注与学习信号。  
2. **规则 + 学习**，暂未强制 LLM（可插拔）。  
3. **GPU 优先 small 模型**，失败回退 CPU。  
4. **学习默认关闭**，由用户勾选后才写入，避免脏样本。  

## 7. 扩展点

- LLM 主卖点理解（替换/增强 rank）  
- 字级时间戳（更准的“删选中文字段”）  
- TTS 改词重配音  
- 多产品分段  
