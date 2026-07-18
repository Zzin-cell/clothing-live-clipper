const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  queued: "排队中",
  claimed: "Agent处理中",
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
  if (status === "queued" || status === "claimed") return "needs";
  if (status === "needs_transcript") return "needs";
  if (status === "failed") return "warn";
  if (status === "success_partial" || status === "processing") return "warn";
  return "";
}

function applyLights(lights) {
  const root = $("status-lights");
  if (!root || !lights) return;
  root.querySelectorAll(".light").forEach((el) => {
    const k = el.getAttribute("data-k");
    const v = lights[k] || "yellow";
    el.classList.remove("green", "yellow", "red");
    el.classList.add(v);
  });
}

function row(label, ok, detail) {
  const cls = ok === true ? "ok" : ok === false ? "bad" : "warn";
  const mark = ok === true ? "✓" : ok === false ? "!" : "·";
  return `<div class="check-row ${cls}"><span class="mark">${mark}</span><div><strong>${escapeHtml(
    label
  )}</strong><div class="muted">${escapeHtml(detail || "")}</div></div></div>`;
}

function renderStatus(st) {
  window.__lastStatus = st;
  applyLights(st.lights || {});
  const banner = $("setup-banner");
  if (banner) {
    banner.hidden = false;
    banner.innerHTML =
      "Web 只负责提交。上传后状态为<strong>排队中</strong>，请在 Agent 对话发送：<code>处理队列</code>";
  }

  const el = $("health");
  if (el) {
    el.className = "pill-status ok";
    el.innerHTML = "<strong>提交台就绪</strong>";
    el.title = `ffmpeg: ${st.ffmpeg?.ok ? "ok" : "no"} · 处理请 Agent 执行 skill`;
  }

  const list = [];
  list.push(row("Web 服务", st.service?.ok, `${st.service?.host}:${st.service?.port}`));
  list.push(row("ffmpeg", st.ffmpeg?.ok, st.ffmpeg?.version || st.ffmpeg?.path || "未找到"));
  list.push(row("ffprobe", st.ffprobe?.ok, st.ffprobe?.path || "可选"));
  list.push(
    row(
      "Whisper 听写",
      st.asr?.ok === true ? true : st.asr?.configured ? null : false,
      st.asr?.configured
        ? `${st.asr.model} · ${st.asr.base_url} · ${st.asr.source || ""}${
            st.asr.ok === true ? " · 探测通过" : st.asr.ok === false ? " · 探测失败" : ""
          }`
        : st.asr?.note || "未配置 API Key"
    )
  );
  list.push(
    row(
      "LLM（可选）",
      st.llm?.ok === true ? true : st.llm?.configured ? null : null,
      st.llm?.configured ? `${st.llm.model}` : "未配置（规则降级）"
    )
  );
  list.push(
    row(
      "输出目录",
      st.storage?.ok,
      `${st.storage?.path || ""} · 剩余 ${st.storage?.free_gb ?? "?"} GB`
    )
  );
  list.push(
    row(
      "最近任务",
      st.recent_health?.ok,
      st.recent_health?.failed_count
        ? `失败 ${st.recent_health.failed_count} 条`
        : "近期正常"
    )
  );
  const cl = $("checklist");
  if (cl) cl.innerHTML = list.join("");

  const compat = $("compat-box");
  if (compat && st.compat) {
    compat.innerHTML = [
      `<div><strong>听写 ASR</strong><ul>${(st.compat.asr || [])
        .map((x) => `<li>${escapeHtml(x)}</li>`)
        .join("")}</ul></div>`,
      `<div><strong>对话 LLM</strong><ul>${(st.compat.llm || [])
        .map((x) => `<li>${escapeHtml(x)}</li>`)
        .join("")}</ul></div>`,
    ].join("");
  }

  const env = $("env-box");
  if (env) {
    env.textContent = JSON.stringify(
      {
        python: st.deps?.python,
        platform: st.deps?.platform,
        packages: st.deps?.packages,
        storage: st.storage,
        ffmpeg: st.ffmpeg,
        config: st.config,
        checked_at: st.checked_at,
      },
      null,
      2
    );
  }

  const rj = $("recent-jobs");
  if (rj) {
    const jobs = st.recent_jobs || [];
    if (!jobs.length) rj.innerHTML = "<div class='empty'>暂无任务</div>";
    else {
      rj.innerHTML = jobs
        .map((j) => {
          return `<div class="job-item" data-id="${escapeHtml(j.job_id)}">
            <div>
              <div class="id">${escapeHtml(j.job_id)}</div>
              <div class="muted">${escapeHtml(j.status || "")} ${
            j.error ? "· " + escapeHtml(j.error) : ""
          }</div>
            </div>
            <div class="muted">${escapeHtml(j.created_at || "")}</div>
          </div>`;
        })
        .join("");
      rj.querySelectorAll(".job-item").forEach((n) =>
        n.addEventListener("click", () => {
          closeSettings();
          showJob(n.dataset.id);
        })
      );
    }
  }

  // fill config form defaults
  const cfg = st.config || {};
  if ($("cfg-base-url") && !$("cfg-base-url").dataset.touched) {
    $("cfg-base-url").value = cfg.base_url || "";
  }
  if ($("cfg-asr-model") && !$("cfg-asr-model").dataset.touched) {
    $("cfg-asr-model").value = cfg.asr_model || "whisper-1";
  }
  if ($("cfg-llm-model") && !$("cfg-llm-model").dataset.touched) {
    $("cfg-llm-model").value = cfg.llm_model || "gpt-4o-mini";
  }
  if ($("cfg-key-hint")) {
    $("cfg-key-hint").textContent = cfg.has_api_key
      ? `已配置 · 末尾 ${cfg.api_key_hint || "****"} · 来源 ${cfg.source || ""}`
      : "未配置 API Key";
  }
  if ($("cfg-llm-enabled")) $("cfg-llm-enabled").checked = cfg.llm_enabled !== false;
}

