# Clothing Live Clipper Web Workstation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 FastAPI Web 收成「视频优先」工作台：可只交视频、可补传转写、处理后在页内预览/回看历史，产物自动落在 `output/web_jobs/{id}/` 并支持下载。

**Architecture:** 继续单进程 `clipper.web:app`。`POST /api/jobs` 以视频为主；无转写且 ASR 未就绪 → `needs_transcript` 并保留 uploads；补传 `POST /api/jobs/{id}/transcript` 后同步 `run_pipeline`。前端 static 三文件改文案与补传区。ASR 仅 health/配置探测，不实现厂商调用。

**Tech Stack:** Python 3.11+、FastAPI、uvicorn、python-multipart、httpx/Starlette TestClient、现有 `run_pipeline`、原生 HTML/JS/CSS。

**Spec:** `docs/superpowers/specs/2026-07-18-clothing-live-clipper-web-design.md`

## Global Constraints

- 默认绑定 `127.0.0.1:8787`；数据目录 `clothing-live-clipper/output/web_jobs/`
- 状态枚举：`queued` | `needs_transcript` | `transcribing` | `processing` | `success` | `success_partial` | `failed`
- 仅视频无转写、ASR 未就绪 → **HTTP 200** + `needs_transcript`（不是 4xx）
- ASR 未实现时禁止假 `transcribing`；`asr_note: not_implemented` fallback
- 有转写时请求内**同步** `run_pipeline`
- 视频扩展名：`.mp4 .mov .mkv .webm .avi`；转写：`.json .srt`
- `target_seconds` 15–180，默认 60
- 不引入 React/新前端工程；不实现真实 ASR provider
- Windows cmd：`git commit -F msgfile`；测试：`set PYTHONPATH=src` 后 `pytest`
- 依赖需补齐：`fastapi`、`uvicorn`、`python-multipart`（当前 requirements 可能缺失）

---

## File Structure

| 路径 | 职责 |
|------|------|
| `clothing-live-clipper/requirements.txt` | 增加 web 依赖 |
| `clothing-live-clipper/pyproject.toml` | 同步 web 依赖（optional 或 main） |
| `clothing-live-clipper/src/clipper/config.py` | ASR 环境探测字段/函数 |
| `clothing-live-clipper/src/clipper/web.py` | 状态机、创建、补传、meta、health |
| `clothing-live-clipper/src/clipper/static/index.html` | 视频优先表单 + 补传区 |
| `clothing-live-clipper/src/clipper/static/app.js` | 提交/列表/详情/补传 |
| `clothing-live-clipper/src/clipper/static/styles.css` | 状态徽章与行动区 |
| `clothing-live-clipper/tests/test_web_api.py` | API 契约测试 |
| `clothing-live-clipper/README.md` | Web 启动与用法 |

---

### Task 1: Web 依赖 + API 测试骨架（RED）

**Files:**
- Modify: `clothing-live-clipper/requirements.txt`
- Modify: `clothing-live-clipper/pyproject.toml`
- Create: `clothing-live-clipper/tests/test_web_api.py`
- Modify: `clothing-live-clipper/src/clipper/web.py`（仅当测试 import 需要 app；本任务以失败测试为主）

**Interfaces:**
- Produces: pytest 能 `from clipper.web import create_app`；TestClient 夹具使用临时 `JOBS_DIR`

- [ ] **Step 1: 把 web 依赖写入 requirements 与 pyproject**

`requirements.txt` 增加：

```text
fastapi>=0.110
uvicorn>=0.27
python-multipart>=0.0.9
```

`pyproject.toml` `[project] dependencies` 同步上述三项（或 `optional-dependencies.web`，**推荐直接写进主 dependencies** 以免安装遗漏）。

- [ ] **Step 2: 安装依赖**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
python -m pip install fastapi uvicorn python-multipart pytest httpx -q
```

Expected: 成功，无 error。

- [ ] **Step 3: 写失败测试 `tests/test_web_api.py`**

```python
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipper import web as webmod
from clipper.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    jobs = tmp_path / "web_jobs"
    jobs.mkdir()
    monkeypatch.setattr(webmod, "JOBS_DIR", jobs)
    app = create_app()
    with TestClient(app) as c:
        yield c, jobs


