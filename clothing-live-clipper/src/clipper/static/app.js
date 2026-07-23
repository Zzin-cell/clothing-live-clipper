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
let planEdit = null; // {golden, trust, cta}
let planOriginal = null;
let asrCards = []; // left transcript editable cards
let selectedAsr = new Set(); // selected asr indices for bulk add

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
    asr: "GPU 口播打轴（small/float16，通常十几秒到1分钟）",
    asr_done: "听写完成",
    filter: "过滤无效词",
    clipper: "卖点排序",
    reclip: "按口播重剪",
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
  // learning status
  try {
    const lr = await fetch("/api/learning/status");
    if (lr.ok) {
      const L = await lr.json();
      const n = Number(L.events || 0);
      if ($("learn-stat")) $("learn-stat").textContent = n > 0 ? `已学 ${n} 次` : "人机闭环";
      if ($("learn-hint")) {
        $("learn-hint").textContent =
          n > 0
            ? `已学习 ${n} 次人工反剪（保留 ${L.kept_slots || 0} / 丢弃 ${L.dropped_slots || 0}）。继续改结构会更像你。`
            : "你每次「保存口播并重剪」都会被记住，后续自动更像你的口味。";
      }
    }
  } catch (_) {}
}

function clonePlan(plan) {
  const copySlot = (s, role) => ({
    clip_id: s.clip_id || `${role}_${Math.random().toString(16).slice(2, 8)}`,
    role: s.role || role,
    t0_ms: Number(s.t0_ms || 0),
    t1_ms: Number(s.t1_ms || 0),
    text: s.text || "",
    score: Number(s.score || 0),
    removed: !!s.removed,
  });
  return {
    golden: (plan.golden || []).map((s) => copySlot(s, "hook")),
    trust: (plan.trust || []).map((s) => copySlot(s, "trust")),
    cta: (plan.cta || []).map((s) => copySlot(s, "cta")),
  };
}

function activeCount(plan) {
  if (!plan) return 0;
  return ["golden", "trust", "cta"]
    .map((k) => (plan[k] || []).filter((s) => !s.removed).length)
    .reduce((a, b) => a + b, 0);
}

function setPlanToolsEnabled(on) {
  if ($("plan-reset")) $("plan-reset").disabled = !on;
  if ($("plan-apply")) $("plan-apply").disabled = !on;
}

function updatePlanHint() {
  const el = $("plan-edit-hint");
  if (!el || !planEdit) {
    if (el) el.textContent = "";
    return;
  }
  const n = activeCount(planEdit);
  const total = ["golden", "trust", "cta"]
    .map((k) => (planEdit[k] || []).length)
    .reduce((a, b) => a + b, 0);
  const removed = total - n;
  el.textContent = removed > 0 ? `已删 ${removed} 段 · 保留 ${n} 段` : `共 ${n} 段，可点 × 删除`;
}

const TRACK_ORDER = ["golden", "trust", "cta"];

function roleLabel(trackKey) {
  return trackKey === "golden" ? "hook" : trackKey;
}

function moveClip(fromRole, fromIdx, toRole, toIdx) {
  if (!planEdit?.[fromRole]?.[fromIdx]) return;
  if (!planEdit[toRole]) planEdit[toRole] = [];
  const item = planEdit[fromRole].splice(fromIdx, 1)[0];
  item.role = roleLabel(toRole);
  const insertAt = Math.max(0, Math.min(toIdx, planEdit[toRole].length));
  planEdit[toRole].splice(insertAt, 0, item);
  renderTracks(planEdit);
}

function syncPlanFieldsFromDom() {
  if (!planEdit) return;
  document.querySelectorAll(".jy-clip").forEach((card) => {
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    if (!planEdit?.[role]?.[idx]) return;
    const ta = card.querySelector(".clip-text-edit");
    const t0 = card.querySelector(".clip-t0");
    const t1 = card.querySelector(".clip-t1");
    if (ta) planEdit[role][idx].text = ta.value;
    if (t0) planEdit[role][idx].t0_ms = Math.max(0, Number(t0.value || 0));
    if (t1) {
      const v1 = Math.max(0, Number(t1.value || 0));
      const v0 = planEdit[role][idx].t0_ms;
      planEdit[role][idx].t1_ms = v1 > v0 ? v1 : v0 + 500;
    }
  });
}

