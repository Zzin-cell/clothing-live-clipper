# Release v0.21 · 小面 CapCut 文档与能力发布

**Tag:** `v0.21-docs-release`  
**Branch:** `feature/web-video-workstation`  
**Date:** 2026-07-23  

---

## 🚀 这一版你可以用它做什么

把服装带货直播长视频，变成：

- 约 **60 秒** 短片  
- **前 20 秒** 强卖点吸睛  
- **尽量看不出直播感**  
- 支持 **人工精修 + 反向重剪**  
- 可选 **学习你的改法**，越用越贴口味  

适合：服装主播、中控、短视频运营、需要批量处理直播回放的商家。

---

## ✨ 亮点功能

### 1）一键自动切片
上传视频即可：

`听写打轴 → 去废话 → 抓特点 → 排序 → 渲染 final.mp4`

- 支持 `mp4 / mov / mkv / webm / ts ...`  
- 本地 faster-whisper  
- **GPU CUDA** 可用时自动加速（RTX 系列实测可显著提速）

### 2）产品向规则（全局）
- 去直播控场（家人们、扣1、过一下、公屏、福袋…）  
- 去尺码 / 价格 / 挂车  
- 前 20 秒独特特点优先  
- 换装/搭配后移  
- 默认 1.3x，成片约 55–65 秒  

### 3）人工反剪（真正可用）
- 成片卡片全展开可编辑  
- 口播时间轴卡片化，可拖入成片  
- **删选中文字段**（去掉句内小时间，例如“199再来一次”）  
- 按秒裁掉中间段  
- 拖到卡片上直接替换  
- 均分时长  
- 重剪强制刷新，避免缓存旧片  

### 4）可选学习（Plan D）
- 勾选「学习这次重剪」才写入全局偏好  
- 一键「清空学习」  
- 可用示例成片文件夹做 bootstrap 种子学习  

---

## 📦 安装与启动

```bat
git clone https://github.com/Zzin-cell/clothing-live-clipper.git
cd clothing-live-clipper
git checkout feature/web-video-workstation

cd clothing-live-clipper
pip install -r requirements.txt
pip install faster-whisper
:: GPU 推荐
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

start-web.bat
```

打开：http://127.0.0.1:8787/

详细文档：
- [README](../README.md)
- [PRODUCT](./PRODUCT.md)
- [ARCHITECTURE](./ARCHITECTURE.md)
- [CHANGELOG](./CHANGELOG.md)

---

## ⚠️ 使用注意（很重要）

1. **成片按时间裁原视频**，不是改字就改声音。  
2. 想去掉某句口语：  
   - 选中文字 → **删选中文字段** → **保存并重剪成片**  
3. 只改文字、不改时间/不删段，成片音画通常不变。  
4. 学习默认不强制开启；需要时再勾选。  

---

## 🏷 相关标签

| Tag | 说明 |
|-----|------|
| `v0.20-xiaomian-capcut-ui` | UI/GPU/反剪能力里程碑 |
| `v0.21-docs-release` | 文档规范化 + 本发布说明 |

---

## 🔭 下一步

- LLM 主卖点语义理解（更像人）  
- 字级时间戳，让“删选中文字段”更准  
- 可选 TTS：改词后重配音  
- 多产品自动分段  

---

**小面 · capcut**  
让服装直播回放，一键变成能挂车的短片。
