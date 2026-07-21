const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  queued: "排队中",
  starting: "启动中",
  processing: "处理中",
  claimed: "处理中",
  success: "完成",
  success_partial: "部分完成",
  failed: "失败",
};

let currentJobId = null;
let pollTimer = null;

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusClass(status) {
  if (status === "success") return "ok";
  if (status === "failed") return "bad";
  if (status === "success_partial") return "warn";
  return "warn";
}

function stageLabel(stage) {
  const map = {
    queued: "排队",
    starting: "启动",
    extract_audio: "抽音频",
    asr: "智能口播打轴",
    filter: "过滤无效词",
    clipper: "卖点排序",
    render: "渲染成片",
    done: "完成",
    failed: "失败",
  };
  return map[stage] || stage || "处理中";
}

async function loadHealth() {
  const el = $("health");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const ok = !!data.ok && !!data.ffmpeg;
    el.className = "jy-pill " + (ok ? "ok" : "bad");
    el.textContent = ok
      ? `本机就绪 · ffmpeg${data.ffmpeg ? "✓" : "·"} · 自动切片`
      : `环境异常 · ffmpeg${data.ffmpeg ? "✓" : "缺失"}`;
  } catch (e) {
    el.className = "jy-pill bad";
    el.textContent = "无法连接后端";
  }
}

function renderTracks(plan) {
  const mk = (arr, role) => {
    if (!arr || !arr.length) return '<div class="jy-empty" style="padding:8px">（空）</div>';
    return arr
      .map((s) => {
        const a = (s.t0_ms / 1000).toFixed(1);
        const b = (s.t1_ms / 1000).toFixed(1);
        return `<div class="jy-clip ${role}"><div>${escapeHtml(s.text || "")}</div><div class="meta">${a}s–${b}s</div></div>`;
      })
      .join("");
  };
  $("golden-track").innerHTML = mk(plan.golden, "hook");
  $("trust-track").innerHTML = mk(plan.trust, "trust");
  $("cta-track").innerHTML = mk(plan.cta, "cta");
}

async function loadTranscript(jobId) {
  const box = $("transcript-list");
  $("asr-count").textContent = "—";
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/files/transcript_for_clipper.json`);
    if (!res.ok) {
      // fallback asr
      const res2 = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/files/transcript_asr.json`);
      if (!res2.ok) {
        box.innerHTML = '<div class="jy-empty">口播尚未生成</div>';
        return;
      }
      const raw = await res2.json();
      $("asr-count").textContent = `${raw.length || 0} 句`;
      box.innerHTML = (raw || [])
        .slice(0, 80)
        .map((u) => {
          const a = ((u.t0_ms || 0) / 1000).toFixed(1);
          const b = ((u.t1_ms || 0) / 1000).toFixed(1);
          return `<div class="jy-line"><div class="t">${a}s–${b}s</div>${escapeHtml(u.text || "")}</div>`;
        })
        .join("");
      return;
    }
    const kept = await res.json();
    $("asr-count").textContent = `保留 ${kept.length || 0} 句`;
    box.innerHTML = (kept || [])
      .map((u) => {
        const a = ((u.t0_ms || 0) / 1000).toFixed(1);
        const b = ((u.t1_ms || 0) / 1000).toFixed(1);
        return `<div class="jy-line keep"><div class="t">${a}s–${b}s</div>${escapeHtml(u.text || "")}</div>`;
      })
      .join("") || '<div class="jy-empty">无保留句子</div>';
  } catch {
    box.innerHTML = '<div class="jy-empty">口播加载失败</div>';
  }
}