function renderTracks(plan) {
  // keep in-progress edits when re-rendering same plan
  if (planEdit) syncPlanFieldsFromDom();

  // prefer editable working copy
  const src = planEdit || clonePlan(plan || {});
  if (!planEdit && (plan?.golden?.length || plan?.trust?.length || plan?.cta?.length)) {
    planEdit = clonePlan(plan);
    planOriginal = clonePlan(plan);
    setPlanToolsEnabled(true);
  }

  const mk = (arr, role, key) => {
    const list = arr || [];
    if (!list.length) {
      return '<div class="jy-drop-hint" data-role="' + key + '">拖到这里 / 可从其他轨移入</div>';
    }
    return list
      .map((s, idx) => {
        const a = (Number(s.t0_ms || 0) / 1000).toFixed(1);
        const b = (Number(s.t1_ms || 0) / 1000).toFixed(1);
        const removed = !!s.removed;
        return `<div class="jy-clip expanded ${role} ${removed ? "removed" : ""}" draggable="true" data-role="${key}" data-idx="${idx}" data-id="${escapeHtml(s.clip_id || "")}">
          <div class="clip-top">
            <span class="clip-drag" title="拖动调整位置">⠿</span>
            <span class="clip-badge">${key === "golden" ? "黄金" : key === "trust" ? "信任" : "收尾"} #${idx + 1}</span>
            <button type="button" class="clip-x" title="${removed ? "恢复" : "删除"}">${removed ? "+" : "×"}</button>
          </div>
          <textarea class="clip-text-edit" rows="4" placeholder="编辑这段口播词…">${escapeHtml(s.text || "")}</textarea>
          <div class="clip-time-row">
            <label>开始(s)<input class="clip-t0s" type="number" step="0.1" min="0" value="${a}" /></label>
            <label>结束(s)<input class="clip-t1s" type="number" step="0.1" min="0" value="${b}" /></label>
            <input class="clip-t0" type="hidden" value="${Number(s.t0_ms || 0)}" />
            <input class="clip-t1" type="hidden" value="${Number(s.t1_ms || 0)}" />
          </div>
          <div class="meta">可改口播词与时间 · 拖拽排序 · 应用后按此反向剪视频</div>
          <div class="clip-tools">
            <button type="button" class="clip-up" title="同轨上移">↑</button>
            <button type="button" class="clip-down" title="同轨下移">↓</button>
            <button type="button" class="clip-prev" title="移到上一轨">↑轨</button>
            <button type="button" class="clip-next" title="移到下一轨">↓轨</button>
          </div>
        </div>`;
      })
      .join("");
  };

  $("golden-track").innerHTML = mk(src.golden, "hook", "golden");
  $("trust-track").innerHTML = mk(src.trust, "trust", "trust");
  $("cta-track").innerHTML = mk(src.cta, "cta", "cta");
  bindTrackEditors();
  updatePlanHint();
}

