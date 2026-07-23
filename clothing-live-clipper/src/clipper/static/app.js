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
let planDirty = false; // user is editing plan; avoid poll clobber
let planRenderQueued = false;
let planEventsBound = false;
let asrEventsBound = false;

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
  if ($("plan-balance")) $("plan-balance").disabled = !on;
}

function slotDurMs(s) {
  return Math.max(0, Number(s.t1_ms || 0) - Number(s.t0_ms || 0));
}

function updatePlanHint() {
  const el = $("plan-edit-hint");
  if (!el || !planEdit) {
    if (el) el.textContent = "";
    return;
  }
  const slots = ["golden", "trust", "cta"].flatMap((k) =>
    (planEdit[k] || []).filter((s) => !s.removed)
  );
  const n = slots.length;
  const durs = slots.map(slotDurMs).filter((d) => d > 0);
  if (!n) {
    el.textContent = "暂无片段";
    return;
  }
  const avg = durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : 0;
  const min = durs.length ? Math.min(...durs) : 0;
  const max = durs.length ? Math.max(...durs) : 0;
  el.textContent = `共 ${n} 段 · 均长 ${(avg / 1000).toFixed(1)}s · 最短 ${(min / 1000).toFixed(1)}s / 最长 ${(
    max / 1000
  ).toFixed(1)}s · 拖到卡片上可替换`;
}

function balancePlanDurations() {
  if (!planEdit) return;
  syncPlanFieldsFromDom();
  const slots = [];
  ["golden", "trust", "cta"].forEach((k) => {
    (planEdit[k] || []).forEach((s, idx) => {
      if (!s.removed) slots.push({ k, idx, s });
    });
  });
  if (slots.length < 2) {
    updatePlanHint();
    return;
  }
  // target: average of current active durations, clamped for watchability
  const avg = slots.reduce((a, x) => a + slotDurMs(x.s), 0) / slots.length;
  const target = Math.max(2500, Math.min(9000, Math.round(avg)));
  slots.forEach(({ s }) => {
    const t0 = Math.max(0, Number(s.t0_ms || 0));
    // keep start, stretch/shrink end around target
    s.t1_ms = t0 + target;
  });
  planDirty = true;
  queueRenderTracks();
  if ($("plan-edit-hint")) {
    $("plan-edit-hint").textContent = `已均分到约 ${(target / 1000).toFixed(1)}s/段（可再微调）`;
  }
}

function replaceClipWithAsr(toRole, toIdx, asrIdx) {
  if (!planEdit?.[toRole]?.[toIdx]) return;
  const item = asrCards[asrIdx];
  if (!item) return;
  const leftCard = document.querySelector(`.asr-card[data-idx="${asrIdx}"]`);
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
  const old = planEdit[toRole][toIdx];
  // keep roughly same duration if possible for visual consistency
  const oldDur = Math.max(1500, slotDurMs(old) || 4000);
  const newDur = Math.max(1500, t1 - t0);
  // prefer source asr window, but if wildly different, keep new asr window
  planEdit[toRole][toIdx] = {
    ...old,
    clip_id: `asr_rep_${asrIdx}_${Date.now().toString(36)}`,
    role: roleLabel(toRole),
    text: String(text || "").trim() || old.text,
    t0_ms: t0,
    t1_ms: t0 + (Math.abs(newDur - oldDur) > oldDur * 0.8 ? newDur : oldDur),
    score: Number(old.score || 20),
    removed: false,
  };
  planDirty = true;
  queueRenderTracks();
}

function swapClips(aRole, aIdx, bRole, bIdx) {
  if (!planEdit?.[aRole]?.[aIdx] || !planEdit?.[bRole]?.[bIdx]) return;
  if (aRole === bRole && aIdx === bIdx) return;
  const a = planEdit[aRole][aIdx];
  const b = planEdit[bRole][bIdx];
  // swap content but keep section role labels
  planEdit[aRole][aIdx] = { ...b, role: roleLabel(aRole) };
  planEdit[bRole][bIdx] = { ...a, role: roleLabel(bRole) };
  planDirty = true;
  queueRenderTracks();
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
  planDirty = true;
  queueRenderTracks();
}

function syncPlanFieldsFromDom() {
  if (!planEdit) return;
  // only plan cards in center tracks (not left asr cards)
  document.querySelectorAll("#golden-track .jy-clip, #trust-track .jy-clip, #cta-track .jy-clip").forEach((card) => {
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    if (!planEdit?.[role]?.[idx]) return;
    const ta = card.querySelector(".clip-text-edit");
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    if (ta) planEdit[role][idx].text = ta.value;
    if (t0s) planEdit[role][idx].t0_ms = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
    if (t1s) {
      const v0 = planEdit[role][idx].t0_ms || 0;
      const v1 = Math.max(v0 + 300, Math.round(Number(t1s.value || 0) * 1000));
      planEdit[role][idx].t1_ms = v1;
    }
  });
}

