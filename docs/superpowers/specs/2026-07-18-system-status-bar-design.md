# 总状态栏与 API 设置抽屉设计规格

| 项 | 内容 |
|----|------|
| 版本 | v1.0 |
| 日期 | 2026-07-18 |
| 状态 | 对话已确认 §1–§3，待用户审阅本文件 |
| 路线 | 方案 A：顶栏状态灯 + 右侧设置抽屉 |
| 代码位置 | `clothing-live-clipper/src/clipper/web.py`, `config.py`, `static/*` |
| 关联 | 视频-only Whisper 智能口播主路径 |

---

## 1. 产品定位

### 1.1 是什么

在本地 Web 工作台增加**总状态栏 + 设置抽屉**，用于：

1. 配置 AI API Key / Base URL / 模型（默认写入本机 `.env`，可选仅会话）
2. 检查「只传视频 → 听写打轴 → 切片 → 成片」**全量运维**环节
3. 提示 **OpenAI 兼容**听写与对话模型的填法
4. 展示磁盘、依赖、最近任务失败等

### 1.2 已确认决策

| 项 | 选择 |
|----|------|
| 形态 | A：顶栏灯 + 设置抽屉（非第二整页、非主路径大表单） |
| Key 存储 | 默认写 `.env`；可选「仅当前会话」 |
| 范围 | 全量运维向 |

### 1.3 非目标

- 多用户账号 / 云端密钥托管
- 将服务默认暴露公网
- 自动扫描局域网模型
- 在 GET 响应中返回完整 API Key

### 1.4 成功标准

- UI 可完成 Key 配置并驱动 Whisper 主路径（persist 或 session）
- 顶栏能一眼看出卡住的环节
- 兼容中转：Base URL + 模型名有明确说明与示例
- 完整 Key 不出现在 GET 响应 / 前端持久化明文 / 日志
- status/config/probe 可用 pytest 覆盖（mock 外呼）

---

## 2. 信息架构

### 2.1 布局

```
Titlebar: [traffic] [brand] .... [status lights] [设置]
Main: 只上传视频（主路径不变）
Drawer (right ~400px): 总览 | API与模型 | 环境与存储 | 最近任务
```

### 2.2 顶栏状态灯（5）

| 灯 | 绿 | 黄 | 红 |
|----|----|----|-----|
| 服务 | 正常 | — | 异常 |
| ffmpeg | 可用 | 缺 ffprobe | 缺失 |
| Whisper | 已配置（探测 OK 更绿） | 已配置未探测 | 无 Key/探测失败 |
| LLM | 已配置 OK | 未配置（规则降级，可选） | Key 无效 |
| 磁盘 | 可写且空间充足 | 空间紧张 | 不可写 |

悬停显示摘要；点击「设置」打开抽屉。

### 2.3 抽屉四段

#### ① 总览体检

每行：`环节 | 状态 | 说明 | 动作`

环节列表：

- Web 服务
- ffmpeg / ffprobe / 抽音频能力
- Whisper 听写
- LLM 卖点（可选）
- 输出目录可写与空间
- 依赖版本
- 最近任务健康度

动作按钮：

- **一键自检** → `GET /api/system/status` 或带 `refresh=1`
- **测试 Whisper** → `POST /api/system/probe` `{target:"whisper"}`
- **测试 LLM** → `POST /api/system/probe` `{target:"llm"}`

#### ② API 与模型

| 字段 | 默认 / 说明 |
|------|-------------|
| API Key | 密码框；保存后掩码 `****abcd` |
| Base URL | `https://api.openai.com/v1` |
| Whisper 模型 | `whisper-1` |
| LLM 模型 | `gpt-4o-mini` |
| 启用 LLM | 可选开关；关则仅规则抽取 |
| 记住本机 | **默认 true** → 写 `.env`；false → 进程会话 |

**兼容说明（UI 固定提示）：**

- ASR：`POST {base}/audio/transcriptions`；官方 `whisper-1`；中转填兼容 Base URL；建议 `verbose_json` + `segments`
- LLM：`POST {base}/chat/completions`；官方 `gpt-4o-mini` / `gpt-4o`；未配置不阻断主路径

#### ③ 环境与存储

- `output/web_jobs` 绝对路径、可写、free_gb
- ffmpeg/ffprobe 路径与版本
- Python 与关键包版本
- bind host:port
- 配置来源：`env` | `session`

#### ④ 最近任务

最近最多 10 条：`job_id, status, error, created_at`；点击加载主区任务详情。

### 2.4 主路径提示

若 Whisper 红：在新建任务卡片顶部显示一条提示  
「请先在设置中配置 API Key 并自检通过」——**不隐藏**上传控件。

---

## 3. API 与配置