function bindTrackEditors() {
  // sync seconds inputs <-> ms hidden fields + model
  document.querySelectorAll(".jy-clip").forEach((card) => {
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    const t0 = card.querySelector(".clip-t0");
    const t1 = card.querySelector(".clip-t1");
    const ta = card.querySelector(".clip-text-edit");

    const push = () => {
      if (!planEdit?.[role]?.[idx]) return;
      if (ta) planEdit[role][idx].text = ta.value;
      if (t0s && t0) {
        const ms0 = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
        t0.value = String(ms0);
        planEdit[role][idx].t0_ms = ms0;
      }
      if (t1s && t1) {
        const ms1 = Math.max(0, Math.round(Number(t1s.value || 0) * 1000));
        t1.value = String(ms1);
        const ms0 = planEdit[role][idx].t0_ms || 0;
        planEdit[role][idx].t1_ms = ms1 > ms0 ? ms1 : ms0 + 500;
        if (ms1 <= ms0) t1s.value = ((ms0 + 500) / 1000).toFixed(1);
      }
      updatePlanHint();
    };
    ta?.addEventListener("input", push);
    t0s?.addEventListener("change", push);
    t1s?.addEventListener("change", push);
    // don't start drag from editors
    ta?.addEventListener("mousedown", (e) => e.stopPropagation());
    t0s?.addEventListener("mousedown", (e) => e.stopPropagation());
    t1s?.addEventListener("mousedown", (e) => e.stopPropagation());
  });

  // delete / restore
  document.querySelectorAll(".jy-clip .clip-x").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncPlanFieldsFromDom();
      const card = btn.closest(".jy-clip");
      const role = card.dataset.role;
      const idx = Number(card.dataset.idx);
      if (!planEdit?.[role]?.[idx]) return;
      planEdit[role][idx].removed = !planEdit[role][idx].removed;
      renderTracks(planEdit);
    };
  });

  // nudge buttons (vertical order inside track)
  document.querySelectorAll(".jy-clip .clip-up").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncPlanFieldsFromDom();
      const card = btn.closest(".jy-clip");
      const role = card.dataset.role;
      const idx = Number(card.dataset.idx);
      if (!planEdit?.[role] || idx <= 0) return;
      moveClip(role, idx, role, idx - 1);
    };
  });
  document.querySelectorAll(".jy-clip .clip-down").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncPlanFieldsFromDom();
      const card = btn.closest(".jy-clip");
      const role = card.dataset.role;
      const idx = Number(card.dataset.idx);
      if (!planEdit?.[role] || idx >= planEdit[role].length - 1) return;
      const arr = planEdit[role];
      const item = arr.splice(idx, 1)[0];
      arr.splice(idx + 1, 0, item);
      renderTracks(planEdit);
    };
  });
  document.querySelectorAll(".jy-clip .clip-prev").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncPlanFieldsFromDom();
      const card = btn.closest(".jy-clip");
      const role = card.dataset.role;
      const idx = Number(card.dataset.idx);
      const i = TRACK_ORDER.indexOf(role);
      if (i <= 0) return;
      moveClip(role, idx, TRACK_ORDER[i - 1], planEdit[TRACK_ORDER[i - 1]].length);
    };
  });
  document.querySelectorAll(".jy-clip .clip-next").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncPlanFieldsFromDom();
      const card = btn.closest(".jy-clip");
      const role = card.dataset.role;
      const idx = Number(card.dataset.idx);
      const i = TRACK_ORDER.indexOf(role);
      if (i < 0 || i >= TRACK_ORDER.length - 1) return;
      moveClip(role, idx, TRACK_ORDER[i + 1], planEdit[TRACK_ORDER[i + 1]].length);
    };
  });

  // drag & drop free move (handle area preferred)
  document.querySelectorAll(".jy-clip[draggable='true']").forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      if (e.target.closest("button, textarea, input")) {
        e.preventDefault();
        return;
      }
      syncPlanFieldsFromDom();
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData(
        "text/plain",
        JSON.stringify({ role: card.dataset.role, idx: Number(card.dataset.idx) })
      );
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      document.querySelectorAll(".jy-track-body").forEach((t) => t.classList.remove("drag-over"));
      document.querySelectorAll(".jy-clip").forEach((c) => c.classList.remove("drag-over-left", "drag-over-right"));
    });
  });

  document.querySelectorAll(".jy-track-body").forEach((track) => {
    track.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      track.classList.add("drag-over");
      const overClip = e.target.closest(".jy-clip");
      document.querySelectorAll(".jy-clip").forEach((c) => c.classList.remove("drag-over-left", "drag-over-right"));
      if (overClip && track.contains(overClip)) {
        const rect = overClip.getBoundingClientRect();
        const mid = rect.top + rect.height / 2;
        overClip.classList.add(e.clientY < mid ? "drag-over-left" : "drag-over-right");
      }
    });
    track.addEventListener("dragleave", (e) => {
      if (!track.contains(e.relatedTarget)) track.classList.remove("drag-over");
    });
    track.addEventListener("drop", (e) => {
      e.preventDefault();
      track.classList.remove("drag-over");
      let payload = null;
      try {
        payload = JSON.parse(e.dataTransfer.getData("text/plain") || "{}");
      } catch (_) {
        return;
      }

      const trackId = track.id; // golden-track / trust-track / cta-track
      const toRole = trackId.replace("-track", "");
      if (!TRACK_ORDER.includes(toRole)) return;

      // ensure plan model exists
      if (!planEdit) {
        planEdit = { golden: [], trust: [], cta: [] };
        planOriginal = clonePlan(planEdit);
        setPlanToolsEnabled(true);
      }
      if (!planEdit[toRole]) planEdit[toRole] = [];

      let toIdx = planEdit[toRole].length;
      const overClip = e.target.closest(".jy-clip");
      if (overClip && track.contains(overClip) && overClip.dataset.role === toRole) {
        const overIdx = Number(overClip.dataset.idx);
        const rect = overClip.getBoundingClientRect();
        const mid = rect.top + rect.height / 2;
        toIdx = e.clientY < mid ? overIdx : overIdx + 1;
      }

      // from left ASR cards
      if (payload.source === "asr") {
        const aidx = Number(payload.idx);
        const item = asrCards[aidx];
        if (!item) return;
        // pull latest edited values from left card if present
        const leftCard = document.querySelector(`.asr-card[data-idx="${aidx}"]`);
        let text = item.text;
        let t0 = Number(item.t0_ms || 0);
        let t1 = Number(item.t1_ms || 0);
        if (leftCard) {
          const ta = leftCard.querySelector(".clip-text-edit");
          const t0s = leftCard.querySelector(".clip-t0s");
          const t1s = leftCard.querySelector(".clip-t1s");
          if (ta) text = ta.value;
          if (t0s) t0 = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
          if (t1s) t1 = Math.max(t0 + 300, Math.round(Number(t1s.value || 0) * 1000));
        }
        const slot = {
          clip_id: `asr_${aidx}_${Date.now().toString(36)}`,
          role: roleLabel(toRole),
          text: String(text || "").trim(),
          t0_ms: t0,
          t1_ms: t1,
          score: 20,
          removed: false,
        };
        if (!slot.text) return;
        planEdit[toRole].splice(Math.max(0, toIdx), 0, slot);
        renderTracks(planEdit);
        return;
      }

      // from plan track reorder/move
      const fromRole = payload.role;
      const fromIdx = Number(payload.idx);
      if (!planEdit?.[fromRole]?.[fromIdx]) return;
      if (fromRole === toRole && fromIdx < toIdx) toIdx -= 1;
      moveClip(fromRole, fromIdx, toRole, Math.max(0, toIdx));
    });
  });
}