function renderJob(data) {
  currentJobId = data.job_id;
  $("current-job-title").textContent = data.video_source || data.job_id;
  const st = data.status || "";
  $("current-job-status").textContent = `${STATUS_LABEL[st] || st}${
    data.final_duration_s ? ` · ${data.final_duration_s}s` : ""
  }`;

  // progress
  const pb = $("progress-block");
  const processing = ["queued", "processing", "starting", "claimed"].includes(st);
  if (processing) {
    pb.hidden = false;
    const pct = Number(data.progress || (st === "queued" ? 2 : 15));
    $("progress-bar").style.width = `${pct}%`;
    $("progress-text").textContent = `${pct}%`;
    $("stage-text").textContent = stageLabel(data.stage) + (data.stage_detail ? ` · ${data.stage_detail}` : "");
  } else {
    pb.hidden = st !== "failed";
    if (st === "failed") {
      pb.hidden = false;
      $("progress-bar").style.width = "100%";
      $("progress-text").textContent = "失败";
      $("stage-text").textContent = data.error || "处理失败";
    }
  }

  // video
  const video = $("preview");
  const files = data.files || {};
  if (files.final) {
    video.src = `/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4?t=${Date.now()}`;
    $("export-btn").disabled = false;
    $("export-btn").onclick = () => {
      window.open(`/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4`, "_blank");
    };
  } else {
    video.removeAttribute("src");
    $("export-btn").disabled = true;
  }

  // tracks + review
  renderTracks(data.plan || {});
  if (st === "failed") {
    $("review-md").textContent = `失败：${data.error || "未知错误"}`;
  } else if (data.review_md) {
    $("review-md").textContent = data.review_md;
  } else if (processing) {
    $("review-md").textContent = "正在自动处理：听写打轴 → 过滤 → 排序 → 渲染…";
  } else {
    $("review-md").textContent = "暂无摘要";
  }

  // actions
  const actions = [];
  if (files.final) {
    actions.push(`<a class="jy-btn primary" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4" download>下载 final.mp4</a>`);
  }
  if (files.plan) {
    actions.push(`<a class="jy-btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/plan.json" target="_blank">plan.json</a>`);
  }
  if (files.review) {
    actions.push(`<a class="jy-btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/review.md" target="_blank">review.md</a>`);
  }
  if (st === "failed") {
    actions.push(`<button type="button" class="jy-btn" id="retry-btn">重试</button>`);
  }
  if (files.transcript || files.transcript_asr || data.status === "success" || data.status === "success_partial") {
    actions.push(`<button type="button" class="jy-btn" id="open-tr-inline">编辑口播稿</button>`);
  }
  $("actions").innerHTML = actions.join("") || '<span class="muted">暂无导出</span>';
  const retry = $("retry-btn");
  if (retry) {
    retry.onclick = async () => {
      await fetch(`/api/jobs/${encodeURIComponent(data.job_id)}/retry`, { method: "POST" });
      pollJob(data.job_id);
    };
  }
  const openTr = $("open-tr-inline");
  if (openTr) openTr.onclick = () => openTranscriptDrawer();

  loadTranscript(data.job_id);
  highlightJob(data.job_id);
}

function highlightJob(id) {
  document.querySelectorAll(".jy-job").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === id);
  });
}

async function showJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  if (!res.ok) return;
  const data = await res.json();
  renderJob(data);
  if (["queued", "processing", "starting", "claimed"].includes(data.status)) {
    pollJob(jobId);
  } else if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) return;
      const data = await res.json();
      renderJob(data);
      loadJobs();
      if (!["queued", "processing", "starting", "claimed"].includes(data.status)) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (_) {}
  }, 2000);
}

async function loadJobs() {
  const box = $("job-list");
  try {
    const res = await fetch("/api/jobs?limit=30");
    const data = await res.json();
    const jobs = data.jobs || [];
    if (!jobs.length) {
      box.innerHTML = '<div class="jy-empty">暂无任务</div>';
      return;
    }
    box.innerHTML = jobs
      .map((j) => {
        const st = j.status || "";
        const cls = statusClass(st);
        return `<div class="jy-job" data-id="${escapeHtml(j.job_id)}">
          <div class="id">${escapeHtml(j.job_id)}</div>
          <div class="st ${cls}">${escapeHtml(STATUS_LABEL[st] || st)}${
            j.progress != null && ["processing", "starting", "queued"].includes(st)
              ? ` · ${j.progress}%`
              : ""
          }${j.final_duration_s ? ` · ${j.final_duration_s}s` : ""}</div>
          <div class="st">${escapeHtml(j.video_source || "")}</div>
        </div>`;
      })
      .join("");
    box.querySelectorAll(".jy-job").forEach((el) => {
      el.addEventListener("click", () => showJob(el.dataset.id));
    });
    if (currentJobId) highlightJob(currentJobId);
  } catch (e) {
    box.textContent = "加载失败：" + e;
  }
}

function setupForm() {
  const form = $("job-form");
  const fileInput = $("video");
  const drop = $("drop-zone");
  const err = $("form-error");
  const btn = $("submit-btn");

  ["dragenter", "dragover"].forEach((ev) => {
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("drag");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove("drag");
    });
  });
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) {
      fileInput.files = e.dataTransfer.files;
      $("file-name").textContent = f.name;
    }
  });
  fileInput.addEventListener("change", () => {
    $("file-name").textContent = fileInput.files?.[0]?.name || "支持 mp4 / mov / mkv / webm";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    if (!fileInput.files?.[0]) {
      err.hidden = false;
      err.textContent = "请选择视频";
      return;
    }
    btn.disabled = true;
    btn.textContent = "上传并启动…";
    try {
      const fd = new FormData();
      fd.append("video", fileInput.files[0]);
      fd.append("target_seconds", $("target_seconds").value || "60");
      fd.append("render", $("render").checked ? "true" : "false");
      fd.append("auto_process", "true");
      const res = await fetch("/api/jobs", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "创建失败");
      renderJob(data);
      await loadJobs();
      pollJob(data.job_id);
    } catch (ex) {
      err.hidden = false;
      err.textContent = String(ex.message || ex);
    } finally {
      btn.disabled = false;
      btn.textContent = "开始智能切片";
    }
  });
}