function queueRenderTracks() {
  if (planRenderQueued) return;
  planRenderQueued = true;
  requestAnimationFrame(() => {
    planRenderQueued = false;
    renderTracks(planEdit || {});
  });
}

function renderTracks(plan) {
  // prefer editable working copy
  const src = planEdit || clonePlan(plan || {});
  if (!planEdit && (plan?.golden?.length || plan?.trust?.length || plan?.cta?.length)) {
    planEdit = clonePlan(plan);
    planOriginal = clonePlan(plan);
    setPlanToolsEnabled(true);
  }

  // preserve focus/cursor across re-render
  const ae = document.activeElement;
  let focusKey = null;
  let caret = null;
  if (ae && ae.closest && ae.closest(".jy-clip") && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT")) {
    const card = ae.closest(".jy-clip");
    focusKey = `${card.dataset.role}:${card.dataset.idx}:${ae.className}`;
    try {
      caret = { s: ae.selectionStart, e: ae.selectionEnd };
    } catch (_) {}
  }
  const scrollBox = document.querySelector(".jy-tracks");
  const scrollTop = scrollBox ? scrollBox.scrollTop : 0;

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
            <span class="clip-drag" title="拖动调整位置" draggable="false">⠿</span>
            <span class="clip-badge">${key === "golden" ? "黄金" : key === "trust" ? "信任" : "收尾"} #${idx + 1}</span>
            <button type="button" class="clip-x" title="${removed ? "恢复" : "删除"}">${removed ? "+" : "×"}</button>
          </div>
          <textarea class="clip-text-edit" rows="4" placeholder="编辑这段口播词…">${escapeHtml(s.text || "")}</textarea>
          <div class="clip-time-row">
            <label>开始(s)<input class="clip-t0s" type="number" step="0.1" min="0" value="${a}" /></label>
            <label>结束(s)<input class="clip-t1s" type="number" step="0.1" min="0" value="${b}" /></label>
          </div>
          <div class="meta">时长 ${((Number(s.t1_ms || 0) - Number(s.t0_ms || 0)) / 1000).toFixed(1)}s · 拖到其他卡片可替换 · 空白处可排序</div>
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
  ensurePlanEventsBound();
  updatePlanHint();

  if (scrollBox) scrollBox.scrollTop = scrollTop;
  if (focusKey) {
    const [role, idx, cls] = focusKey.split(":");
    const card = document.querySelector(`#${role}-track .jy-clip[data-idx="${idx}"]`);
    const el = card?.querySelector(`.${cls.split(" ").filter(Boolean).join(".")}`) || card?.querySelector(".clip-text-edit");
    if (el) {
      el.focus({ preventScroll: true });
      if (caret && typeof el.setSelectionRange === "function") {
        try {
          el.setSelectionRange(caret.s ?? el.value.length, caret.e ?? el.value.length);
        } catch (_) {}
      }
    }
  }
}