async function applyPlanEdit() {
  if (!currentJobId || !planEdit) return;
  syncPlanFieldsFromDom();
  const clean = (arr) =>
    (arr || [])
      .filter((s) => !s.removed)
      .map((s) => ({
        clip_id: s.clip_id,
        role: s.role,
        text: String(s.text || "").trim(),
        t0_ms: Math.max(0, Number(s.t0_ms || 0)),
        t1_ms: Math.max(Number(s.t0_ms || 0) + 300, Number(s.t1_ms || 0)),
        score: Number(s.score || 0),
      }))
      .filter((s) => s.text);
  const payload = {
    reclip: true,
    golden: clean(planEdit.golden),
    trust: clean(planEdit.trust),
    cta: clean(planEdit.cta),
  };
  if (!payload.golden.length && !payload.trust.length && !payload.cta.length) {
    alert("请至少保留一个片段，并填写口播词");
    return;
  }
  $("plan-edit-hint").textContent = "正在按修改后的口播与结构反向剪视频…";
  $("plan-apply").disabled = true;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}/plan`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "应用失败");
    // reset local edit baseline after server accepts
    planEdit = null;
    planOriginal = null;
    renderJob(data);
    await loadJobs();
    pollJob(currentJobId);
  } catch (e) {
    alert(String(e.message || e));
    $("plan-apply").disabled = false;
  }
}

function setupPlanTools() {
  $("plan-reset")?.addEventListener("click", () => {
    if (!planOriginal) return;
    planEdit = clonePlan(planOriginal);
    renderTracks(planEdit);
  });
  $("plan-apply")?.addEventListener("click", applyPlanEdit);
}

function setAsrToolsEnabled(on) {
  ["asr-reload", "asr-to-golden", "asr-to-trust", "asr-to-cta"].forEach((id) => {
    if ($(id)) $(id).disabled = !on;
  });
}

function addAsrToTrack(trackKey, indices) {
  if (!TRACK_ORDER.includes(trackKey)) return;
  if (!planEdit) {
    planEdit = { golden: [], trust: [], cta: [] };
    planOriginal = clonePlan(planEdit);
  }
  setPlanToolsEnabled(true);
  const list = indices && indices.length ? indices : [...selectedAsr];
  if (!list.length) {
    alert("请先勾选左侧口播卡片，或直接拖拽到成片结构");
    return;
  }
  list
    .sort((a, b) => a - b)
    .forEach((aidx) => {
      const item = asrCards[aidx];
      if (!item) return;
      const leftCard = document.querySelector(`.asr-card[data-idx="${aidx}"]`);
      let text = item.text;
      let t0 = Number(item.t0_ms || 0);
      let t1 = Number(item.t1_ms || 0);
      if (leftCard) {
        const ta = leftCard.querySelector(".clip-text-edit");
        const t0s = leftCard.querySelector(".clip-t0s");
        const t1s = leftCard.querySelector(".clip-t1s");
        if (ta) text = ta.value;
        if (t0s) t0 = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
        if (t1s) t1 = Math.max(t0 + 300, Math.round(Number(t1s.value || 0) * 1000));
        // keep left edits
        item.text = text;
        item.t0_ms = t0;
        item.t1_ms = t1;
      }
      const slot = {
        clip_id: `asr_${aidx}_${Date.now().toString(36)}_${Math.random().toString(16).slice(2, 6)}`,
        role: roleLabel(trackKey),
        text: String(text || "").trim(),
        t0_ms: t0,
        t1_ms: t1,
        score: 20,
        removed: false,
      };
      if (!slot.text) return;
      planEdit[trackKey].push(slot);
    });
  selectedAsr.clear();
  renderTracks(planEdit);
  renderAsrCards();
}

function renderAsrCards() {
  const box = $("transcript-list");
  if (!box) return;
  if (!asrCards.length) {
    box.innerHTML = '<div class="jy-empty">口播尚未生成 / 无保留句子</div>';
    setAsrToolsEnabled(false);
    return;
  }
  setAsrToolsEnabled(true);
  $("asr-count").textContent = `${asrCards.length} 句`;
  box.innerHTML = asrCards
    .map((u, idx) => {
      const a = (Number(u.t0_ms || 0) / 1000).toFixed(1);
      const b = (Number(u.t1_ms || 0) / 1000).toFixed(1);
      const checked = selectedAsr.has(idx) ? "checked" : "";
      return `<div class="jy-clip expanded asr-card trust" draggable="true" data-source="asr" data-idx="${idx}">
        <div class="clip-top">
          <input type="checkbox" class="asr-check" ${checked} title="多选后批量加入成片" />
          <span class="clip-drag" title="拖到成片结构">⠿</span>
          <span class="clip-badge">口播 #${idx + 1}</span>
          <button type="button" class="clip-x asr-add-one" title="加入黄金">＋</button>
        </div>
        <textarea class="clip-text-edit" rows="5" placeholder="编辑这段口播词…">${escapeHtml(u.text || "")}</textarea>
        <div class="clip-time-row">
          <label>开始(s)<input class="clip-t0s" type="number" step="0.1" min="0" value="${a}" /></label>
          <label>结束(s)<input class="clip-t1s" type="number" step="0.1" min="0" value="${b}" /></label>
        </div>
        <div class="meta">可改词/改时码 · 拖到右侧成片结构 · 或勾选后点 +黄金/信任/收尾</div>
        <div class="clip-tools">
          <button type="button" class="asr-add-golden">+黄金</button>
          <button type="button" class="asr-add-trust">+信任</button>
          <button type="button" class="asr-add-cta">+收尾</button>
        </div>
      </div>`;
    })
    .join("");

  // bind left cards
  box.querySelectorAll(".asr-card").forEach((card) => {
    const idx = Number(card.dataset.idx);
    const ta = card.querySelector(".clip-text-edit");
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    const ck = card.querySelector(".asr-check");
    const push = () => {
      if (!asrCards[idx]) return;
      if (ta) asrCards[idx].text = ta.value;
      if (t0s) asrCards[idx].t0_ms = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
      if (t1s) {
        const t0 = asrCards[idx].t0_ms || 0;
        const t1 = Math.max(t0 + 300, Math.round(Number(t1s.value || 0) * 1000));
        asrCards[idx].t1_ms = t1;
      }
    };
    ta?.addEventListener("input", push);
    t0s?.addEventListener("change", push);
    t1s?.addEventListener("change", push);
    ta?.addEventListener("mousedown", (e) => e.stopPropagation());
    t0s?.addEventListener("mousedown", (e) => e.stopPropagation());
    t1s?.addEventListener("mousedown", (e) => e.stopPropagation());
    ck?.addEventListener("change", () => {
      if (ck.checked) selectedAsr.add(idx);
      else selectedAsr.delete(idx);
    });
    card.querySelector(".asr-add-golden")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      push();
      addAsrToTrack("golden", [idx]);
    });
    card.querySelector(".asr-add-trust")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      push();
      addAsrToTrack("trust", [idx]);
    });
    card.querySelector(".asr-add-cta")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      push();
      addAsrToTrack("cta", [idx]);
    });
    card.querySelector(".asr-add-one")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      push();
      addAsrToTrack("golden", [idx]);
    });

    card.addEventListener("dragstart", (e) => {
      if (e.target.closest("button, textarea, input")) {
        e.preventDefault();
        return;
      }
      push();
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "copyMove";
      e.dataTransfer.setData("text/plain", JSON.stringify({ source: "asr", idx }));
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
  });
}

async function loadTranscript(jobId) {
  const box = $("transcript-list");
  $("asr-count").textContent = "—";
  asrCards = [];
  selectedAsr.clear();
  setAsrToolsEnabled(false);
  try {
    // prefer full raw asr for human selection space; fallback kept
    let items = [];
    const resRaw = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/files/transcript_asr.json`);
    if (resRaw.ok) {
      items = await resRaw.json();
    } else {
      const resKept = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/files/transcript_for_clipper.json`);
      if (!resKept.ok) {
        box.innerHTML = '<div class="jy-empty">口播尚未生成</div>';
        return;
      }
      items = await resKept.json();
    }
    asrCards = (items || [])
      .filter((u) => u && String(u.text || "").trim())
      .map((u, i) => ({
        utt_id: u.utt_id || `a${i}`,
        text: String(u.text || "").trim(),
        t0_ms: Number(u.t0_ms || 0),
        t1_ms: Number(u.t1_ms || 0),
      }));
    renderAsrCards();
  } catch {
    box.innerHTML = '<div class="jy-empty">口播加载失败</div>';
  }
}

function setupAsrTools() {
  $("asr-reload")?.addEventListener("click", () => {
    if (currentJobId) loadTranscript(currentJobId);
  });
  $("asr-to-golden")?.addEventListener("click", () => addAsrToTrack("golden"));
  $("asr-to-trust")?.addEventListener("click", () => addAsrToTrack("trust"));
  $("asr-to-cta")?.addEventListener("click", () => addAsrToTrack("cta"));
}

function renderJob(data) {
  const jobChanged = currentJobId !== data.job_id;
  currentJobId = data.job_id;
  if (jobChanged) {
    planEdit = null;
    planOriginal = null;
    setPlanToolsEnabled(false);
  }
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
  // Keep local plan edits while user is adjusting; only reset on job switch
  // or when server returns a finished plan and we have no local edits yet.
  if (jobChanged) {
    planEdit = null;
    planOriginal = null;
    renderTracks(data.plan || {});
  } else if (planEdit) {
    renderTracks(planEdit);
  } else {
    renderTracks(data.plan || {});
  }
  // review text panel removed from UI; status shown in player bar / progress

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
    $("file-name").textContent = fileInput.files?.[0]?.name || "支持 mp4 / mov / mkv / webm / ts";
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
      if (!res.ok) throw new Error(data.detail || res.statusText || "创建失败");
      renderJob(data);
      await loadJobs();
      pollJob(data.job_id);
    } catch (ex) {
      err.hidden = false;
      const msg = String(ex.message || ex);
      if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("fetch")) {
        err.textContent =
          "无法连接本地服务 (127.0.0.1:8787)。请先运行 start-web.bat 并保持窗口不关闭，然后刷新页面再试。";
      } else {
        err.textContent = msg;
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "开始服装切片";
    }
  });
}

let transcriptCache = [];

function openTranscriptDrawer() {
  const mask = $("drawer-backdrop");
  const drawer = $("transcript-drawer");
  if (mask) {
    mask.hidden = false;
    mask.style.display = "block";
  }
  if (drawer) {
    drawer.hidden = false;
    drawer.style.display = "flex";
  }
  loadTranscriptEditor(currentJobId);
}

function closeTranscriptDrawer() {
  const mask = $("drawer-backdrop");
  const drawer = $("transcript-drawer");
  if (mask) {
    mask.hidden = true;
    mask.style.display = "none";
  }
  if (drawer) {
    drawer.hidden = true;
    drawer.style.display = "none";
  }
}

function setupTranscriptModule() {
  $("open-transcript")?.addEventListener("click", (e) => {
    e.preventDefault();
    openTranscriptDrawer();
  });
  // capture phase so close always works even if something stops bubbling
  $("close-transcript")?.addEventListener(
    "click",
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeTranscriptDrawer();
    },
    true
  );
  $("drawer-backdrop")?.addEventListener("click", (e) => {
    e.preventDefault();
    closeTranscriptDrawer();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeTranscriptDrawer();
  });
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
    const utt_id = row.dataset.uid || `e${String(i).padStart(4, "0")}`;
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

function setupTranscriptPanelToggle() {
  const panel = $("panel-transcript");
  const btn = $("toggle-transcript-panel");
  if (!panel || !btn) return;
  const apply = (collapsed) => {
    panel.classList.toggle("collapsed", collapsed);
    btn.textContent = collapsed ? "展开" : "收起";
    try {
      localStorage.setItem("clipper_transcript_panel_collapsed", collapsed ? "1" : "0");
    } catch (_) {}
  };
  // restore preference
  let collapsed = false;
  try {
    collapsed = localStorage.getItem("clipper_transcript_panel_collapsed") === "1";
  } catch (_) {}
  apply(collapsed);
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    apply(!panel.classList.contains("collapsed"));
  });
}

$("refresh-jobs")?.addEventListener("click", loadJobs);
loadHealth();
loadJobs();
setupForm();
setupTranscriptModule();
setupPlanTools();
setupAsrTools();
setupTranscriptPanelToggle();
