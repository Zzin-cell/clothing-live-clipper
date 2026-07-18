# clothing-live-clipper（简单可用版）

服装带货直播回放 → **卖点提取 + 黄金 20 秒重排 + 约 60 秒成片** 的本地 MVP。

完整规格见：`../docs/superpowers/specs/2026-07-18-clothing-live-clipper-design.md`  
本仓库当前实现的是**可跑通的竖切面**：含 CLI 与本地 Web 工作台；不做声纹、剪映草稿、多产品切分、真实 ASR 自动听写。

## 功能

1. 读取口播转写（`.json` / `.srt`）
2. 规则识别：版型 / 布料 / 卖点 / 价格等
3. 打分排序，生成三段结构：黄金开头(20s) + 信任建设 + 促单(10s)
4. 有视频 + ffmpeg 时裁切拼接为 `final.mp4`
5. 无视频也可只出 `plan.json` / `review.md`
6. 本地 Web 工作台：视频优先上传，转写可选/可后补

## 环境

- Windows / Python 3.11+
- 可选： [ffmpeg](https://ffmpeg.org/)（要出成片时必须）

```bat
cd clothing-live-clipper
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 用法

### 1）只生成切片计划（不需要视频）

```bat
python -m clipper run --transcript tests\fixtures\sample_transcript.json --out output\demo --no-render
```

查看：

- `output\demo\plan.json` — 时间轴
- `output\demo\review.md` — 人类可读摘要
- `output\demo\clips.json` — 每句得分

### 2）生成约 60 秒成片

先准备：源视频 `your.mp4`，以及与之对齐的转写（毫秒时间戳）。

```bat
python -m clipper run --video path\to\your.mp4 --transcript path\to\talk.json --out output\job1
```

输出 `output\job1\final.mp4`。

### 转写 JSON 格式

```json
[
  {"utt_id": "u1", "text": "收腰显瘦，梨形闭眼入", "t0_ms": 12000, "t1_ms": 18000},
  {"utt_id": "u2", "text": "券后只要129", "t0_ms": 46000, "t1_ms": 52000}
]
```

也支持 `.srt`。

## Web 工作台（视频优先）

```bat
cd clothing-live-clipper
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
uvicorn clipper.web:app --host 127.0.0.1 --port 8787
```

浏览器打开 http://127.0.0.1:8787/

- 主入口：上传直播视频
- 转写可选；没有转写时任务为「待补转写」，可稍后上传 json/srt 继续
- 产物目录：`output/web_jobs/{job_id}/`（自动保存，可预览/下载 final.mp4）
- ASR 自动听写：预留配置，尚未实现

### 限制更新表

| 有 | 无 |
|----|----|
| Web 上传视频/转写、历史回看、预览下载 | 真 ASR 自动听写 |
| 补传转写继续处理 | 账号/多租户 |

## 测试

```bat
set PYTHONPATH=src
pytest -q
```

## 说明与限制（MVP）

| 有 | 无 |
|----|----|
| 规则卖点抽取 | 真 ASR 自动听写（请自备转写；`.env` 已预留 stub） |
| 黄金 20s 重排 | 主播声纹过滤 |
| ffmpeg 成片 | 剪映草稿 |
| CLI + 本地 Web 工作台 | 多产品自动拆分 / 账号多租户 |

时间戳必须和视频对齐，否则成片会切错。

## 下一步（对齐完整规格）

P0 加强：声纹、真实 ASR API、多产品切段、评测集  
P1：剪映草稿、公网鉴权  
P2：画面特写加权