function ensurePlanEventsBound() {
  if (planEventsBound) return;
  planEventsBound = true;
  const root = document.querySelector(".jy-timeline-panel");
  if (!root) return;

  // text/time edits: update model only, NO full re-render (smooth typing)
  root.addEventListener("input", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    if (!t.classList.contains("clip-text-edit") && !t.classList.contains("clip-t0s") && !t.classList.contains("clip-t1s")) return;
    const card = t.closest(".jy-clip");
    if (!card || !planEdit) return;
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    if (!planEdit?.[role]?.[idx]) return;
    planDirty = true;
    if (t.classList.contains("clip-text-edit")) {
      planEdit[role][idx].text = t.value;
    } else if (t.classList.contains("clip-t0s")) {
      planEdit[role][idx].t0_ms = Math.max(0, Math.round(Number(t.value || 0) * 1000));
    } else if (t.classList.contains("clip-t1s")) {
      const v0 = planEdit[role][idx].t0_ms || 0;
      planEdit[role][idx].t1_ms = Math.max(v0 + 300, Math.round(Number(t.value || 0) * 1000));
    }
  });

  root.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const card = btn.closest(".jy-clip");
    if (!card || !planEdit) return;
    e.preventDefault();
    e.stopPropagation();
    // capture latest fields first (cheap)
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    const ta = card.querySelector(".clip-text-edit");
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    if (planEdit?.[role]?.[idx]) {
      if (ta) planEdit[role][idx].text = ta.value;
      if (t0s) planEdit[role][idx].t0_ms = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
      if (t1s) {
        const v0 = planEdit[role][idx].t0_ms || 0;
        planEdit[role][idx].t1_ms = Math.max(v0 + 300, Math.round(Number(t1s.value || 0) * 1000));
      }
    }

    if (btn.classList.contains("clip-x")) {
      if (!planEdit?.[role]?.[idx]) return;
      planEdit[role][idx].removed = !planEdit[role][idx].removed;
      planDirty = true;
      queueRenderTracks();
      return;
    }
    if (btn.classList.contains("clip-up")) {
      if (idx > 0) moveClip(role, idx, role, idx - 1);
      return;
    }
    if (btn.classList.contains("clip-down")) {
      if (planEdit?.[role] && idx < planEdit[role].length - 1) {
        const arr = planEdit[role];
        const item = arr.splice(idx, 1)[0];
        arr.splice(idx + 1, 0, item);
        planDirty = true;
        queueRenderTracks();
      }
      return;
    }
    if (btn.classList.contains("clip-prev")) {
      const i = TRACK_ORDER.indexOf(role);
      if (i > 0) moveClip(role, idx, TRACK_ORDER[i - 1], planEdit[TRACK_ORDER[i - 1]].length);
      return;
    }
    if (btn.classList.contains("clip-next")) {
      const i = TRACK_ORDER.indexOf(role);
      if (i >= 0 && i < TRACK_ORDER.length - 1) moveClip(role, idx, TRACK_ORDER[i + 1], planEdit[TRACK_ORDER[i + 1]].length);
    }
  });

  // drag start (event delegation)
  root.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".jy-clip");
    if (!card || !root.contains(card)) return;
    if (e.target.closest("button, textarea, input")) {
      e.preventDefault();
      return;
    }
    // sync only this card fields
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    if (planEdit?.[role]?.[idx]) {
      const ta = card.querySelector(".clip-text-edit");
      const t0s = card.querySelector(".clip-t0s");
      const t1s = card.querySelector(".clip-t1s");
      if (ta) planEdit[role][idx].text = ta.value;
      if (t0s) planEdit[role][idx].t0_ms = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
      if (t1s) {
        const v0 = planEdit[role][idx].t0_ms || 0;
        planEdit[role][idx].t1_ms = Math.max(v0 + 300, Math.round(Number(t1s.value || 0) * 1000));
      }
    }
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", JSON.stringify({ role: card.dataset.role, idx: Number(card.dataset.idx) }));
  });

  root.addEventListener("dragend", (e) => {
    const card = e.target.closest?.(".jy-clip");
    card?.classList.remove("dragging");
    root.querySelectorAll(".jy-track-body").forEach((t) => t.classList.remove("drag-over"));
    root.querySelectorAll(".jy-clip").forEach((c) => c.classList.remove("drag-over-left", "drag-over-right"));
  });

  root.addEventListener("dragover", (e) => {
    const track = e.target.closest?.(".jy-track-body");
    if (!track) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    track.classList.add("drag-over");
    const overClip = e.target.closest(".jy-clip");
    root.querySelectorAll(".jy-clip").forEach((c) =>
      c.classList.remove("drag-over-left", "drag-over-right", "drag-over-replace")
    );
    if (overClip && track.contains(overClip)) {
      // hovering body of a card => replace mode; near edges still show insert cue
      const rect = overClip.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const edge = Math.max(12, rect.height * 0.22);
      if (y < edge) overClip.classList.add("drag-over-left");
      else if (y > rect.height - edge) overClip.classList.add("drag-over-right");
      else overClip.classList.add("drag-over-replace");
    }
  });

  root.addEventListener("drop", (e) => {
    const track = e.target.closest?.(".jy-track-body");
    if (!track) return;
    e.preventDefault();
    track.classList.remove("drag-over");
    let payload = null;
    try {
      payload = JSON.parse(e.dataTransfer.getData("text/plain") || "{}");
    } catch (_) {
      return;
    }
    const toRole = track.id.replace("-track", "");
    if (!TRACK_ORDER.includes(toRole)) return;
    if (!planEdit) {
      planEdit = { golden: [], trust: [], cta: [] };
      planOriginal = clonePlan(planEdit);
      setPlanToolsEnabled(true);
    }
    if (!planEdit[toRole]) planEdit[toRole] = [];

    const overClip = e.target.closest(".jy-clip");
    // 1) drop ON a card center => replace/swap; near edges => insert
    if (overClip && track.contains(overClip) && overClip.dataset.role === toRole) {
      const overIdx = Number(overClip.dataset.idx);
      const rect = overClip.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const edge = Math.max(12, rect.height * 0.22);
      const nearEdge = y < edge || y > rect.height - edge;
      if (!nearEdge) {
        if (payload.source === "asr") {
          replaceClipWithAsr(toRole, overIdx, Number(payload.idx));
          return;
        }
        if (payload.role != null && payload.idx != null) {
          swapClips(String(payload.role), Number(payload.idx), toRole, overIdx);
          return;
        }
      }
    }

    // 2) drop on empty area / card edge => insert/reorder
    let toIdx = planEdit[toRole].length;
    if (overClip && track.contains(overClip) && overClip.dataset.role === toRole) {
      const overIdx = Number(overClip.dataset.idx);
      const rect = overClip.getBoundingClientRect();
      const mid = rect.top + rect.height / 2;
      toIdx = e.clientY < mid ? overIdx : overIdx + 1;
    }

    if (payload.source === "asr") {
      const aidx = Number(payload.idx);
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
      }
      // if track already has clips, make new clip duration near average
      const act = (planEdit[toRole] || []).filter((s) => !s.removed);
      if (act.length) {
        const avg = act.reduce((a, s) => a + slotDurMs(s), 0) / act.length;
        const target = Math.max(2500, Math.min(9000, Math.round(avg)));
        t1 = t0 + target;
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
      planDirty = true;
      queueRenderTracks();
      return;
    }

    const fromRole = payload.role;
    const fromIdx = Number(payload.idx);
    if (!planEdit?.[fromRole]?.[fromIdx]) return;
    if (fromRole === toRole && fromIdx < toIdx) toIdx -= 1;
    moveClip(fromRole, fromIdx, toRole, Math.max(0, toIdx));
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
    // accept server result and clear dirty lock
    planDirty = false;
    planEdit = null;
    planOriginal = null;
    renderJob(data);
    await loadJobs();
    pollJob(currentJobId);
    loadHealth();
  } catch (e) {
    alert(String(e.message || e));
    $("plan-apply").disabled = false;
  }
}

