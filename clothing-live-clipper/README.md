# clothing-live-clipper

服装带货直播回放 → **自动口播打轴 + 特点前置 + 约 60 秒成片**。

当前主路径：

```
只上传视频 → 本地 faster-whisper 打轴 → 过滤废话/价格 → 逻辑排序 → 1.3x ≈60s → final.mp4
```

- **默认不依赖 Agent 对话**
- **默认不依赖云端 Whisper API**
- Web 页面为剪映风格深色 HTML，上传后自动处理

## 快速开始（Web）

```bat
cd clothing-live-clipper
set PATH=%LOCALAPPDATA%\ffmpeg\bin;%PATH%
set PYTHONPATH=src
start-web.bat
```

打开：http://127.0.0.1:8787/

1. 拖入/选择视频（支持 `mp4 / mov / mkv / webm / avi / m4v / ts / mts / m2ts`）  
2. 点「开始服装切片」  
3. 页面显示进度：抽音频 → 听写 → 过滤 → 排序 → 渲染  
4. 完成后可预览/下载 `final.mp4`

产物目录：`output/web_jobs/{job_id}/`

## 命令行（无界面）

```bat
set PYTHONPATH=src
set PATH=%LOCALAPPDATA%\ffmpeg\bin;%PATH%
python scripts\agent_clip_video.py "D:\video.mp4"
```

批量桌面目录：

```bat
python scripts\batch_desktop_clip.py
```

## 环境依赖

- Windows / Python 3.11+
- ffmpeg（`%LOCALAPPDATA%\ffmpeg\bin` 或 PATH）
- 本地 whisper 模型：`C:\Users\MR\AppData\grok\models\whisper-tiny`
- Python 包：`pip install -r requirements.txt`，以及 `faster-whisper`

```bat
pip install -r requirements.txt
pip install faster-whisper
```

## 当前规则（摘要）

- 输入只要视频
- 前 20 秒特点/卖点优先
- 不讨论价格
- 去直播控场词（如「过一下」）与非服装闲聊
- 逻辑排序优先于硬去重
- 拼接默认直接接（尽量无剪辑痕迹）
- 默认 1.3 倍速，成片约 55–65 秒

## 测试

```bat
set PYTHONPATH=src
pytest -q
```

## 与 Agent Skill 的关系

- 引擎代码可完全独立运行（Web/CLI）
- `clothing-live-clip` Skill 仍可用于对话触发/说明，但**不是主路径必须项**

## 目录

- `src/clipper/` 引擎 + Web
- `scripts/agent_clip_video.py` 本地一键流水线
- `scripts/batch_desktop_clip.py` 桌面批量
- `output/web_jobs/` Web 任务
- `output/agent_jobs/` CLI 任务