def test_health_has_asr_configured(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "ffmpeg" in body
    assert "asr_configured" in body
    assert body["asr_configured"] is False  # default


def test_create_video_only_needs_transcript(client):
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "needs_transcript"
    job_id = body["job_id"]
    assert (jobs / job_id / "uploads").exists()
    vids = list((jobs / job_id / "uploads").glob("*.mp4"))
    assert vids, "video should be saved under uploads"


def test_empty_submit_400(client):
    c, _ = client
    r = c.post("/api/jobs", data={"render": "false"})
    assert r.status_code == 400


def test_attach_transcript_and_process(client):
    c, jobs = client
    video_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    r = c.post(
        "/api/jobs",
        data={"target_seconds": "60", "render": "false"},
        files={"video": ("demo.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    tr = FIXTURE.read_bytes()
    r2 = c.post(
        f"/api/jobs/{job_id}/transcript",
        data={"render": "false"},
        files={"transcript": ("t.json", io.BytesIO(tr), "application/json")},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] in {"success", "success_partial"}
    assert body.get("files", {}).get("plan") is True
    assert (jobs / job_id / "plan.json").exists()


def test_list_jobs_includes_new(client):
    c, _ = client
    tr = FIXTURE.read_bytes()
    r = c.post(
        "/api/jobs",
        data={"use_sample": "false", "target_seconds": "60", "render": "false"},
        files={"transcript": ("t.json", io.BytesIO(tr), "application/json")},
    )
    # sample path optional — if use_sample true preferred:
    assert r.status_code in {200, 400}
    # primary: use_sample
    r = c.post(
        "/api/jobs",
        data={"use_sample": "true", "target_seconds": "60", "render": "false"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    lst = c.get("/api/jobs").json()["jobs"]
    ids = {j["job_id"] for j in lst}
    assert job_id in ids
```

Note: 若现有 `create_job` 在无视频有 sample 已 200，保留；`test_create_video_only_needs_transcript` 是本任务 RED 核心。

- [ ] **Step 4: 跑测试确认 RED**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
python -m pytest tests\test_web_api.py -v
```

Expected: 至少 `test_health_has_asr_configured` 或 `test_create_video_only_needs_transcript` **FAIL**（缺字段/仅视频 400）。

- [ ] **Step 5: Commit**

```bat
cd /d C:\Users\MR\AppData\grok
git add clothing-live-clipper/requirements.txt clothing-live-clipper/pyproject.toml clothing-live-clipper/tests/test_web_api.py
git commit -F- < nul
```

Use message file:

```text
test: RED web API video-first and needs_transcript
```

---

### Task 2: config ASR 探测 + web 后端状态机（GREEN API）

**Files:**
- Modify: `clothing-live-clipper/src/clipper/config.py`
- Modify: `clothing-live-clipper/src/clipper/web.py`

**Interfaces:**
- Produces:
  - `def asr_status() -> dict` with keys `asr_configured: bool`, `asr_note: str | None`
  - `POST /api/jobs` video-only → `needs_transcript`
  - `POST /api/jobs/{job_id}/transcript`
  - health includes `asr_configured`
  - terminal status `success` vs `success_partial` when plan ok but no mp4 / render skipped

- [ ] **Step 1: 扩展 config**

在 `config.py` 增加：

```python
def asr_status() -> dict:
    """Read-only ASR probe. Never claim ready if provider not implemented."""
    enabled = (os.getenv("CLIPPER_ASR_ENABLED") or "false").lower() in {"1", "true", "yes"}
    provider = (os.getenv("CLIPPER_ASR_PROVIDER") or "none").strip().lower()
    # v1: no real provider implemented
    implemented = False
    configured = bool(enabled and provider not in {"", "none"} and implemented)
    note = None
    if enabled and provider not in {"", "none"} and not implemented:
        note = "not_implemented"
    return {"asr_configured": configured, "asr_note": note, "asr_provider": provider}
```

- [ ] **Step 2: 改 `create_app` health**

```python
from clipper.config import Settings, asr_status

@app.get("/api/health")
def health():
    a = asr_status()
    return {
        "ok": True,
        "ffmpeg": bool(which_ffmpeg()),
        "sample_transcript": SAMPLE_TRANSCRIPT.exists(),
        "asr_configured": a["asr_configured"],
        "asr_note": a.get("asr_note"),
        "time": _utc_now(),
    }
```

- [ ] **Step 3: 重写/扩展 create_job 核心分支（保持函数名 `create_job`）**

逻辑伪代码（写入真实 Python 时贴合现有结构）：

```python
# after saving optional uploads...
has_tr = transcript_path is not None  # from upload or sample
has_vid = video_path is not None

if not has_tr and not has_vid:
    raise HTTPException(400, detail="请上传视频或转写，或勾选示例转写")

if has_vid and not has_tr:
    a = asr_status()
    if not a["asr_configured"]:
        meta.update({
            "status": "needs_transcript",
            "has_video": True,
            "has_final": False,
            "finished_at": _utc_now(),
            "asr_configured": a["asr_configured"],
            "asr_note": a.get("asr_note"),
        })
        _write_meta(d, meta)
        return get_job(job_id)
    # configured but v1 not implemented → same fallback
    meta.update({..., "status": "needs_transcript", "asr_note": "not_implemented"})
    ...
    return get_job(job_id)

# has transcript → processing path (existing run_pipeline)
meta["status"] = "processing"
...
result = run_pipeline(...)
# map status:
if result.plan and result.output_mp4:
    status = "success"
elif result.plan:
    status = "success_partial"
else:
    status = "failed"
meta["has_video"] = has_vid
meta["has_final"] = bool(result.output_mp4)
```

Helper:

```python
def _write_meta(d: Path, meta: dict) -> None:
    (d / "job_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
```

- [ ] **Step 4: 新增补传端点**

```python
@app.post("/api/jobs/{job_id}/transcript")
async def attach_transcript(
    job_id: str,
    transcript: UploadFile = File(...),
    render: bool = Form(default=True),
    target_seconds: int | None = Form(default=None),
):
    d = _job_dir(job_id)
    meta_path = d / "job_meta.json"
    if not meta_path.exists():
        raise HTTPException(404, detail="job not found")
    meta = _read_json(meta_path)
    if meta.get("status") != "needs_transcript":
        raise HTTPException(400, detail="job is not waiting for transcript")
    # save transcript under uploads
    # find video in uploads (first matching ALLOWED_VIDEO)
    # run_pipeline(video=..., transcript_path=..., out_dir=d, render=...)
    # update meta success | success_partial | failed
    # return get_job(job_id)
```

- [ ] **Step 5: list/get meta 字段**

确保 list 返回的 meta 含 `has_video`, `has_final`, `status`（写入 create/attach 时）。  
`get_job` files 标志保持并增加可靠 `final`。

- [ ] **Step 6: 跑测试 GREEN**

```bat
set PYTHONPATH=src
python -m pytest tests\test_web_api.py -v
```

Expected: **全部 PASS**。

- [ ] **Step 7: 全量回归**

```bat
python -m pytest -q
```

Expected: 全绿。

- [ ] **Step 8: Commit**

```text
feat: video-first web jobs with needs_transcript attach
```

---

### Task 3: 前端视频优先 + 补传 + 历史展示

**Files:**
- Modify: `clothing-live-clipper/src/clipper/static/index.html`
- Modify: `clothing-live-clipper/src/clipper/static/app.js`
- Modify: `clothing-live-clipper/src/clipper/static/styles.css`

**Interfaces:**
- Consumes: API from Task 2
- Produces: UI 可完成：上传视频、见 needs_transcript、补传、预览/下载、点历史回看

- [ ] **Step 1: 改 index.html 文案与字段顺序**

要点：

- hero 副文案：视频为主，转写可选/可后补  
- 表单顺序：`video` 在上（主），`transcript` 可选在下  
- 结果区增加：

```html
<div id="attach-panel" class="attach" hidden>
  <h3>待补转写</h3>
  <p class="hint">已保存视频。上传 .json/.srt 后继续处理。</p>
  <input type="file" id="attach-transcript" accept=".json,.srt,application/json" />
  <button type="button" class="btn primary" id="attach-btn">继续处理</button>
  <p class="error" id="attach-error" hidden></p>
</div>
```

- [ ] **Step 2: app.js — 提交逻辑**

- FormData：始终可只带 video  
- 去掉「无转写且无 sample 就前端拦截」的硬阻挡；改为允许只传视频  
- 仍：无 video 且无 transcript 且无 sample → 前端提示错误  
- `renderJob`：若 `status === 'needs_transcript'` 显示 `attach-panel`，隐藏或清空 video 预览  
- 其他状态隐藏 attach-panel  

- [ ] **Step 3: app.js — 补传**

```javascript
async function attachTranscript(jobId) {
  const input = $("attach-transcript");
  if (!input.files || !input.files[0]) throw new Error("请选择转写文件");
  const fd = new FormData();
  fd.append("transcript", input.files[0]);
  fd.append("render", $("render").checked ? "true" : "false");
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/transcript`, {
    method: "POST",
    body: fd,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "补传失败");
  renderJob(data);
  await loadJobs();
}
```

绑定 `#attach-btn`；当前 jobId 可用 `data-job-id` 写在 result-panel。

- [ ] **Step 4: 历史列表字段**

显示 `has_final` / `has_video` / 状态中文映射：

```javascript
const STATUS_LABEL = {
  needs_transcript: "待补转写",
  processing: "处理中",
  success: "完成",
  success_partial: "部分完成",
  failed: "失败",
};
```

- [ ] **Step 5: health 展示 asr_configured**

在 `loadHealth` 增加一行 ASR：未配置/未实现。

- [ ] **Step 6: CSS**

`.chip.needs`, `.attach` 边框/背景与 `.job-item` 状态色少量样式。

- [ ] **Step 7: 手工冒烟（无浏览器自动化则检查清单）**

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787
```

Checklist（写入 PR/commit 说明即可）：

1. 打开 `/` 见视频主字段  
2. 仅上传任意小 mp4 → 状态待补转写  
3. 补传 `tests/fixtures/sample_transcript.json` → 出 plan  
4. 历史可点回  

- [ ] **Step 8: Commit**

```text
feat: web UI video-first attach transcript flow
```

---

### Task 4: README + .env.example + 收尾回归

**Files:**
- Modify: `clothing-live-clipper/README.md`
- Modify: `clothing-live-clipper/.env.example`
- Optional: `clothing-live-clipper/src/clipper/cli.py` 增加 `web` 子命令启动说明（可选，非必须）

**Interfaces:**
- Produces: 用户可按 README 启动并理解视频优先路径

- [ ] **Step 1: README 增加 Web 章节**

```markdown
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
```

并修正文首「不做 Web」过时表述。

- [ ] **Step 2: .env.example 增加**

```text
CLIPPER_ASR_ENABLED=false
CLIPPER_ASR_PROVIDER=none
```

- [ ] **Step 3: 全量测试**

```bat
set PYTHONPATH=src
python -m pytest -q
```

Expected: 全绿。

- [ ] **Step 4: Commit**

```text
docs: web workstation usage and ASR env stubs
```

- [ ] **Step 5: Push（若用户已要求同步 GitHub）**

```bat
cd /d C:\Users\MR\AppData\grok
git push origin master
```

---

## Self-Review (plan author)

### Spec coverage

| Spec | Task |
|------|------|
| 视频优先 IA / UI | T3 |
| needs_transcript 状态 | T2 |
| 补传 API | T2 + T3 |
| 自动落盘 web_jobs | 已有目录 + T2 保持 |
| 预览/下载 | 已有 files API + T3 展示 |
| health.asr_configured | T1/T2 |
| ASR 不假转写中 | T2 asr_status implemented=False |
| README 启动 | T4 |
| 测试场景 | T1/T2 |

### Placeholder scan

无 TBD；补传与 create 分支有明确伪代码与测试。

### Consistency

- 状态名与规格一致  
- 端口 8787  
- JOBS_DIR monkeypatch 方式固定  

### Out of scope

真实 ASR、异步队列、收藏片库、公网鉴权。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-18-clothing-live-clipper-web.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每任务子代理 + 审查  
2. **Inline Execution** — 本会话 executing-plans  

**Which approach?**
