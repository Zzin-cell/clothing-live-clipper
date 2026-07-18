const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  needs_transcript: "待补转写",
  processing: "处理中",
  success: "完成",
  success_partial: "部分完成",
  failed: "失败",
};

function slotHtml(slots) {
  if (!slots || !slots.length) return "<li class='muted'>（空）</li>";
  return slots
    .map((s) => {
      const sec0 = (s.t0_ms / 1000).toFixed(1);
      const sec1 = (s.t1_ms / 1000).toFixed(1);
      const score = typeof s.score === "number" ? s.score.toFixed(0) : "-";
      return `<li>${escapeHtml(s.text)}<span class="meta">${sec0}s–${sec1}s · score ${score} · ${escapeHtml(s.role || "")}</span></li>`;
    })
    .join("");
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusChipClass(status) {
  if (status === "success") return "ok";
  if (status === "needs_transcript") return "needs";
  if (status === "failed") return "warn";
  if (status === "success_partial" || status === "processing") return "warn";
  return "";
}

async function loadHealth() {
  const el = $("health");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    el.className = "health " + (data.ok ? "ok" : "bad");
    const asr =
      data.asr_configured === true
        ? "已配置"
        : data.asr_configured === false
          ? "未配置/未实现"
          : "未配置/未实现";
    el.innerHTML = [
      `<div><strong>服务</strong>：正常</div>`,
      `<div><strong>ffmpeg</strong>：${data.ffmpeg ? "已检测到" : "未找到（只能出计划）"}</div>`,
      `<div><strong>示例转写</strong>：${data.sample_transcript ? "可用" : "缺失"}</div>`,
      `<div><strong>ASR</strong>：${asr}</div>`,
    ].join("");
  } catch (e) {
    el.className = "health bad";
    el.textContent = "无法连接后端：" + e;
  }
}

async function loadJobs() {
  const box = $("job-list");
  try {
    const res = await fetch("/api/jobs");
    const data = await res.json();
    const jobs = data.jobs || [];
    if (!jobs.length) {
      box.innerHTML = "<div class='empty'>暂无历史任务</div>";
      return;
    }
    box.innerHTML = jobs
      .map((j) => {
        const status = j.status || "?";
        const label = STATUS_LABEL[status] || status;
        const dur = j.duration_s != null ? `${Number(j.duration_s).toFixed(1)}s` : "-";
        const g20 = j.golden20_passed ? "黄金20✓" : "黄金20·";
        const flags = [
          j.has_video ? "有视频" : "无视频",
          j.has_final ? "有成片" : "无成片",
        ].join(" · ");
        const statusClass = statusChipClass(status);
        return `<div class="job-item ${statusClass ? "status-" + statusClass : ""}" data-id="${escapeHtml(j.job_id)}">
          <div>
            <div class="id">${escapeHtml(j.job_id)}</div>
            <div class="muted"><span class="chip ${statusClass}">${escapeHtml(label)}</span> · ${dur} · ${g20} · ${flags}</div>
          </div>
          <div class="muted">${escapeHtml(j.created_at || "")}</div>
        </div>`;
      })
      .join("");
    box.querySelectorAll(".job-item").forEach((node) => {
      node.addEventListener("click", () => showJob(node.dataset.id));
    });
  } catch (e) {
    box.textContent = "加载失败：" + e;
  }
}

