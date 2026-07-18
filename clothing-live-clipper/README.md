# clothing-live-clipper（简单可用版）

服装带货直播回放 → **卖点提取 + 黄金 20 秒重排 + 约 60 秒成片** 的本地 MVP。

完整规格见：`../docs/superpowers/specs/2026-07-18-clothing-live-clipper-design.md`  
本仓库当前实现的是**可跑通的竖切面**，不做声纹、剪映草稿、Web、多产品切分。

## 功能

1. 读取口播转写（`.json` / `.srt`）
2. 规则识别：版型 / 布料 / 卖点 / 价格等
3. 打分排序，生成三段结构：黄金开头(20s) + 信任建设 + 促单(10s)
4. 有视频 + ffmpeg 时裁切拼接为 `final.mp4`
5. 无视频也可只出 `plan.json` / `review.md`

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

## 测试

```bat
set PYTHONPATH=src
pytest -q
```

## 说明与限制（MVP）

| 有 | 无 |
|----|----|
| 规则卖点抽取 | 云端 ASR 自动听写（请自备转写） |
| 黄金 20s 重排 | 主播声纹过滤 |
| ffmpeg 成片 | 剪映草稿 |
| CLI | Web 界面 / 多产品自动拆分 |

时间戳必须和视频对齐，否则成片会切错。

## 下一步（对齐完整规格）

P0 加强：声纹、ASR API、多产品切段、评测集  
P1：剪映草稿、本地预览页  
P2：画面特写加权
