# clothing-live-clipper（简单可用版）

服装带货直播回放 → **卖点提取 + 黄金 20 秒重排 + 约 60 秒成片** 的本地 MVP。

完整规格见：`../docs/superpowers/specs/2026-07-18-clothing-live-clipper-design.md`  
本仓库当前实现的是**可跑通的竖切面**：含 CLI 与本地 Web 工作台；Web 主路径为 **只上传视频 → Whisper 智能口播打轴 → 切片**。不做声纹、剪映草稿、多产品切分。

## 功能

1. **智能口播（Whisper API）**：视频抽音频并自动句级时间戳（类剪映智能口播）
2. 规则识别：版型 / 布料 / 卖点 / 价格等
3. 打分排序，生成三段结构：黄金开头(20s) + 信任建设 + 促单(10s)
4. 有视频 + ffmpeg 时裁切拼接为 `final.mp4`
5. CLI 仍支持外部 `.json` / `.srt` 转写
6. 本地 Web：**只上传视频**即可处理（需配置 ASR API Key）

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

1. 复制 `.env.example` 为 `.env`，填写：
   - `OPENAI_API_KEY=` 或 `CLIPPER_ASR_API_KEY=`
   - （可选）兼容中转：`CLIPPER_ASR_BASE_URL=`
2. 主入口：**只上传直播视频** → 自动 Whisper 听写打轴 → 重排切片
3. 产物目录：`output/web_jobs/{job_id}/`（含 `transcript_asr.json`、`plan.json`、`final.mp4`）

### 限制更新表

| 有 | 无 |
|----|----|
| 只传视频 + Whisper 智能口播打轴 | 声纹过滤 |
| Web 预览/下载/历史回看 | 账号/多租户 |
| OpenAI 兼容 Whisper API | 本地离线模型（可后加） |

## 测试

```bat
set PYTHONPATH=src
pytest -q
```

## 说明与限制（MVP）

| 有 | 无 |
|----|----|
| Whisper 智能口播打轴（视频→时间戳） | 主播声纹过滤 |
| 规则卖点抽取 + 黄金 20s 重排 | 剪映草稿 |
| ffmpeg 成片 | 多产品自动拆分 / 账号多租户 |
| CLI + 本地 Web | 本地离线 Whisper（可选后续） |

Web 听写时间轴由 ASR 生成；CLI 若手传转写，时间戳须与视频对齐。

## 下一步（对齐完整规格）

P0 加强：声纹、多产品切段、评测集  
P1：剪映草稿、异步长任务进度  
P2：画面特写加权
