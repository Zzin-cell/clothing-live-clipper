# 小面 CapCut · clothing-live-clipper

> **一句话**：把服装带货直播长视频，自动剪成 **约 60 秒、前 20 秒强卖点、尽量看不出直播感** 的短片。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](#)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green.svg)](#)
[![GPU](https://img.shields.io/badge/GPU-CUDA%20optional-76B900.svg)](#)
[![License](https://img.shields.io/badge/License-see%20repo-lightgrey.svg)](#)

---

## 为什么做这个

直播回放很长，真正能卖货的往往只有：

- 面料 / 版型 / 显瘦 / 不透  
- 独特卖点与细节证明  

人工剪又慢又累。  
**小面** 帮你：自动听写 →（可选）LLM 逻辑处理口播稿 → 按时间轴反剪成片 → 还能人工精修再重剪。

---

## 核心能力

| 能力 | 说明 |
|------|------|
| **只传视频** | 不用先准备口播稿 |
| **本地 ASR** | faster-whisper medium，支持 **GPU CUDA** + 降噪 |
| **LLM 逻辑排片** | 用户在网页填写自己的 API（OpenAI 兼容），处理 ASR 全量小句后反剪；失败回退规则 |
| **去直播感** | 剔除家人们/扣1/上链接/尺码/价格等 |
| **逻辑通顺** | 卖点→版型→穿着体验→细节→搭配后置 |
| **成片约 60s** | 默认 1.4x 播放 |
| **人工反剪** | 改时间、删小句、重排后一键重渲 |
| **可选学习** | 勾选后把你的改法写进全局偏好 |

---

## 版本

| 标签 | 说明 |
|------|------|
| **V3.1** | 逻辑成片撤销/恢复（2 步）+ 中间删字保留后半 · [发布说明](docs/RELEASE_V3.1.md) |
| **V3** | 离线绿包 / 队列 / 反剪学习全路径 |

离线小白包构建：`python scripts/build_portable_package.py` → 桌面 `xiaomian-V3.zip`。

---

## 30 秒上手

### 1）环境

- Windows 10/11  
- Python 3.11+  
- ffmpeg（建议 `%LOCALAPPDATA%\ffmpeg\bin`）  
- （可选）NVIDIA GPU + CUDA 运行库（听写更快更准）

```bat
cd clothing-live-clipper
pip install -r requirements.txt
pip install faster-whisper
:: GPU 推荐再装：
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### 2）启动 Web（小面）

```bat
start-web.bat
```

浏览器打开：**http://127.0.0.1:8787/**

### 3）剪一条

1. 拖入直播视频（`mp4/mov/mkv/webm/ts/...`）  
2. 点 **开始服装切片**  
3. 等听写 → 过滤 → 排序 → 渲染  
4. 预览 / 下载 `final.mp4`

---

## 产品标准（全局默认）

1. **看不出直播**  
   去掉控场话术、欢迎语、公屏互动、挂车话术  
2. **前 20 秒只放重点**  
   独特/强卖点优先，不是流水账  
3. **换装/搭配后移**  
4. **尺码 / 价格剔除**  
5. **约 55–65 秒 @ 1.4x**  
6. **拼接尽量无痕迹**（直接接 + 微消爆音）

---

## 人工精修（反向剪辑）

自动结果不满意时：

| 你想做的事 | 操作 |
|------------|------|
| 去掉某一小句（如「199再来一次」） | 口播框 **选中文字** → **删选中文字段** → 重剪 |
| 精确裁秒 | 填「裁掉从/到」→ **裁掉这段** → 重剪 |
| 删掉整段模块 | 点 **× / 删整段** → 重剪 |
| 替换某段 | 左侧口播 **拖到右侧卡片中间** 替换 |
| 时长不均 | 点 **均分时长** |
| 学到全局口味 | 勾选 **学习这次重剪** 再保存 |

> ⚠️ **改文字 ≠ 改原片声音**。  
> 成片是按 **时间轴裁原视频**；只有改时间 / 删小段 / 删整段 / 重排 才会改变成片内容。

---

## 学习系统（Plan D）

可选的人机闭环：

```
自动切片 → 你改到满意 → 勾选「学习这次重剪」→ 写入偏好
→ 下次自动排序更像你
```

- 偏好文件：`output/learning/preferences.json`  
- 可 **清空学习** 重来  
- 可用示例成片批量种子学习：

```bat
python scripts\bootstrap_learning_from_folder.py "D:\学习样本文件夹"
```

---

## 命令行用法

```bat
set PYTHONPATH=src
set PATH=%LOCALAPPDATA%\ffmpeg\bin;%PATH%
python scripts\agent_clip_video.py "D:\video.mp4"
```

批量桌面目录（待剪辑 → 已经完成）：

```bat
python scripts\batch_desktop_clip.py
```

---

## 目录结构（精简）

```
clothing-live-clipper/
├── start-web.bat                 # 一键启动小面 Web
├── README.md
├── docs/
│   ├── PRODUCT.md                # 产品说明
│   ├── ARCHITECTURE.md           # 架构与流水线
│   └── CHANGELOG.md              # 版本变更
├── src/clipper/                  # 核心引擎 + Web
│   ├── web.py                    # FastAPI
│   ├── rank.py                   # 排序/黄金20s
│   ├── extract.py                # 标签与词表
│   ├── media.py                  # 裁切/拼接/倍速
│   ├── learning.py               # 人机学习
│   ├── job_worker.py             # 后台任务
│   └── static/                   # 小面前端（白底 UI）
├── scripts/                      # ASR/批处理/学习种子等
├── tests/                        # pytest
└── output/
    ├── web_jobs/                 # Web 任务产物
    └── learning/                 # 学习偏好
```

---

## 配置要点（`.env.example`）

| 变量 | 含义 | 建议 |
|------|------|------|
| `CLIPPER_ASR_DEVICE` | `cuda` / `cpu` | 有 NVIDIA 用 `cuda` |
| `CLIPPER_ASR_COMPUTE_TYPE` | `float16` / `int8` | GPU 用 `float16` |
| `CLIPPER_LOCAL_WHISPER_MODEL` | 本地模型路径 | `.../models/whisper-small` |
| `CLIPPER_ASR_BEAM_SIZE` | 解码宽度 | GPU 可用 `3` |
| `CLIPPER_PLAYBACK_SPEED` | 成片倍速 | 默认 `1.3` |

---

## 测试

```bat
set PYTHONPATH=src
pytest -q
```

---

## 里程碑版本

- 标签：`v0.20-xiaomian-capcut-ui`  
- 分支：`feature/web-video-workstation`  
- 本整理版本：见 `docs/CHANGELOG.md`

---

## 路线图（简）

- [x] 本地 ASR + Web 自动流水线  
- [x] 去直播感 / 去尺码价格 / 特点前 20s  
- [x] GPU 听写  
- [x] 人工反剪（删小段/替换/均分）  
- [x] 可选学习闭环  
- [ ] LLM 语义理解主卖点（更像人）  
- [ ] 字级时间戳精准句内裁剪  
- [ ] 可选 TTS 改词后重配音  

---

## 许可证 / 贡献

欢迎 Issue / PR。  
提交前请跑通：`pytest -q`。

---

**小面 · capcut**  
让服装直播回放，一键变成能挂车的短片。