function setupPlanTools() {
  $("plan-reset")?.addEventListener("click", () => {
    if (!planOriginal) return;
    planEdit = clonePlan(planOriginal);
    planDirty = true;
    queueRenderTracks();
  });
  $("plan-balance")?.addEventListener("click", () => balancePlanDurations());
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
  // clear checks without full left re-render
  document.querySelectorAll(".asr-card .asr-check").forEach((ck) => {
    ck.checked = false;
  });
  planDirty = true;
  queueRenderTracks();
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

  // preserve left panel scroll + focus
  const scrollTop = box.scrollTop;
  const ae = document.activeElement;
  let focusIdx = null;
  let focusCls = null;
  let caret = null;
  if (ae && ae.closest && ae.closest(".asr-card")) {
    const c = ae.closest(".asr-card");
    focusIdx = c.dataset.idx;
    focusCls = ae.className;
    try {
      caret = { s: ae.selectionStart, e: ae.selectionEnd };
    } catch (_) {}
  }

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

  ensureAsrEventsBound();
  box.scrollTop = scrollTop;
  if (focusIdx != null) {
    const card = box.querySelector(`.asr-card[data-idx="${focusIdx}"]`);
    const el =
      (focusCls && card?.querySelector(`.${String(focusCls).split(" ").filter(Boolean).join(".")}`)) ||
      card?.querySelector(".clip-text-edit");
    if (el) {
      el.focus({ preventScroll: true });
      if (caret && typeof el.setSelectionRange === "function") {
        try {
          el.setSelectionRange(caret.s ?? el.value.length, caret.e ?? el.value.length);
        } catch (_) {}
      }
    }
  }
}

