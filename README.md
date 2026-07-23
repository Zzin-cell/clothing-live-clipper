# 小面 CapCut · clothing-live-clipper

> 服装带货直播长视频 → **约 60 秒短片**  
> **前 20 秒强卖点** · **尽量看不出直播感** · **可人工反剪 + 可选学习**

本仓库主工程在 [`clothing-live-clipper/`](./clothing-live-clipper/)。

[![GitHub](https://img.shields.io/badge/GitHub-Zzin--cell%2Fclothing--live--clipper-181717?logo=github)](https://github.com/Zzin-cell/clothing-live-clipper)
[![Branch](https://img.shields.io/badge/branch-feature%2Fweb--video--workstation-blue)](https://github.com/Zzin-cell/clothing-live-clipper/tree/feature/web-video-workstation)
[![Release](https://img.shields.io/badge/release-v0.21--docs--release-brightgreen)](https://github.com/Zzin-cell/clothing-live-clipper/releases)

---

## ✨ 你能得到什么

| 痛点 | 小面怎么解决 |
|------|----------------|
| 直播回放太长 | 自动剪成约 60 秒 |
| 前几秒留不住人 | 黄金 20 秒优先独特卖点 |
| 像直播间不像短视频 | 去控场/尺码/价格/挂车废话 |
| 自动结果不完美 | 人工改词、裁小句、重排后一键重剪 |
| 越剪越想贴自己口味 | 可选「学习这次重剪」人机闭环 |

---

## 🚀 30 秒启动

```bat
cd clothing-live-clipper
pip install -r requirements.txt
pip install faster-whisper
start-web.bat
```

打开：**http://127.0.0.1:8787/**

1. 拖入直播视频  
2. 点「开始服装切片」  
3. 预览 / 下载 `final.mp4`  
4. 不满意？在成片结构里精修 → **保存并重剪成片**

完整文档请看：

- 📘 [工程 README](./clothing-live-clipper/README.md)  
- 🎯 [产品说明 PRODUCT](./clothing-live-clipper/docs/PRODUCT.md)  
- 🏗 [架构 ARCHITECTURE](./clothing-live-clipper/docs/ARCHITECTURE.md)  
- 📝 [变更 CHANGELOG](./clothing-live-clipper/docs/CHANGELOG.md)

---

## 🧠 产品标准（默认全局）

1. **看不出直播**  
2. **前 20 秒只放重点吸睛**（独特特点优先）  
3. **换装/搭配后移**  
4. **尺码 / 价格剔除**  
5. **约 55–65 秒 @ 1.3x**  
6. **直接拼接，减少剪辑痕迹**

---

## ✂️ 人工反剪（关键）

| 目标 | 操作 |
|------|------|
| 去掉「199再来一次」这类小句 | 选中文字 → **删选中文字段** → 重剪 |
| 精确裁秒 | 填「裁掉从/到」→ **裁掉这段** |
| 删整段 | **× / 删整段** |
| 替换某段 | 左侧口播拖到右侧卡片中间 |
| 时长不均 | **均分时长** |
| 学到全局口味 | 勾选 **学习这次重剪** 再保存 |

> ⚠️ 成片按 **时间轴裁原视频**。  
> **只改文字不会改原片声音**；改时间/删小段/删整段才会改变成片。

---

## 🏷 版本

| Tag | 说明 |
|-----|------|
| `v0.20-xiaomian-capcut-ui` | 小面 UI + GPU ASR + 人工反剪里程碑 |
| `v0.21-docs-release` | 文档规范化 + 发布说明整理 |

分支：`feature/web-video-workstation`

---

## 📁 仓库结构

```
.
├── README.md                      # 你正在看的总览
├── clothing-live-clipper/         # 主工程（引擎 + Web + 脚本）
│   ├── README.md
│   ├── docs/
│   ├── src/clipper/
│   ├── scripts/
│   └── tests/
└── skills/clothing-live-clip/     # Agent Skill（可选）
```

---

## 🧪 测试

```bat
cd clothing-live-clipper
set PYTHONPATH=src
pytest -q
```

---

## 🤝 贡献

欢迎 Issue / PR。  
建议先读 `clothing-live-clipper/docs/PRODUCT.md` 对齐产品标准。

---

**小面 · capcut**  
让服装直播回放，一键变成能挂车的短片。
