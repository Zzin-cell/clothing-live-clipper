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
    const asrOk = data.asr_configured === true;
    el.className = "pill-status " + (data.ok && asrOk ? "ok" : data.ok ? "warn" : "bad");
    if (!asrOk && data.ok) {
      // soften: keep pill-status without .ok
      el.className = "pill-status";
    }
    el.title = [
      `服务：${data.ok ? "正常" : "异常"}`,
      `ffmpeg：${data.ffmpeg ? "已检测到" : "未找到"}`,
      `智能口播 ASR：${asrOk ? "已配置" : data.asr_note || "未配置 API Key"}`,
    ].join("\n");
    el.innerHTML = `<strong>${asrOk ? "可自动听写" : "待配置 ASR"}</strong> · ${
      data.ffmpeg ? "ffmpeg✓" : "ffmpeg·"
    } · ${asrOk ? "Whisper✓" : "Whisper·"}`;
  } catch (e) {
    el.className = "pill-status bad";
    el.title = String(e);
    el.textContent = "无法连接后端";
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
  if (data.transcript_source) {
    chips.push(
      `<span class="chip">口播 ${escapeHtml(
        data.transcript_source === "whisper_api" ? "智能听写" : String(data.transcript_source)
      )}</span>`
    );
  }
  chips.push(
    `<span class="chip ${data.golden20_passed ? "ok" : "warn"}">黄金20 ${data.golden20_passed ? "通过" : "待审"}</span>`
  );
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
  if (attach) attach.hidden = true;

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
  if (files.transcript_asr) {
    actions.push(
      `<a class="btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/transcript_asr.json" target="_blank">智能口播</a>`
    );
  } else if (files.transcript) {
    actions.push(
      `<a class="btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/transcript.json" target="_blank">口播轴</a>`
    );
  }
  if (files.final) {
    actions.push(
      `<a class="btn primary" style="width:auto" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4" download>下载 final.mp4</a>`
    );
  }
  $("actions").innerHTML = actions.join("") || "<span class='muted'>无文件</span>";

  const video = $("preview");
  if (files.final) {
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
  $("review-md").textContent = data.review_md || "（无 review）";
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

  if (useSample && transcript) {
    useSample.addEventListener("change", () => {
      transcript.disabled = useSample.checked;
      if (useSample.checked) transcript.value = "";
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    err.textContent = "";
    btn.disabled = true;
    btn.textContent = "智能口播处理中…";

    try {
      const fd = new FormData();
      fd.append("use_sample", useSample && useSample.checked ? "true" : "false");
      fd.append("target_seconds", $("target_seconds").value || "60");
      fd.append("render", $("render").checked ? "true" : "false");

      const video = $("video");
      const hasVideo = !!(video.files && video.files[0]);
      const hasTranscript = !!(transcript && transcript.files && transcript.files[0]);

      if (!hasVideo && !(useSample && useSample.checked) && !hasTranscript) {
        throw new Error("请上传直播视频（将自动智能口播打轴）");
      }

      if (hasVideo) {
        fd.append("video", video.files[0]);
      }
      if (transcript && !(useSample && useSample.checked) && hasTranscript) {
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
      btn.textContent = "上传并自动切片";
    }
  });
}

$("refresh-jobs").addEventListener("click", loadJobs);

loadHealth();
loadJobs();
setupForm();