### 3.1 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 可保留精简字段；顶栏可改用 status |
| GET | `/api/system/status` | 全量体检 |
| POST | `/api/system/probe` | body: `{ "target": "whisper" \| "llm" \| "all" }` |
| GET | `/api/system/config` | 非机密 + key 掩码 |
| PUT | `/api/system/config` | 更新配置；`persist: boolean` |

### 3.2 PUT body（示例）

```json
{
  "persist": true,
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "asr_model": "whisper-1",
  "llm_model": "gpt-4o-mini",
  "llm_enabled": true,
  "asr_enabled": true
}
```

空 `api_key` 表示不修改已有密钥。

### 3.3 持久化

- `persist=true`：合并写入 `clothing-live-clipper/.env`（不删未知键）
- `persist=false`：进程内 overlay，优先级高于 `.env`
- 保存后刷新 `asr_status` / Settings 读取路径
- GET 永不回传完整 key：`has_key`, `key_hint`

管理键示例：

```
CLIPPER_ASR_ENABLED
CLIPPER_ASR_PROVIDER
CLIPPER_ASR_API_KEY  (或 OPENAI_API_KEY)
CLIPPER_ASR_BASE_URL
CLIPPER_ASR_MODEL
CLIPPER_LLM_API_KEY
CLIPPER_LLM_BASE_URL
CLIPPER_LLM_MODEL
```

### 3.4 探测

| 项 | 策略 |
|----|------|
| ffmpeg | which + `-version` |
| Whisper | 鉴权/最小 transcription 或可区分 401 的请求；记录 ok/error |
| LLM | 最小 chat completion |
| 磁盘 | 输出目录 writable；free_gb：&lt;1 黄，不可写红 |

### 3.5 status JSON 形状（规范）

```json
{
  "service": {"ok": true, "host": "127.0.0.1", "port": 8787},
  "ffmpeg": {"ok": true, "path": "...", "version": "..."},
  "ffprobe": {"ok": true, "path": "..."},
  "asr": {
    "configured": true,
    "ok": null,
    "provider": "openai_whisper",
    "model": "whisper-1",
    "base_url": "https://api.openai.com/v1",
    "key_hint": "abcd",
    "source": "env"
  },
  "llm": {
    "configured": false,
    "ok": null,
    "optional": true,
    "model": "gpt-4o-mini",
    "source": "env"
  },
  "storage": {"path": "...", "writable": true, "free_gb": 100.0},
  "deps": {"python": "3.13.x", "packages": {}},
  "recent_jobs": [],
  "compat": {
    "asr": [
      "OpenAI whisper-1",
      "Compatible POST /v1/audio/transcriptions with verbose_json segments"
    ],
    "llm": [
      "gpt-4o-mini / gpt-4o",
      "Any OpenAI-compatible chat model id"
    ]
  },
  "checked_at": "2026-07-18T00:00:00Z"
}
```

### 3.6 安全

- 默认仅本机；文档强调勿暴露公网写配置接口
- 日志脱敏
- 不提交真实 `.env` 入 git（已有 gitignore）

---

## 4. UI 风格

- 延续现有 macOS 顶栏与毛玻璃
- 抽屉：右侧滑入、遮罩、ESC 关闭
- 状态灯：小圆点 + 可选文字
- 兼容说明用 inset 提示卡片，非弹窗连篇

---

## 5. 实现落点（预告）

| 文件 | 变更 |
|------|------|
| `config.py` | session overlay、.env 合并读写、掩码 |
| `web.py` | `/api/system/*` |
| `static/index.html` | 灯 + 设置按钮 + 抽屉 DOM |
| `static/app.js` | 打开抽屉、保存配置、自检 |
| `static/styles.css` | 抽屉与状态灯 |
| `tests/test_system_status.py` | API 契约 |

---

## 6. 测试要点

- status 在无 key 时 asr.configured=false
- PUT persist=false 不写文件但进程内 has_key
- PUT persist=true 写入临时 .env（tmp_path monkeypatch）
- GET config 无完整 key
- probe whisper 在 mock httpx 下 ok/fail
- 前端非必须 e2e；手工点验抽屉

---

## 7. 已确认决策记录

1. 方案 A：顶栏 + 设置抽屉  
2. Key：默认 `.env`，可选仅会话  
3. 范围：全量运维向  
4. 设计 §1–§3 对话 OK  

---

## 8. 下一步

1. 用户审阅本文件  
2. 批准后 writing-plans → 实现  
3. 实现后与视频-only Whisper 主路径联调  

---

## 9. 规格自检

| 检查项 | 结果 |
|--------|------|
| 占位符 | 无阻断 TBD；Whisper 探测具体策略实现期在「鉴权/最小请求」中二选一写死 |
| 一致性 | 灯、抽屉四段、API、安全一致 |
| 范围 | 单 Web 增量；不含公网多租户 |
| 歧义 | persist 默认 true；LLM 可选；GET 不返回完整 key |

（自检通过，待用户审阅。）