let transcriptCache = [];

function openTranscriptDrawer() {
  $("drawer-backdrop").hidden = false;
  $("transcript-drawer").hidden = false;
  loadTranscriptEditor(currentJobId);
}

function closeTranscriptDrawer() {
  $("drawer-backdrop").hidden = true;
  $("transcript-drawer").hidden = true;
}

function setupTranscriptModule() {
  $("open-transcript")?.addEventListener("click", openTranscriptDrawer);
  $("close-transcript")?.addEventListener("click", closeTranscriptDrawer);
  $("drawer-backdrop")?.addEventListener("click", closeTranscriptDrawer);
  $("tr-reload")?.addEventListener("click", () => loadTranscriptEditor(currentJobId));
  $("tr-all")?.addEventListener("click", () => setAllKeep(true));
  $("tr-none")?.addEventListener("click", () => setAllKeep(false));
  $("tr-save")?.addEventListener("click", () => saveTranscript(false));
  $("tr-reclip")?.addEventListener("click", () => saveTranscript(true));
}

function setAllKeep(v) {
  transcriptCache.forEach((u) => (u.keep = v));
  renderTranscriptEditor();
}

function collectEditorItems() {
  const rows = Array.from(document.querySelectorAll(".tr-item"));
  const items = [];
  rows.forEach((row, i) => {
    const keep = !!row.querySelector(".tr-keep")?.checked;
    const text = (row.querySelector(".tr-text")?.value || "").trim();
    const t0 = Number(row.querySelector(".tr-t0")?.value || 0);
    const t1 = Number(row.querySelector(".tr-t1")?.value || 0);
    const utt_id = row.dataset.uid || `e${i:04d}`;
    items.push({ utt_id, text, t0_ms: t0, t1_ms: t1, keep });
  });
  return items;
}

function renderTranscriptEditor() {
  const box = $("tr-list");
  if (!transcriptCache.length) {
    box.innerHTML = '<div class="jy-empty">暂无口播句子（先完成自动听写）</div>';
    return;
  }
  box.innerHTML = transcriptCache
    .map((u, i) => {
      const keep = u.keep !== false;
      return `<div class="tr-item ${keep ? "" : "off"}" data-uid="${escapeHtml(u.utt_id || `u${i}`)}">
        <input class="tr-keep" type="checkbox" ${keep ? "checked" : ""} />
        <div class="tr-time">
          <input class="tr-t0" type="number" value="${Number(u.t0_ms || 0)}" title="开始 ms" />
          <input class="tr-t1" type="number" value="${Number(u.t1_ms || 0)}" title="结束 ms" />
        </div>
        <textarea class="tr-text">${escapeHtml(u.text || "")}</textarea>
      </div>`;
    })
    .join("");
  box.querySelectorAll(".tr-keep").forEach((ck) => {
    ck.addEventListener("change", () => {
      ck.closest(".tr-item")?.classList.toggle("off", !ck.checked);
    });
  });
}

async function loadTranscriptEditor(jobId) {
  const meta = $("tr-meta");
  const msg = $("tr-msg");
  msg.textContent = "";
  if (!jobId) {
    meta.textContent = "未选择任务";
    $("tr-list").innerHTML = '<div class="jy-empty">先处理一个视频，再编辑口播稿</div>';
    transcriptCache = [];
    return;
  }
  meta.textContent = `任务 ${jobId} · 加载中…`;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/transcript?kind=all`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "加载失败");
    transcriptCache = data.items || [];
    meta.textContent = `任务 ${jobId} · 共 ${transcriptCache.length} 句 · 已勾选将参与重剪`;
    renderTranscriptEditor();
  } catch (e) {
    meta.textContent = `任务 ${jobId}`;
    $("tr-list").innerHTML = `<div class="jy-empty">${escapeHtml(String(e.message || e))}</div>`;
  }
}

async function saveTranscript(reclip) {
  const msg = $("tr-msg");
  msg.textContent = "";
  if (!currentJobId) {
    msg.textContent = "请先选择任务";
    return;
  }
  const items = collectEditorItems();
  const kept = items.filter((x) => x.keep && x.text);
  if (!kept.length) {
    msg.textContent = "请至少勾选并保留一句口播";
    return;
  }
  msg.textContent = reclip ? "保存并重剪中…" : "保存中…";
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}/transcript`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, reclip: !!reclip }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "保存失败");
    msg.textContent = reclip ? "已保存，正在按口播重剪…" : `已保存 ${kept.length} 句`;
    renderJob(data);
    await loadJobs();
    if (reclip) pollJob(currentJobId);
  } catch (e) {
    msg.textContent = String(e.message || e);
  }
}

$("refresh-jobs")?.addEventListener("click", loadJobs);
loadHealth();
loadJobs();
setupForm();
setupTranscriptModule();
