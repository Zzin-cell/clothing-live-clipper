# Changelog

## V3.1 · 成片撤销 + 中间删字修尾（2026-08-13）

**Tag:** `V3.1` · UI `jy81-plan-undo`

- 逻辑成片 **撤销 / 恢复**（最多 2 步，Ctrl+Z / Ctrl+Y），仅「保存并重剪」前有效  
- 修复「删选中中间文字 → 后半段被唯一性逻辑误删」  
- 任务切换/完成后逻辑成片与口播与任务 id 绑定刷新（V3 延续）  
- 时间戳 UI 0.01s、直播过门硬删/剥离、同主题连排、离线包 `xiaomian-V3` 构建目标  

详见 [RELEASE_V3.1.md](./RELEASE_V3.1.md)

## V3 · 离线绿包 + 队列与学习全路径（2026-08）

**Tag:** `V3`

- 局域网多任务队列（位次 / 等待 / ETA）、槽位与预热  
- 反剪学习接到云端 LLM / 本地卖点 / 规则三条路径  
- 干净离线包：`build_portable_package.py` → Desktop `xiaomian-V3.zip`  
- UI 精简与可折叠 LLM 配置等  

## v0.21 · 文档整理与完整推送（2026-07-23）

- 全面规范化 README / PRODUCT / ARCHITECTURE  
- 统一产品标准、反剪说明、学习机制文档  
- 准备完整推送 GitHub  

## v0.20 · 小面 CapCut UI 里程碑

标签：`v0.20-xiaomian-capcut-ui`

### 产品
- Web 白底 UI，品牌「小面 / capcut」  
- 上传即自动本地处理（无需 Agent 对话）  
- 全局规则：去直播感、去尺码价格、特点前20s、换装后移  

### 听写
- faster-whisper local  
- GPU CUDA float16（RTX 系列）  
- small 模型提升中文精度  

### 人工反剪
- 成片卡片全展开可编辑  
- 口播时间轴卡片化，可拖入成片  
- 拖替换 / 均分时长  
- **删选中文字段**（句内小时间裁切）  
- 按秒裁掉中间段  
- 重剪强制刷新 final  

### 学习（Plan D）
- 可选「学习这次重剪」  
- 清空学习  
- 示例文件夹 bootstrap 种子学习  
- 学习权重加强，能影响排序  

### 工程
- 并行裁切 + concat copy  
- pytest 覆盖核心策略与 Web API  

## 更早

- v0.1x：CLI MVP、规则切片、Web 工作台雏形  
- 队列/Agent skill 形态后收敛为「本地自动主路径」  
