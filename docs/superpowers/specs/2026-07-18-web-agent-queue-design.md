# Web 提交台 + Agent Skill 队列后端设计

| 项 | 内容 |
|----|------|
| 版本 | v1.0 |
| 日期 | 2026-07-18 |
| 状态 | 已确认 |
| 核心 | Web 只提交/展示；Agent + clothing-live-clip skill 处理 |

## 分工

- Web：上传视频、queued、轮询、预览下载
- Agent：对话「处理队列」→ 领任务 → skill 智能口播打轴+切片 → 写回
- 交接面：`output/web_jobs/{job_id}/`

## 状态

queued → claimed → success | success_partial | failed

## API

- POST /api/jobs：视频入队（默认不跑 Whisper）
- GET /api/agent/next：领 queued
- POST /api/agent/jobs/{id}/complete|fail
- GET /api/jobs*：展示

## Skill

触发：处理队列 / process queue