async function loadSystemStatus() {
  const res = await fetch("/api/system/status");
  if (!res.ok) throw new Error("status failed");
  const st = await res.json();
  renderStatus(st);
  return st;
}

async function loadHealth() {
  try {
    await loadSystemStatus();
  } catch (e) {
    const el = $("health");
    if (el) {
      el.className = "pill-status bad";
      el.textContent = "无法连接后端";
      el.title = String(e);
    }
  }
}

function openSettings() {
  $("drawer-backdrop").hidden = false;
  $("settings-drawer").hidden = false;
  document.body.classList.add("drawer-open");
  loadSystemStatus().catch(() => {});
}

function closeSettings() {
  $("drawer-backdrop").hidden = true;
  $("settings-drawer").hidden = true;
  document.body.classList.remove("drawer-open");
}

function setupSettings() {
  $("open-settings")?.addEventListener("click", openSettings);
  $("close-settings")?.addEventListener("click", closeSettings);
  $("drawer-backdrop")?.addEventListener("click", closeSettings);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSettings();
  });

  document.querySelectorAll(".drawer-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".drawer-tabs .tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      const id = "tab-" + tab.dataset.tab;
      $(id)?.classList.add("active");
    });
  });

  ["cfg-base-url", "cfg-asr-model", "cfg-llm-model", "cfg-api-key"].forEach((id) => {
    $(id)?.addEventListener("input", () => {
      $(id).dataset.touched = "1";
    });
  });

  $("btn-refresh-status")?.addEventListener("click", () => loadSystemStatus());
  $("btn-probe-whisper")?.addEventListener("click", async () => {
    const res = await fetch("/api/system/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "whisper" }),
    });
    const data = await res.json();
    if (data.status) renderStatus(data.status);
    alert(
      data.probe?.ok
        ? "Whisper 探测通过"
        : "Whisper 探测失败：" + (data.probe?.error || data.probe?.detail || "")
    );
  });
  $("btn-probe-llm")?.addEventListener("click", async () => {
    const res = await fetch("/api/system/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "llm" }),
    });
    const data = await res.json();
    if (data.status) renderStatus(data.status);
    alert(
      data.probe?.ok
        ? "LLM 探测通过"
        : "LLM 探测失败：" + (data.probe?.error || data.probe?.detail || "")
    );
  });

  $("config-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("cfg-error");
    const ok = $("cfg-ok");
    err.hidden = true;
    ok.hidden = true;
    const body = {
      persist: !!$("cfg-persist")?.checked,
      base_url: $("cfg-base-url")?.value || undefined,
      asr_model: $("cfg-asr-model")?.value || undefined,
      llm_model: $("cfg-llm-model")?.value || undefined,
      llm_enabled: !!$("cfg-llm-enabled")?.checked,
      asr_enabled: true,
      asr_provider: "openai_whisper",
    };
    const key = ($("cfg-api-key")?.value || "").trim();
    if (key) body.api_key = key;
    try {
      const res = await fetch("/api/system/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "保存失败");
      if (data.status) renderStatus(data.status);
      if ($("cfg-api-key")) $("cfg-api-key").value = "";
      ok.hidden = false;
      ok.textContent = body.persist ? "已保存到本机 .env" : "已应用到当前会话";
    } catch (ex) {
      err.hidden = false;
      err.textContent = String(ex.message || ex);
    }
  });
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
  if (data.queue_hint && (status === "queued" || status === "claimed")) {
    chips.push(`<span class="chip needs">${escapeHtml(data.queue_hint)}</span>`);
  }
  if (data.process_mode) {
    chips.push(`<span class="chip">模式 ${escapeHtml(data.process_mode)}</span>`);
  }
  if (data.transcript_source) {
    chips.push(
      `<span class="chip">口播 ${escapeHtml(
        data.transcript_source === "whisper_api" ? "智能听写" : String(data.transcript_source)
      )}</span>`
    );
  }
  if (status !== "queued" && status !== "claimed") {
    chips.push(
      `<span class="chip ${data.golden20_passed ? "ok" : "warn"}">黄金20 ${
        data.golden20_passed ? "通过" : "待审"
      }</span>`
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
  if (status === "queued") {
    $("review-md").textContent =
      "任务已入队。\n\n请在 Agent 对话发送：\n  处理队列\n\nAgent 将调用 clothing-live-clip skill 完成智能口播打轴与切片，完成后此处自动可刷新查看。";
  } else if (status === "claimed") {
    $("review-md").textContent = "Agent 正在处理，请稍候后点刷新…";
  } else {
    $("review-md").textContent = data.review_md || "（无 review）";
  }
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
    btn.textContent = "上传中…";

    try {
      const fd = new FormData();
      fd.append("use_sample", useSample && useSample.checked ? "true" : "false");
      fd.append("target_seconds", $("target_seconds").value || "60");
      fd.append("render", $("render").checked ? "true" : "false");
      fd.append("mode", "agent");

      const video = $("video");
      const hasVideo = !!(video.files && video.files[0]);
      const hasTranscript = !!(transcript && transcript.files && transcript.files[0]);

      if (!hasVideo && !(useSample && useSample.checked) && !hasTranscript) {
        throw new Error("请上传直播视频");
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
      btn.textContent = "上传并排队";
    }
  });
}

$("refresh-jobs").addEventListener("click", loadJobs);

// Auto-refresh while waiting for Agent
setInterval(() => {
  const st = $("result-panel")?.dataset?.jobId;
  const panelHidden = $("result-panel")?.hidden;
  if (!st || panelHidden) return;
  const chips = $("stats")?.textContent || "";
  if (chips.includes("排队") || chips.includes("Agent处理")) {
    showJob(st).catch(() => {});
    loadJobs().catch(() => {});
  }
}, 5000);

loadHealth();
loadJobs();
setupForm();
setupSettings();
