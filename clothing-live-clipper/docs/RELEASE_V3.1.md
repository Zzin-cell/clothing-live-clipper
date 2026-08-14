# Release V3.1 · 小面 / xiaomian

**Tag:** `V3.1`（annotated）  
**Previous:** `V3`  
**Branch:** `master`  
**Date:** 2026-08-13  
**UI build:** `jy81-plan-undo`  

---

## 一句话

本地服装直播切片：**听写 → 逻辑排片 → 人工反剪 → 重剪出片**。  
V3 稳住局域网排队与离线绿包；**V3.1** 修好「中间删字吞后半」并加上成片 **撤销/恢复**。

---

## V3.1 相对 V3 的变化

### 逻辑成片编辑

| 功能 | 说明 |
|------|------|
| **撤销 ↶ / 恢复 ↷** | 最多 **2 步**（类似 Office 短撤销栈） |
| 快捷键 | **Ctrl+Z** 撤销 · **Ctrl+Y** / **Ctrl+Shift+Z** 恢复 |
| 生效范围 | 仅 **保存并重剪之前**；提交重剪或切换任务后清空 |
| 会记历史的操作 | 删选中、按秒裁、删整段、拖序、加入/替换、改文案（连打算 1 步）、改时间 |

### 反剪修复（重要）

选中 **中间** 几个字再点「删选中文字段」时，原先后半口播会消失。

**原因：** 切成前后两段后共用 `from_asr_idx`，「模块唯一」把后半当成重复删掉。  

**修复（`jy80`/`jy81`）：** 拆分后去共用 id；时间不重叠的同来源句不再当重复。  
效果：中间删除 → **前半 + 后半两张卡**，段数 +1。

### 其它 V3 系已含能力（摘要）

- 任务切换 / 完成后 **强制刷新逻辑成片与口播**（避免任务 2 仍显示任务 1 的 plan）  
- 同主题小句连排；手速/开架/抱一下等直播过门硬删/剥离  
- 模块时间 UI **0.01s** 精度  
- 反剪学习接到云端/本地/规则三条路径  
- 离线绿包目标：`Desktop\xiaomian-V3` + `xiaomian-V3.zip`  

---

## 离线分发（小白包）

构建（开发机、需一次网络拉 wheel 时可接受）：

```bat
cd clothing-live-clipper
python scripts\build_portable_package.py
```

产出：

| 交付物 | 默认路径 |
|--------|----------|
| 文件夹 | `%USERPROFILE%\Desktop\xiaomian-V3\` |
| Zip | `%USERPROFILE%\Desktop\xiaomian-V3.zip` |

包内：内置 Python + 预装依赖、wheels、ffmpeg、VC++ 红包、whisper medium/small/tiny。  
**不含：** `.venv`、`.env`、API Key、`web_jobs`、学习缓存、tests/docs。

使用：解压到短路径 → **启动小面.bat** → 网页右侧自填 LLM。

---

## 升级与验证清单

1. 停止旧进程后用最新包或源码启动  
2. 浏览器 **Ctrl+F5**（UI 对齐 `jy81-plan-undo`）  
3. 连续两条任务：第二条 **逻辑成片文案/时间** 应更新  
4. 中间选字删除：应拆成前后两卡，**Ctrl+Z** 可回退  
5. 保存并重剪后，撤销按钮应变灰  

---

## Git

```text
Tag V3     — release(V3) portable + plan/job sync baseline
Tag V3.1   — release(V3.1) plan undo/redo + mid-cut split dedupe fix
Commit     — 见 git show V3.1
```

推送：

```bat
git push origin master
git push origin V3
git push origin V3.1
```

> 说明：仓库目录结构可能带 `clothing-live-clipper/` 前缀；桌面 `xiaomian-V3` 是构建产物，**不是** git 仓库。

---

## 已知边界

- 撤销只保留 **2** 步；保存并重剪后不可撤销已提交编辑  
- 中间删字时间仍为 **字比例估算**（无词级 ASR 时）；帧级对齐需后续 word timestamps  
- 空白机首次可能需装 VC++（包内 redist）并重启  
- GPU 听写依赖本机 CUDA/驱动；失败会回退 CPU（更慢）  

---

## 相关文档

- [CHANGELOG.md](./CHANGELOG.md)  
- [PRODUCT.md](./PRODUCT.md)  
- [ARCHITECTURE.md](./ARCHITECTURE.md)  
- 包内 `先读我.txt` / `使用说明-操作指南.txt`  