function ensureAsrEventsBound() {
  if (asrEventsBound) return;
  asrEventsBound = true;
  const box = $("transcript-list");
  if (!box) return;

  // typing: only update model, no re-render
  box.addEventListener("input", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    const card = t.closest(".asr-card");
    if (!card) return;
    const idx = Number(card.dataset.idx);
    if (!asrCards[idx]) return;
    if (t.classList.contains("clip-text-edit")) asrCards[idx].text = t.value;
    if (t.classList.contains("clip-t0s")) asrCards[idx].t0_ms = Math.max(0, Math.round(Number(t.value || 0) * 1000));
    if (t.classList.contains("clip-t1s")) {
      const t0 = asrCards[idx].t0_ms || 0;
      asrCards[idx].t1_ms = Math.max(t0 + 300, Math.round(Number(t.value || 0) * 1000));
    }
  });

  box.addEventListener("change", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    const card = t.closest(".asr-card");
    if (!card) return;
    const idx = Number(card.dataset.idx);
    if (t.classList.contains("asr-check")) {
      if (t.checked) selectedAsr.add(idx);
      else selectedAsr.delete(idx);
    }
  });

  box.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const card = btn.closest(".asr-card");
    if (!card) return;
    e.preventDefault();
    e.stopPropagation();
    const idx = Number(card.dataset.idx);
    // flush current values
    const ta = card.querySelector(".clip-text-edit");
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    if (asrCards[idx]) {
      if (ta) asrCards[idx].text = ta.value;
      if (t0s) asrCards[idx].t0_ms = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
      if (t1s) {
        const t0 = asrCards[idx].t0_ms || 0;
        asrCards[idx].t1_ms = Math.max(t0 + 300, Math.round(Number(t1s.value || 0) * 1000));
      }
    }
    if (btn.classList.contains("asr-add-golden") || btn.classList.contains("asr-add-one")) addAsrToTrack("golden", [idx]);
    else if (btn.classList.contains("asr-add-trust")) addAsrToTrack("trust", [idx]);
    else if (btn.classList.contains("asr-add-cta")) addAsrToTrack("cta", [idx]);
  });

  box.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".asr-card");
    if (!card) return;
    if (e.target.closest("button, textarea, input")) {
      e.preventDefault();
      return;
    }
    const idx = Number(card.dataset.idx);
    const ta = card.querySelector(".clip-text-edit");
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    if (asrCards[idx]) {
      if (ta) asrCards[idx].text = ta.value;
      if (t0s) asrCards[idx].t0_ms = Math.max(0, Math.round(Number(t0s.value || 0) * 1000));
      if (t1s) {
        const t0 = asrCards[idx].t0_ms || 0;
        asrCards[idx].t1_ms = Math.max(t0 + 300, Math.round(Number(t1s.value || 0) * 1000));
      }
    }
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "copyMove";
    e.dataTransfer.setData("text/plain", JSON.stringify({ source: "asr", idx }));
  });
  box.addEventListener("dragend", (e) => {
    e.target.closest?.(".asr-card")?.classList.remove("dragging");
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
    planDirty = false;
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

  // video: don't thrash src while user edits / same file
  const video = $("preview");
  const files = data.files || {};
  if (files.final) {
    const url = `/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4`;
    if (!video.src.includes(url) || (!processing && !planDirty)) {
      // only refresh final when not mid-edit, or first load
      if (!planDirty || jobChanged || !video.src) {
        video.src = `${url}?t=${Date.now()}`;
      }
    }
    $("export-btn").disabled = false;
    $("export-btn").onclick = () => {
      window.open(`/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4`, "_blank");
    };
  } else if (jobChanged) {
    video.removeAttribute("src");
    $("export-btn").disabled = true;
  }

  // tracks: never clobber while user is editing (planDirty)
  if (jobChanged) {
    planEdit = null;
    planOriginal = null;
    planDirty = false;
    renderTracks(data.plan || {});
    loadTranscript(data.job_id);
  } else if (!planDirty) {
    if (!planEdit) renderTracks(data.plan || {});
    // don't reload transcript cards while typing
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

  // only load left transcript when switching jobs (or empty)
  if (jobChanged || !asrCards.length) {
    loadTranscript(data.job_id);
  }
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
      // while user is editing, only lightly refresh jobs list / progress text, don't rebuild editors
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (planDirty && data.job_id === currentJobId) {
        // soft progress update only
        const st = data.status || "";
        $("current-job-status").textContent = `${STATUS_LABEL[st] || st}${
          data.final_duration_s ? ` · ${data.final_duration_s}s` : ""
        }`;
        const processing = ["queued", "processing", "starting", "claimed"].includes(st);
        const pb = $("progress-block");
        if (processing) {
          pb.hidden = false;
          const pct = Number(data.progress || 15);
          $("progress-bar").style.width = `${pct}%`;
          $("progress-text").textContent = `${pct}%`;
          $("stage-text").textContent = stageLabel(data.stage) + (data.stage_detail ? ` · ${data.stage_detail}` : "");
        }
        if (!processing) {
          // finished while editing: allow one full refresh after user applies, not now
          loadJobs();
          clearInterval(pollTimer);
          pollTimer = null;
        }
        return;
      }
      renderJob(data);
      loadJobs();
      if (!["queued", "processing", "starting", "claimed"].includes(data.status)) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (_) {}
  }, 2500);
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