function renderJob(data) {
  $("result-empty").hidden = true;
  const panel = $("result-panel");
  panel.hidden = false;
  panel.dataset.jobId = data.job_id || "";

  const status = data.status || "";
  const label = STATUS_LABEL[status] || status;
  const chips = [];
  chips.push(`<span class="chip">任务 ${escapeHtml(data.job_id)}</span>`);
  chips.push(
    `<span class="chip ${statusChipClass(status)}">状态 ${escapeHtml(label)}</span>`
  );
  if (data.duration_s != null) {
    chips.push(`<span class="chip">成片规划 ${Number(data.duration_s).toFixed(1)}s</span>`);
  }
  if (status !== "needs_transcript") {
    chips.push(
      `<span class="chip ${data.golden20_passed ? "ok" : "warn"}">黄金20 ${data.golden20_passed ? "通过" : "待审"}</span>`
    );
  }
  if (data.selected_clips != null) {
    chips.push(`<span class="chip">选中 ${data.selected_clips} 段</span>`);
  }
  if (data.warnings && data.warnings.length) {
    chips.push(`<span class="chip warn">警告 ${escapeHtml(data.warnings.join(", "))}</span>`);
  }
  if (data.render_skipped) {
    chips.push(
      `<span class="chip warn">未渲染 ${escapeHtml(data.render_error || "")}</span>`
    );
  }
  if (data.error) {
    chips.push(`<span class="chip warn">错误 ${escapeHtml(data.error)}</span>`);
  }
  $("stats").innerHTML = chips.join("");

  const attach = $("attach-panel");
  const attachErr = $("attach-error");
  if (status === "needs_transcript") {
    attach.hidden = false;
    attachErr.hidden = true;
    attachErr.textContent = "";
  } else {
    attach.hidden = true;
  }

  const files = data.files || {};
  const actions = [];
  if (files.plan) {
    actions.push(
      `<a class="btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/plan.json" target="_blank">plan.json</a>`
    );
  }
  if (files.review) {
    actions.push(
      `<a class="btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/review.md" target="_blank">review.md</a>`
    );
  }
  if (files.clips) {
    actions.push(
      `<a class="btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/clips.json" target="_blank">clips.json</a>`
    );
  }
  if (files.final) {
    actions.push(
      `<a class="btn primary" style="width:auto" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4" download>下载 final.mp4</a>`
    );
  }
  $("actions").innerHTML =
    actions.join("") ||
    (status === "needs_transcript"
      ? "<span class='muted'>等待补传转写后继续</span>"
      : "<span class='muted'>无文件</span>");

  const video = $("preview");
  if (status === "needs_transcript") {
    video.hidden = true;
    video.removeAttribute("src");
  } else if (files.final) {
    video.hidden = false;
    video.src = `/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4?t=${Date.now()}`;
  } else {
    video.hidden = true;
    video.removeAttribute("src");
  }

  const plan = data.plan || {};
  $("golden-list").innerHTML = slotHtml(plan.golden);
  $("trust-list").innerHTML = slotHtml(plan.trust);
  $("cta-list").innerHTML = slotHtml(plan.cta);
  $("review-md").textContent =
    status === "needs_transcript"
      ? "（待补转写后生成）"
      : data.review_md || "（无 review）";
}

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

async function showJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || "加载任务失败");
    return;
  }
  const data = await res.json();
  renderJob(data);
}

function setupForm() {
  const form = $("job-form");
  const useSample = $("use_sample");
  const transcript = $("transcript");
  const err = $("form-error");
  const btn = $("submit-btn");

  useSample.addEventListener("change", () => {
    transcript.disabled = useSample.checked;
    if (useSample.checked) transcript.value = "";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    err.textContent = "";
    btn.disabled = true;
    btn.textContent = "处理中…";

    try {
      const fd = new FormData();
      fd.append("use_sample", useSample.checked ? "true" : "false");
      fd.append("target_seconds", $("target_seconds").value || "60");
      fd.append("render", $("render").checked ? "true" : "false");

      const video = $("video");
      const hasVideo = !!(video.files && video.files[0]);
      const hasTranscript = !!(transcript.files && transcript.files[0]);

      // Allow video-only; block only when nothing useful is provided
      if (!hasVideo && !useSample.checked && !hasTranscript) {
        throw new Error("请上传视频、转写文件，或勾选使用示例转写");
      }

      if (hasVideo) {
        fd.append("video", video.files[0]);
      }
      if (!useSample.checked && hasTranscript) {
        fd.append("transcript", transcript.files[0]);
      }

      const res = await fetch("/api/jobs", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || res.statusText || "创建失败");
      }
      renderJob(data);
      await loadJobs();
    } catch (ex) {
      err.hidden = false;
      err.textContent = String(ex.message || ex);
    } finally {
      btn.disabled = false;
      btn.textContent = "开始切片";
    }
  });
}

function setupAttach() {
  const btn = $("attach-btn");
  const err = $("attach-error");
  btn.addEventListener("click", async () => {
    err.hidden = true;
    err.textContent = "";
    const jobId = $("result-panel").dataset.jobId;
    if (!jobId) {
      err.hidden = false;
      err.textContent = "无当前任务";
      return;
    }
    btn.disabled = true;
    btn.textContent = "处理中…";
    try {
      await attachTranscript(jobId);
    } catch (ex) {
      err.hidden = false;
      err.textContent = String(ex.message || ex);
    } finally {
      btn.disabled = false;
      btn.textContent = "继续处理";
    }
  });
}

$("refresh-jobs").addEventListener("click", loadJobs);

loadHealth();
loadJobs();
setupForm();
setupAttach();
