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

/** Module timestamp precision: keep ms (0.01s UI), not 100ms. */
const TS_UI_STEP = "0.01";
const TS_MIN_DUR_MS = 120; // allow tighter reverse cuts than 300ms

function msToSecInput(ms) {
  const v = Math.max(0, Number(ms) || 0) / 1000;
  // show centiseconds; trim trailing zeros for readability but keep precision
  return (Math.round(v * 100) / 100).toFixed(2);
}

function secInputToMs(sec) {
  const s = Number(sec);
  if (!Number.isFinite(s) || s < 0) return 0;
  // round to nearest ms after centisecond entry (avoid float junk)
  return Math.max(0, Math.round(s * 1000));
}

function formatDurSec(ms) {
  const d = Math.max(0, Number(ms) || 0) / 1000;
  return (Math.round(d * 100) / 100).toFixed(2);
}

let currentJobId = null;
let pollTimer = null;
let planEdit = null; // {golden, trust, cta}
let planOriginal = null;
/** Job id whose plan is currently shown in planEdit (must match currentJobId). */
let planSourceJobId = null;
let asrCards = []; // left transcript editable cards
let selectedAsr = new Set(); // selected asr indices for bulk add
let planDirty = false; // user is editing plan; avoid poll clobber
let planRenderQueued = false;
let planEventsBound = false;
let asrEventsBound = false;
/** Job id that asrCards currently represent (prevents job1 ASR overwriting job2 UI). */
let asrTranscriptJobId = null;
/** Monotonic token so late loadTranscript responses are ignored. */
let asrLoadToken = 0;

/** Plan undo/redo (before 保存并重剪 only). Depth like a short Office undo stack. */
const PLAN_HIST_MAX = 2;
let planUndoStack = []; // snapshots before edits
let planRedoStack = [];
let planHistSuspended = false; // skip while applying undo/redo / server load
let planTextHistBatch = false; // coalesce continuous typing into one undo step

function planSlotCount(plan) {
  if (!plan) return 0;
  return ["golden", "trust", "cta"].reduce(
    (n, k) => n + ((plan[k] || []).filter((s) => s && !s.removed).length || 0),
    0
  );
}

function snapshotPlan(plan) {
  try {
    return JSON.parse(JSON.stringify(plan || { golden: [], trust: [], cta: [] }));
  } catch (_) {
    return { golden: [], trust: [], cta: [] };
  }
}

function clearPlanHistory() {
  planUndoStack = [];
  planRedoStack = [];
  planTextHistBatch = false;
  updatePlanHistoryButtons();
}

function updatePlanHistoryButtons() {
  const u = $("plan-undo");
  const r = $("plan-redo");
  if (u) {
    u.disabled = planUndoStack.length === 0 || !planEdit;
    u.title =
      planUndoStack.length > 0
        ? `撤销 (Ctrl+Z) · 还可 ${planUndoStack.length} 步`
        : "撤销 (Ctrl+Z) · 保存并重剪前可用";
  }
  if (r) {
    r.disabled = planRedoStack.length === 0 || !planEdit;
    r.title =
      planRedoStack.length > 0
        ? `恢复 (Ctrl+Y) · 还可 ${planRedoStack.length} 步`
        : "恢复 (Ctrl+Y)";
  }
}

/**
 * Push current planEdit onto undo stack BEFORE a structural/text mutation.
 * Call only when about to change planEdit; skips while history suspended.
 */
function pushPlanHistory(label) {
  if (planHistSuspended || !planEdit) return;
  try {
    const snap = snapshotPlan(planEdit);
    planUndoStack.push({ plan: snap, label: label || "编辑", at: Date.now() });
    if (planUndoStack.length > PLAN_HIST_MAX) {
      planUndoStack.splice(0, planUndoStack.length - PLAN_HIST_MAX);
    }
    // new branch invalidates redo
    planRedoStack = [];
    if (label !== "改文案") planTextHistBatch = false;
    updatePlanHistoryButtons();
  } catch (_) {}
}

/** Coalesce continuous typing into a single undo step. */
function pushPlanHistoryForText() {
  if (planTextHistBatch) return;
  pushPlanHistory("改文案");
  planTextHistBatch = true;
}

function undoPlanEdit() {
  if (!planEdit || planUndoStack.length === 0) return false;
  const prev = planUndoStack.pop();
  if (!prev?.plan) return false;
  try {
    planRedoStack.push({ plan: snapshotPlan(planEdit), label: "恢复前", at: Date.now() });
    if (planRedoStack.length > PLAN_HIST_MAX) {
      planRedoStack.splice(0, planRedoStack.length - PLAN_HIST_MAX);
    }
    planHistSuspended = true;
    planTextHistBatch = false;
    planEdit = snapshotPlan(prev.plan);
    planDirty = true;
    planSourceJobId = currentJobId;
    setPlanToolsEnabled(true);
    queueRenderTracks();
    if ($("plan-edit-hint")) {
      $("plan-edit-hint").textContent = `已撤销${prev.label ? "「" + prev.label + "」" : ""}（保存并重剪前有效）`;
    }
  } finally {
    planHistSuspended = false;
    updatePlanHistoryButtons();
  }
  return true;
}

function redoPlanEdit() {
  if (!planEdit || planRedoStack.length === 0) return false;
  const next = planRedoStack.pop();
  if (!next?.plan) return false;
  try {
    planUndoStack.push({ plan: snapshotPlan(planEdit), label: "撤销前", at: Date.now() });
    if (planUndoStack.length > PLAN_HIST_MAX) {
      planUndoStack.splice(0, planUndoStack.length - PLAN_HIST_MAX);
    }
    planHistSuspended = true;
    planTextHistBatch = false;
    planEdit = snapshotPlan(next.plan);
    planDirty = true;
    planSourceJobId = currentJobId;
    setPlanToolsEnabled(true);
    queueRenderTracks();
    if ($("plan-edit-hint")) {
      $("plan-edit-hint").textContent = "已恢复上一步修改（保存并重剪前有效）";
    }
  } finally {
    planHistSuspended = false;
    updatePlanHistoryButtons();
  }
  return true;
}

function clearPlanEditorState() {
  planEdit = null;
  planOriginal = null;
  planDirty = false;
  planSourceJobId = null;
  planRenderQueued = false;
  clearPlanHistory();
  setPlanToolsEnabled(false);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** Off-screen mirror gives reliable content height (textarea scrollHeight is flaky). */
let _mirrorEl = null;
function getTextMirror() {
  if (_mirrorEl && _mirrorEl.isConnected) return _mirrorEl;
  const m = document.createElement("div");
  m.id = "clip-text-mirror";
  m.setAttribute("aria-hidden", "true");
  Object.assign(m.style, {
    position: "fixed",
    left: "-99999px",
    top: "0",
    visibility: "hidden",
    pointerEvents: "none",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    overflow: "hidden",
    boxSizing: "border-box",
  });
  document.body.appendChild(m);
  _mirrorEl = m;
  return m;
}

function measureTextareaContentHeight(el) {
  const cs = window.getComputedStyle(el);
  const mirror = getTextMirror();
  const width = el.clientWidth || el.offsetWidth || 280;
  // copy typography/box used for wrap decisions
  mirror.style.width = `${width}px`;
  mirror.style.font = cs.font;
  mirror.style.fontSize = cs.fontSize;
  mirror.style.fontFamily = cs.fontFamily;
  mirror.style.fontWeight = cs.fontWeight;
  mirror.style.lineHeight = cs.lineHeight;
  mirror.style.letterSpacing = cs.letterSpacing;
  mirror.style.padding = cs.padding;
  mirror.style.border = cs.border;
  mirror.style.boxSizing = cs.boxSizing;
  // trailing newline needs a space to take a line
  const val = el.value || el.placeholder || "";
  mirror.textContent = val.endsWith("\n") ? `${val} ` : val || " ";
  return Math.ceil(mirror.scrollHeight);
}

/** Grow/shrink textarea to fit full text (no clipped second line). */
function fitTextareaHeight(el, { minPx = 48, maxPx = 360 } = {}) {
  if (!el || el.tagName !== "TEXTAREA") return;
  // Prefer explicit rows from content length as a floor (works even if layout is delayed)
  const rowsFloor = suggestTextareaRows(el.value);
  const lineH = (() => {
    const cs = window.getComputedStyle(el);
    const lh = parseFloat(cs.lineHeight);
    if (Number.isFinite(lh) && lh > 0) return lh;
    const fs = parseFloat(cs.fontSize) || 13;
    return fs * 1.5;
  })();
  const padY = (() => {
    const cs = window.getComputedStyle(el);
    return (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0)
      + (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
  })();
  const fromRows = Math.ceil(rowsFloor * lineH + padY + 2);

  let measured = fromRows;
  try {
    if (el.clientWidth > 0) {
      measured = Math.max(fromRows, measureTextareaContentHeight(el) + 2);
    } else {
      // width not ready: use textarea native after clearing height
      el.style.height = "auto";
      el.style.maxHeight = "none";
      void el.offsetHeight;
      measured = Math.max(fromRows, el.scrollHeight + 6);
    }
  } catch (_) {
    measured = fromRows;
  }

  const next = Math.min(maxPx, Math.max(minPx, measured));
  el.style.maxHeight = "none";
  el.style.height = `${next}px`;
  el.style.overflowY = measured > maxPx ? "auto" : "hidden";
  // keep rows attribute in sync for non-JS fallbacks
  el.rows = Math.max(2, Math.min(12, rowsFloor));
}

function clipTextareaLimits(ta) {
  const inAsr = !!(ta?.closest?.("#transcript-list") || ta?.closest?.(".jy-transcript"));
  // High max so long ASR lines fully expand; still cap absurd walls of text
  return inAsr ? { minPx: 48, maxPx: 320 } : { minPx: 52, maxPx: 360 };
}

function fitAllClipTextareas(root) {
  const scope = root && root.querySelectorAll ? root : document;
  const list = scope.querySelectorAll("textarea.clip-text-edit");
  list.forEach((ta) => fitTextareaHeight(ta, clipTextareaLimits(ta)));
}

/** After DOM paint — multiple passes until width is real. */
function scheduleFitClipTextareas(root) {
  const run = () => fitAllClipTextareas(root);
  run();
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      run();
      requestAnimationFrame(run);
    });
  }
  setTimeout(run, 0);
  setTimeout(run, 80);
  setTimeout(run, 200);
}

function suggestTextareaRows(text) {
  const t = String(text || "").trim();
  if (!t) return 2;
  // Left column ~280–320px, 13px font ≈ 14–16 Chinese chars/line; stay conservative
  const hard = (t.match(/\n/g) || []).length + 1;
  const soft = Math.ceil(t.length / 14);
  return Math.min(12, Math.max(2, Math.max(hard, soft)));
}

function statusClass(status) {
  if (status === "success") return "ok";
  if (status === "failed") return "bad";
  if (status === "success_partial") return "warn";
  return "warn";
}

function stageLabel(stage) {
  const map = {
    queued: "排队等待",
    starting: "启动",
    warm_extract: "预热抽音频",
    extract_audio: "抽音频",
    wait_asr: "等待听写槽",
    asr: "听写中（串行稳定）",
    asr_done: "听写完成",
    wait_llm: "等待LLM槽",
    wait_render: "等待渲染槽",
    filter: "过滤无效词",
    llm_plan: "LLM 全量小句提取主要内容并重排反剪",
    clipper: "规则逻辑排序",
    reclip: "按口播重剪",
    render: "渲染成片",
    export: "导出终稿",
    done: "完成",
    failed: "失败",
  };
  return map[stage] || stage || "处理中";
}

/** Parse ISO time (…Z) to epoch ms; 0 if invalid. */
function parseUtcMs(iso) {
  if (!iso) return 0;
  const s = String(iso).trim();
  if (!s) return 0;
  const t = Date.parse(s.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(s) ? s : s + "Z");
  return Number.isFinite(t) ? t : 0;
}

/** Format seconds to 12s / 3分05秒 / 1小时02分 */
function formatWaitDuration(sec) {
  let s = Math.max(0, Math.floor(Number(sec) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}分${String(r).padStart(2, "0")}秒`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}小时${String(mm).padStart(2, "0")}分`;
}

/** Frontend build id; must match server ui_build_expected for version alignment. */
const UI_BUILD = "jy81-plan-undo";
window.__XIAOMIAN_UI_BUILD__ = UI_BUILD;

/** Last text selection in a plan/asr textarea (survives button mousedown blur). */
const _lastTextSel = new WeakMap(); // HTMLTextAreaElement -> {start,end,text}

function isQueueWaiting(data) {
  if (!data) return false;
  const st = String(data.status || "");
  const stage = String(data.stage || "");
  if (["success", "success_partial", "failed"].includes(st)) return false;
  if (st === "queued") return true;
  return (
    stage === "queued" ||
    stage === "wait_asr" ||
    stage === "wait_llm" ||
    stage === "wait_render" ||
    stage === "warm_extract" ||
    stage === "starting"
  );
}

/**
 * Queue subtitle: position + live wait duration + ETA.
 * Only while waiting — not during active asr/render (avoid confusing total time).
 */
function formatQueueWaitInfo(data) {
  if (!isQueueWaiting(data)) return "";

  const st = String(data.status || "");
  const stage = String(data.stage || "");
  const qAt = parseUtcMs(data.queued_at) || parseUtcMs(data.created_at);
  let waitS = 0;
  if (qAt > 0) waitS = Math.max(0, Math.floor((Date.now() - qAt) / 1000));
  else if (data.queue_wait_s != null) waitS = Math.max(0, Number(data.queue_wait_s) || 0);

  const pos = Number(data.queue_pos || 0);
  const total = Number(data.queue_total || 0);
  const parts = [];
  if (pos > 0) parts.push(`第${pos}${total ? "/" + total : ""}位`);
  if (waitS > 0 || st === "queued" || stage.startsWith("wait_") || stage === "warm_extract") {
    parts.push(`已等${formatWaitDuration(waitS)}`);
  }
  // ETA from backend sliding average
  let etaS = data.eta_s != null ? Number(data.eta_s) : NaN;
  if (!Number.isFinite(etaS) || etaS <= 0) {
    // soft client ETA: pos * avg if provided via health later
    etaS = NaN;
  }
  if (Number.isFinite(etaS) && etaS > 0 && isQueueWaiting(data)) {
    parts.push(`预计还需${formatWaitDuration(etaS)}`);
  }
  return parts.join(" · ");
}

function buildStageLine(data) {
  const base = stageLabel(data.stage);
  if (isQueueWaiting(data)) {
    const liveWait = formatQueueWaitInfo(data);
    let detail = String(data.stage_detail || "").trim();
    detail = detail.replace(/^排队(中|等待)?[·\s]*/u, "").trim();
    const liveOnlyWait = (liveWait || "").split(" · ").find((x) => x.startsWith("已等"));
    const livePos = (liveWait || "").split(" · ").find((x) => x.startsWith("第"));
    const liveEta = (liveWait || "").split(" · ").find((x) => x.startsWith("预计还需"));
    if (liveOnlyWait) {
      if (/已等/.test(detail)) detail = detail.replace(/已等[^\s·]*/u, liveOnlyWait);
      else detail = detail ? `${detail} · ${liveOnlyWait}` : liveOnlyWait;
    }
    if (livePos && !/第\d+/.test(detail)) {
      detail = detail ? `${livePos} · ${detail}` : livePos;
    }
    if (liveEta) {
      if (/预计还需/.test(detail)) detail = detail.replace(/预计还需[^\s·]*/u, liveEta);
      else detail = detail ? `${detail} · ${liveEta}` : liveEta;
    }
    if (!detail && liveWait) detail = liveWait;
    return detail ? `${base} · ${detail}` : base;
  }
  const detail = String(data.stage_detail || "").trim();
  return detail ? `${base} · ${detail}` : base;
}

// const UI_BUILD = "jy56-queue-eta";  // duplicate removed

function checkUiVersionAlignment(data) {
  try {
    const expect = String((data && data.ui_build_expected) || "").trim();
    if (!expect) return;
    if (expect === UI_BUILD) return;
    const el = $("health");
    if (el) {
      el.className = "jy-pill bad";
      el.textContent = `前端偏旧(${UI_BUILD}≠${expect}) · 请 Ctrl+F5 并重启服务`;
    }
    // job-run-hint removed from UI; version mismatch shows on health pill only
  } catch (_) {}
}

async function loadHealth() {
  const el = $("health");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const ok = !!data.ok && !!data.ffmpeg;
    el.className = "jy-pill " + (ok ? "ok" : "bad");
    const llmReady = !!data.llm_plan_ready;
    el.textContent = ok
      ? `本机就绪 · ffmpeg${data.ffmpeg ? "✓" : "·"} · ${llmReady ? "用户LLM✓" : "待填LLM/规则"}`
      : `环境异常 · ffmpeg${data.ffmpeg ? "✓" : "缺失"}`;
    // C: hard-refresh if static UI lags behind server queue/UI contract
    checkUiVersionAlignment(data);
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
      // top_hook may be phrase strings or [phrase, score] pairs
      const top = (L.top_hook || [])
        .slice(0, 5)
        .map((x) => (Array.isArray(x) ? x[0] : x))
        .filter(Boolean)
        .join(" / ");
      if ($("learn-stat")) $("learn-stat").textContent = n > 0 ? `已学 ${n} 次` : "人机闭环";
      if ($("learn-hint")) {
        $("learn-hint").textContent =
          n > 0
            ? `已自动学习 ${n} 次（保留 ${L.kept_slots || 0} / 丢弃 ${L.dropped_slots || 0}）${top ? " · 偏好：" + top : ""}`
            : "保存并重剪会自动学习（可在逻辑成片关闭）";
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
    // keep link back to left ASR card when present (for 已加入 color)
    from_asr_idx: s.from_asr_idx != null && s.from_asr_idx !== "" ? Number(s.from_asr_idx) : undefined,
  });
  // flatten legacy 3-section plans into one logical sequence
  const flat = [
    ...(plan.golden || []).map((s) => copySlot(s, "story")),
    ...(plan.trust || []).map((s) => copySlot(s, "story")),
    ...(plan.cta || []).map((s) => copySlot(s, "story")),
  ];
  // Best-effort: when loading auto plan without from_asr_idx, attach left ASR idx by time/text
  try {
    if (Array.isArray(asrCards) && asrCards.length) {
      for (const s of flat) {
        if (s.from_asr_idx != null && Number.isFinite(Number(s.from_asr_idx))) continue;
        let best = -1;
        let bestScore = 0;
        for (let i = 0; i < asrCards.length; i++) {
          const u = asrCards[i];
          if (!u) continue;
          const ov = windowOverlapRatio(s.t0_ms, s.t1_ms, u.t0_ms, u.t1_ms);
          const soft = textSoftMatch(s.text, u.text) ? 0.35 : 0;
          const sc = ov + soft;
          if (sc > bestScore && (ov >= 0.4 || soft > 0)) {
            bestScore = sc;
            best = i;
          }
        }
        if (best >= 0) s.from_asr_idx = best;
      }
    }
  } catch (_) {}
  return {
    golden: flat,
    trust: [],
    cta: [],
  };
}

function activeCount(plan) {
  if (!plan) return 0;
  return (plan.golden || []).filter((s) => !s.removed).length;
}

function setPlanToolsEnabled(on) {
  const apply = $("plan-apply");
  if (apply && "disabled" in apply) apply.disabled = !on;
  // optional legacy controls (may be hidden placeholders without disabled)
  const reset = $("plan-reset");
  if (reset && "disabled" in reset) reset.disabled = !on;
  const balance = $("plan-balance");
  if (balance && "disabled" in balance) balance.disabled = !on;
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
  const slots = (planEdit.golden || []).filter((s) => !s.removed);
  const n = slots.length;
  const durs = slots.map(slotDurMs).filter((d) => d > 0);
  if (!n) {
    el.textContent = "暂无片段";
    return;
  }
  const avg = durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : 0;
  const min = durs.length ? Math.min(...durs) : 0;
  const max = durs.length ? Math.max(...durs) : 0;
  // Short status only — keep header clean
  el.textContent = `${n} 段 · ${formatDurSec(avg)}s均`;
}

function balancePlanDurations() {
  if (!planEdit) return;
  syncPlanFieldsFromDom();
  const slots = [];
  (planEdit.golden || []).forEach((s, idx) => {
    if (!s.removed) slots.push({ k: "golden", idx, s });
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
    $("plan-edit-hint").textContent = `已均分到约 ${formatDurSec(target)}s/段（可再微调）`;
  }
}

function replaceClipWithAsr(toRole, toIdx, asrIdx) {
  if (!planEdit?.[toRole]?.[toIdx]) return;
  const item = asrCards[asrIdx];
  if (!item) return;
  pushPlanHistory("替换片段");
  const leftCard = document.querySelector(`.asr-card[data-idx="${asrIdx}"]`);
  let text = item.text;
  let t0 = Number(item.t0_ms || 0);
  let t1 = Number(item.t1_ms || 0);
  if (leftCard) {
    const ta = leftCard.querySelector(".clip-text-edit");
    const t0s = leftCard.querySelector(".clip-t0s");
    const t1s = leftCard.querySelector(".clip-t1s");
    if (ta) text = ta.value;
    if (t0s) t0 = Math.max(0, secInputToMs(t0s.value || 0));
    if (t1s) t1 = Math.max(t0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
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
  pushPlanHistory("交换位置");
  const a = planEdit[aRole][aIdx];
  const b = planEdit[bRole][bIdx];
  // swap content but keep section role labels
  planEdit[aRole][aIdx] = { ...b, role: roleLabel(aRole) };
  planEdit[bRole][bIdx] = { ...a, role: roleLabel(bRole) };
  planDirty = true;
  queueRenderTracks();
}

function _splitClipByCut(role, idx, cut0ms, cut1ms, textLeft, textRight) {
  if (!planEdit?.[role]?.[idx]) return false;
  const s = planEdit[role][idx];
  const t0 = Math.max(0, Number(s.t0_ms || 0));
  const t1 = Math.max(t0 + TS_MIN_DUR_MS, Number(s.t1_ms || 0));
  const dur = Math.max(TS_MIN_DUR_MS, t1 - t0);
  let c0 = Math.max(t0, Math.min(t1, Number(cut0ms)));
  let c1 = Math.max(t0, Math.min(t1, Number(cut1ms)));
  if (c1 < c0) [c0, c1] = [c1, c0];

  const leftText = String(textLeft ?? "").trim();
  const rightText = String(textRight ?? "").trim();
  const midDelete = !!(leftText && rightText);

  // Mid-delete: force both residuals by character proportion of full original text.
  // Do NOT rely on ASR anchors / large pads here — they were wiping the tail.
  if (midDelete) {
    const full = String(s.text || "");
    const L = Math.max(1, leftText.length);
    const R = Math.max(1, rightText.length);
    const M = Math.max(1, Math.max(0, full.length - L - R) || (c1 > c0 ? 1 : 1));
    // Prefer explicit cut times if sane (inside and leave >=12% each side)
    const leftRoom = c0 - t0;
    const rightRoom = t1 - c1;
    const minSide = Math.max(120, Math.floor(dur * 0.12));
    if (leftRoom < minSide || rightRoom < minSide || c1 - c0 < 40) {
      const totalChars = L + M + R;
      const leftFrac = L / totalChars;
      const midFrac = M / totalChars;
      c0 = t0 + Math.round(dur * leftFrac);
      c1 = t0 + Math.round(dur * (leftFrac + midFrac));
    }
    // clamp so both sides always exist
    c0 = Math.min(Math.max(c0, t0 + minSide), t1 - minSide - 40);
    c1 = Math.max(Math.min(c1, t1 - minSide), c0 + 40);
    if (c1 >= t1 - 40) c1 = t1 - minSide;
    if (c0 <= t0 + 40) c0 = t0 + minSide;
  } else {
    // end-trim cuts can be more aggressive
    if (c1 - c0 < 60) return false;
  }

  const stamp = Date.now().toString(36);
  const base = { ...s, role: roleLabel(role), removed: false };
  // Drop shared from_asr_idx on SPLIT parts so uniqueness won't treat L/R as one module
  delete base.from_asr_idx;
  const parts = [];
  // LEFT
  if (leftText || (!midDelete && c0 - t0 >= 100)) {
    parts.push({
      ...base,
      clip_id: `${s.clip_id || "c"}_L_${stamp}`,
      text: leftText || String(s.text || "").trim(),
      t0_ms: t0,
      t1_ms: Math.max(t0 + TS_MIN_DUR_MS, Math.min(c0, t1 - TS_MIN_DUR_MS)),
      from_asr_idx: undefined,
      split_from: s.clip_id || "",
      split_side: "L",
    });
  }
  // RIGHT — for mid-delete ALWAYS keep when rightText exists
  if (rightText || (!midDelete && t1 - c1 >= 100)) {
    const r0 = midDelete
      ? Math.min(t1 - TS_MIN_DUR_MS, Math.max(c1, t0 + TS_MIN_DUR_MS))
      : Math.min(t1 - TS_MIN_DUR_MS, c1);
    parts.push({
      ...base,
      clip_id: `${s.clip_id || "c"}_R_${stamp}`,
      text: rightText || String(s.text || "").trim(),
      t0_ms: r0,
      t1_ms: t1,
      from_asr_idx: undefined,
      split_from: s.clip_id || "",
      split_side: "R",
    });
  }
  // Absolute safety for mid-delete: if somehow only one part, rebuild both by thirds
  if (midDelete && parts.length < 2) {
    const third = Math.floor(dur / 3);
    pushPlanHistory("删选中(中间)");
    planEdit[role].splice(
      idx,
      1,
      {
        ...base,
        clip_id: `${s.clip_id || "c"}_L_${stamp}`,
        text: leftText,
        t0_ms: t0,
        t1_ms: t0 + third,
        from_asr_idx: undefined,
        split_side: "L",
      },
      {
        ...base,
        clip_id: `${s.clip_id || "c"}_R_${stamp}`,
        text: rightText,
        t0_ms: t0 + 2 * third,
        t1_ms: t1,
        from_asr_idx: undefined,
        split_side: "R",
      }
    );
    planDirty = true;
    updatePlanHistoryButtons();
    return true;
  }
  if (!parts.length) return false;
  pushPlanHistory(midDelete ? "删选中(中间)" : "裁剪片段");
  planEdit[role].splice(idx, 1, ...parts);
  planDirty = true;
  updatePlanHistoryButtons();
  return true;
}

/** Chinese/English speaking-time weight of one code unit (for cut mapping). */
function _charSpeakWeight(ch) {
  if (!ch) return 0;
  // CJK ideographs: full unit
  if (/[\u4e00-\u9fff\u3400-\u4dbf]/.test(ch)) return 1.0;
  // fullwidth digits / letters roughly half-ish spoken length
  if (/[０-９Ａ-Ｚａ-ｚ]/.test(ch)) return 0.55;
  // ascii letters
  if (/[A-Za-z]/.test(ch)) return 0.45;
  // numbers (often price junk) spoken a bit faster but still material
  if (/[0-9]/.test(ch)) return 0.5;
  // Chinese punctuation — short breath, keep tiny weight for alignment stability
  if (/[，。！？、；：…—·,.!?;:\s]/.test(ch)) return 0.12;
  // other symbols
  return 0.2;
}

/**
 * Map character index [0..text.length] -> absolute ms within [t0,t1]
 * using cumulative Chinese speech weights (not naive byte length ratio).
 * Optional ASR clause anchors correct drift for long clips.
 */
function mapCharIndexToMs(text, charIdx, t0, t1, anchors) {
  const full = String(text || "");
  const n = full.length;
  const i = Math.max(0, Math.min(n, Number(charIdx) || 0));
  const dur = Math.max(300, Number(t1) - Number(t0));
  if (n <= 0) return Number(t0);
  if (i <= 0) return Number(t0);
  if (i >= n) return Number(t1);

  // cumulative weights at each boundary 0..n
  const w = new Array(n);
  let total = 0;
  for (let k = 0; k < n; k++) {
    w[k] = _charSpeakWeight(full[k]);
    total += w[k];
  }
  if (total <= 1e-6) total = n;
  const cum = new Array(n + 1);
  cum[0] = 0;
  for (let k = 0; k < n; k++) cum[k + 1] = cum[k] + w[k];

  // base: weighted proportional time
  let ms = Number(t0) + Math.round((cum[i] / total) * dur);

  // refine with ASR clauses that fall inside this clip window
  if (Array.isArray(anchors) && anchors.length) {
    // pick surrounding anchors by overlapping [t0,t1] text fragments
    const hits = [];
    for (const a of anchors) {
      const at0 = Number(a.t0_ms || 0);
      const at1 = Number(a.t1_ms || 0);
      if (at1 <= t0 + 40 || at0 >= t1 - 40) continue;
      const atext = String(a.text || "").trim();
      if (!atext) continue;
      // locate atext inside full text (prefer first occurrence that fits window order)
      let pos = full.indexOf(atext);
      // loose: strip spaces
      if (pos < 0) {
        const compact = full.replace(/\s+/g, "");
        const ac = atext.replace(/\s+/g, "");
        const cp = compact.indexOf(ac);
        if (cp >= 0) {
          // map compact pos back roughly
          let seen = 0;
          pos = 0;
          for (let x = 0; x < full.length; x++) {
            if (/\s/.test(full[x])) continue;
            if (seen === cp) {
              pos = x;
              break;
            }
            seen++;
          }
        }
      }
      if (pos < 0) continue;
      hits.push({
        i0: pos,
        i1: pos + atext.length,
        t0: Math.max(t0, at0),
        t1: Math.min(t1, at1),
      });
    }
    hits.sort((a, b) => a.i0 - b.i0 || a.t0 - b.t0);
    // find bracketing anchors for character i
    let prev = null;
    let next = null;
    for (const h of hits) {
      if (h.i1 <= i) prev = h;
      if (h.i0 >= i && !next) next = h;
    }
    // if inside an anchor clause, interpolate within that clause
    for (const h of hits) {
      if (i >= h.i0 && i <= h.i1) {
        const localW = Math.max(1e-6, cum[h.i1] - cum[h.i0]);
        const frac = (cum[i] - cum[h.i0]) / localW;
        ms = h.t0 + Math.round(frac * Math.max(200, h.t1 - h.t0));
        return Math.max(t0, Math.min(t1, ms));
      }
    }
    if (prev && next && next.i0 > prev.i1) {
      const spanW = Math.max(1e-6, cum[next.i0] - cum[prev.i1]);
      const frac = (cum[i] - cum[prev.i1]) / spanW;
      ms = prev.t1 + Math.round(frac * Math.max(100, next.t0 - prev.t1));
    } else if (prev) {
      // after last known anchor: scale remaining weights into remaining time
      const remW = Math.max(1e-6, total - cum[prev.i1]);
      const frac = (cum[i] - cum[prev.i1]) / remW;
      ms = prev.t1 + Math.round(frac * Math.max(100, t1 - prev.t1));
    } else if (next) {
      const remW = Math.max(1e-6, cum[next.i0]);
      const frac = cum[i] / remW;
      ms = t0 + Math.round(frac * Math.max(100, next.t0 - t0));
    }
  }
  return Math.max(t0, Math.min(t1, ms));
}

/** ASR cards overlapping a plan slot — used as timing anchors. */
function getCutAnchorsForSlot(slot) {
  const t0 = Math.max(0, Number(slot?.t0_ms || 0));
  const t1 = Math.max(t0 + TS_MIN_DUR_MS, Number(slot?.t1_ms || 0));
  const list = [];
  for (const u of asrCards || []) {
    const a0 = Number(u.t0_ms || 0);
    const a1 = Number(u.t1_ms || 0);
    if (a1 <= t0 + 20 || a0 >= t1 - 20) continue;
    list.push({ text: u.text, t0_ms: a0, t1_ms: a1 });
  }
  // also include other plan slots that look like short source crumbs inside range
  return list;
}

/**
 * Delete a sub-range inside one clip by selected Chinese text.
 * Uses speech-weight mapping + ASR clause anchors (not plain char ratio).
 */
function cutSelectedTextInClip(role, idx, ta) {
  if (!planEdit?.[role]?.[idx] || !ta) return false;
  const text = String(ta.value || "");
  // Prefer live selection; fall back to cached selection (button click blurs textarea)
  let a = Number(ta.selectionStart ?? 0);
  let b = Number(ta.selectionEnd ?? 0);
  const cached = _lastTextSel.get(ta);
  if (!(b > a) && cached && cached.text === text && cached.end > cached.start) {
    a = cached.start;
    b = cached.end;
  }
  if (!(b > a) || !text.length) {
    alert("请先在口播框里用鼠标选中要删掉的那几个字（例如“侧面看险薄，板板真正”），再点「删选中文字段」");
    return false;
  }
  // expand selection only for pure latin/digit tokens mid-selection
  while (a > 0 && /[0-9A-Za-z]/.test(text[a - 1]) && /[0-9A-Za-z]/.test(text[a])) a -= 1;
  while (b < text.length && /[0-9A-Za-z]/.test(text[b - 1]) && /[0-9A-Za-z]/.test(text[b])) b += 1;

  const s = planEdit[role][idx];
  // Always use the textarea text as source of truth for left/right split
  const stripEdgePunct = (s) =>
    String(s || "")
      .replace(/^[\s，,。.!！?？、；;：:]+/u, "")
      .replace(/[\s，,。.!！?？、；;：:]+$/u, "")
      .trim();
  const textLeft = stripEdgePunct(text.slice(0, a));
  const textRight = stripEdgePunct(text.slice(b));
  const midDelete = !!(textLeft && textRight);

  const t0 = Math.max(0, Number(s.t0_ms || 0));
  const t1 = Math.max(t0 + TS_MIN_DUR_MS, Number(s.t1_ms || 0));
  const dur = Math.max(1, t1 - t0);

  // Mid-delete: PURE character proportion — most reliable for Chinese short phrases.
  // (Weighted/anchor mapping was collapsing the tail on mixed-length clauses.)
  let cut0;
  let cut1;
  if (midDelete) {
    cut0 = t0 + Math.round((a / Math.max(1, text.length)) * dur);
    cut1 = t0 + Math.round((b / Math.max(1, text.length)) * dur);
    // tiny pad only
    const pad = Math.min(40, Math.floor(dur * 0.01));
    cut0 = Math.max(t0 + 80, cut0 - pad);
    cut1 = Math.min(t1 - 80, cut1 + pad);
  } else {
    const anchors = getCutAnchorsForSlot(s);
    cut0 = mapCharIndexToMs(text, a, t0, t1, anchors);
    cut1 = mapCharIndexToMs(text, b, t0, t1, anchors);
    if (cut1 < cut0) [cut0, cut1] = [cut1, cut0];
    const pad = Math.min(80, Math.max(20, Math.floor(dur * 0.01)));
    cut0 = Math.max(t0, cut0 - pad);
    cut1 = Math.min(t1, cut1 + pad);
  }
  if (cut1 <= cut0) {
    cut0 = t0 + Math.floor((a / Math.max(1, text.length)) * dur);
    cut1 = t0 + Math.ceil((b / Math.max(1, text.length)) * dur);
  }

  const ok = _splitClipByCut(role, idx, cut0, cut1, textLeft, textRight);
  if (!ok) {
    alert("选中范围太短或会裁空整段，请扩大选中或改用「裁掉秒数」");
    return false;
  }
  // clear cache after successful cut
  try {
    _lastTextSel.delete(ta);
  } catch (_) {}
  if ($("plan-edit-hint")) {
    const sideNote =
      textLeft && textRight
        ? ` · 前后保留（后：${textRight.slice(0, 12)}${textRight.length > 12 ? "…" : ""}）`
        : textLeft
          ? " · 仅保留前段"
          : textRight
            ? " · 仅保留后段"
            : "";
    $("plan-edit-hint").textContent = `已裁掉 ${(cut0 / 1000).toFixed(2)}s–${(cut1 / 1000).toFixed(2)}s${sideNote}，请点重剪生效`;
  }
  queueRenderTracks();
  return true;
}

/** Explicit second-range cut inside one clip: cutFromS/cutToS absolute seconds on source. */
function cutSecondsInClip(role, idx, fromS, toS) {
  if (!planEdit?.[role]?.[idx]) return false;
  const s = planEdit[role][idx];
  const t0 = Math.max(0, Number(s.t0_ms || 0));
  const t1 = Math.max(t0 + TS_MIN_DUR_MS, Number(s.t1_ms || 0));
  let c0 = secInputToMs(fromS);
  let c1 = secInputToMs(toS);
  if (!(c1 > c0)) {
    alert("结束秒必须大于开始秒");
    return false;
  }
  if (c0 < t0 - 50 || c1 > t1 + 50) {
    alert(`裁剪范围需在当前片段内：${(t0 / 1000).toFixed(2)}s ~ ${(t1 / 1000).toFixed(2)}s`);
    return false;
  }
  c0 = Math.max(t0, c0);
  c1 = Math.min(t1, c1);
  // keep full text on both sides (user can edit); middle spoken part removed
  const text = String(s.text || "");
  const ok = _splitClipByCut(role, idx, c0, c1, text, text);
  if (!ok) {
    alert("裁剪后没有可用片段（范围过大）");
    return false;
  }
  if ($("plan-edit-hint")) {
    $("plan-edit-hint").textContent = `已裁掉 ${(c0 / 1000).toFixed(2)}s–${(c1 / 1000).toFixed(2)}s，请点重剪生效`;
  }
  queueRenderTracks();
  return true;
}

// Single logical track (compat keys kept for plan.json schema)
const TRACK_ORDER = ["golden"];

function roleLabel(trackKey) {
  return "story";
}

function moveClip(fromRole, fromIdx, toRole, toIdx) {
  if (!planEdit?.[fromRole]?.[fromIdx]) return;
  if (!planEdit[toRole]) planEdit[toRole] = [];
  // same track no-op
  if (fromRole === toRole && (toIdx === fromIdx || toIdx === fromIdx + 1)) return;
  pushPlanHistory("调整顺序");
  const item = planEdit[fromRole].splice(fromIdx, 1)[0];
  if (!item) return;
  item.role = roleLabel(toRole);
  let insertAt = Math.max(0, Math.min(Number(toIdx) || 0, planEdit[toRole].length));
  // after splice, indices after fromIdx shift down when same track
  if (fromRole === toRole && fromIdx < insertAt) insertAt = Math.max(0, insertAt - 1);
  // uniqueness: if same module already exists at target track, drop the moved copy
  // (should not happen on same track, but protects cross-track / re-entry)
  const conflict = planEdit[toRole].findIndex((s) => s && !s.removed && sameModule(s, item));
  if (conflict >= 0) {
    // keep existing, discard moved duplicate
    planDirty = true;
    enforcePlanUniqueness(toRole);
    queueRenderTracks();
    if ($("plan-edit-hint")) {
      $("plan-edit-hint").textContent = "模块已存在，已保持唯一不重复";
    }
    updatePlanHistoryButtons();
    return;
  }
  planEdit[toRole].splice(insertAt, 0, item);
  enforcePlanUniqueness(toRole);
  planDirty = true;
  queueRenderTracks();
  if ($("plan-edit-hint")) {
    $("plan-edit-hint").textContent = "已调整模块顺序（需点「保存并重剪」才生效）";
  }
  updatePlanHistoryButtons();
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
    if (t0s) planEdit[role][idx].t0_ms = Math.max(0, secInputToMs(t0s.value || 0));
    if (t1s) {
      const v0 = planEdit[role][idx].t0_ms || 0;
      const v1 = Math.max(v0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
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

function renderTracks(plan, opts = {}) {
  const forceServer = !!opts.forceServer;
  // If planEdit belongs to another job, never prefer it
  if (planSourceJobId && currentJobId && planSourceJobId !== currentJobId) {
    planEdit = null;
    planOriginal = null;
    planDirty = false;
    planSourceJobId = null;
  }
  // prefer editable working copy only when same job + not force-reload from server
  const src =
    !forceServer && planEdit && planSourceJobId === currentJobId
      ? planEdit
      : clonePlan(plan || {});
  if (
    forceServer ||
    (!planEdit && (plan?.golden?.length || plan?.trust?.length || plan?.cta?.length))
  ) {
    planHistSuspended = true;
    try {
      if (plan?.golden?.length || plan?.trust?.length || plan?.cta?.length) {
        planEdit = clonePlan(plan);
        planOriginal = clonePlan(plan);
        planSourceJobId = currentJobId;
        planDirty = false;
        clearPlanHistory();
        setPlanToolsEnabled(true);
      } else if (forceServer) {
        planEdit = { golden: [], trust: [], cta: [] };
        planOriginal = clonePlan(planEdit);
        planSourceJobId = currentJobId;
        planDirty = false;
        clearPlanHistory();
        setPlanToolsEnabled(false);
      }
    } finally {
      planHistSuspended = false;
    }
  } else if (planEdit && !planSourceJobId && currentJobId) {
    planSourceJobId = currentJobId;
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
        const a = msToSecInput(s.t0_ms  || 0);
        const b = msToSecInput(s.t1_ms  || 0);
        const removed = !!s.removed;
        return `<div class="jy-clip ${role} ${removed ? "removed" : ""}" draggable="true" data-role="${key}" data-idx="${idx}" data-id="${escapeHtml(s.clip_id || "")}">
          <div class="clip-top">
            <span class="clip-drag" title="按住拖动调整位置">⠿</span>
            <span class="clip-badge">逻辑 #${idx + 1}</span>
            <div class="clip-order-btns">
              <button type="button" class="clip-up" title="上移" ${idx === 0 ? "disabled" : ""}>↑</button>
              <button type="button" class="clip-down" title="下移" ${idx >= list.length - 1 ? "disabled" : ""}>↓</button>
            </div>
            <button type="button" class="clip-x" title="${removed ? "恢复" : "删除"}">${removed ? "+" : "×"}</button>
          </div>
          <textarea class="clip-text-edit" rows="${suggestTextareaRows(s.text)}" placeholder="编辑这段口播词…">${escapeHtml(s.text || "")}</textarea>
          <div class="clip-time-row">
            <label>开始(s)<input class="clip-t0s" type="number" step="${TS_UI_STEP}" min="0" value="${a}" /></label>
            <label>结束(s)<input class="clip-t1s" type="number" step="${TS_UI_STEP}" min="0" value="${b}" /></label>
          </div>
          <div class="clip-time-row cut-row">
            <label>裁掉从(s)<input class="clip-cut0s" type="number" step="${TS_UI_STEP}" min="0" value="" placeholder="${a}" /></label>
            <label>到(s)<input class="clip-cut1s" type="number" step="${TS_UI_STEP}" min="0" value="" placeholder="${b}" /></label>
            <button type="button" class="clip-cut-range" title="按秒数裁掉中间一段">裁掉这段</button>
            <button type="button" class="clip-cut-sel" title="删除选中中文对应时间（语速权重+口播锚点，比均分更准）">删选中文字段</button>
          </div>
          <div class="meta">时长 ${formatDurSec((Number(s.t1_ms || 0) - Number(s.t0_ms || 0)))}s · 可拖动手柄/点 ↑↓ 调整顺序</div>
        </div>`;
      })
      .join("");
  };

  // single logical sequence in golden — always unique modules
  if (planEdit?.golden) {
    const n = enforcePlanUniqueness("golden");
    if (n > 0 && $("plan-edit-hint")) {
      $("plan-edit-hint").textContent = `已去掉 ${n} 个重复模块（成片内唯一）`;
    }
  }
  const goldenList = planEdit?.golden || src.golden || [];
  if ($("golden-track")) $("golden-track").innerHTML = mk(goldenList, "story", "golden");
  if ($("trust-track")) $("trust-track").innerHTML = "";
  if ($("cta-track")) $("cta-track").innerHTML = "";
  ensurePlanEventsBound();
  updatePlanHint();
  scheduleFitClipTextareas($("golden-track"));
  // Keep left ASR card colors in sync with right plan membership
  try {
    refreshAsrInPlanMarks();
  } catch (_) {}
  updatePlanHistoryButtons();

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
      if (el.classList?.contains("clip-text-edit")) fitTextareaHeight(el);
    }
  }
}

function ensurePlanEventsBound() {
  const root = document.querySelector(".jy-timeline-panel");
  if (!root) return;
  if (planEventsBound) return;
  planEventsBound = true;

  // text/time edits: update model only, NO full re-render (smooth typing)
  root.addEventListener("input", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    if (!t.classList.contains("clip-text-edit") && !t.classList.contains("clip-t0s") && !t.classList.contains("clip-t1s")) return;
    const card = t.closest(".jy-clip");
    if (!card || !planEdit) return;
    // only plan track cards (golden), not left ASR
    if (card.classList.contains("asr-card")) return;
    const role = card.dataset.role;
    const idx = Number(card.dataset.idx);
    if (!planEdit?.[role]?.[idx]) return;
    if (t.classList.contains("clip-text-edit")) {
      pushPlanHistoryForText();
    } else {
      pushPlanHistory("改时间");
    }
    planDirty = true;
    if (t.classList.contains("clip-text-edit")) {
      planEdit[role][idx].text = t.value;
      fitTextareaHeight(t, clipTextareaLimits(t));
    } else if (t.classList.contains("clip-t0s")) {
      planEdit[role][idx].t0_ms = Math.max(0, secInputToMs(t.value || 0));
    } else if (t.classList.contains("clip-t1s")) {
      const v0 = planEdit[role][idx].t0_ms || 0;
      planEdit[role][idx].t1_ms = Math.max(v0 + TS_MIN_DUR_MS, secInputToMs(t.value || 0));
    }
  });

  // Cache textarea selection BEFORE button steals focus (mousedown on 删选中文字段)
  root.addEventListener(
    "mousedown",
    (e) => {
      const t = e.target;
      if (t && t.classList && t.classList.contains("clip-text-edit")) {
        try {
          const start = t.selectionStart;
          const end = t.selectionEnd;
          if (end > start) {
            _lastTextSel.set(t, { start, end, text: String(t.value || "") });
          }
        } catch (_) {}
        return;
      }
      // clicking cut button: freeze whatever selection last was in this card
      const btn = e.target.closest?.("button.clip-cut-sel");
      if (!btn) return;
      const card = btn.closest(".jy-clip");
      const ta = card?.querySelector?.(".clip-text-edit");
      if (!ta) return;
      try {
        // if textarea still has selection at mousedown (rare), capture it
        if (ta.selectionEnd > ta.selectionStart) {
          _lastTextSel.set(ta, {
            start: ta.selectionStart,
            end: ta.selectionEnd,
            text: String(ta.value || ""),
          });
        }
      } catch (_) {}
    },
    true
  );
  root.addEventListener(
    "mouseup",
    (e) => {
      const t = e.target;
      if (!(t && t.classList && t.classList.contains("clip-text-edit"))) return;
      try {
        if (t.selectionEnd > t.selectionStart) {
          _lastTextSel.set(t, {
            start: t.selectionStart,
            end: t.selectionEnd,
            text: String(t.value || ""),
          });
        }
      } catch (_) {}
    },
    true
  );
  root.addEventListener(
    "keyup",
    (e) => {
      const t = e.target;
      if (!(t && t.classList && t.classList.contains("clip-text-edit"))) return;
      try {
        if (t.selectionEnd > t.selectionStart) {
          _lastTextSel.set(t, {
            start: t.selectionStart,
            end: t.selectionEnd,
            text: String(t.value || ""),
          });
        }
      } catch (_) {}
    },
    true
  );

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
      if (t0s) planEdit[role][idx].t0_ms = Math.max(0, secInputToMs(t0s.value || 0));
      if (t1s) {
        const v0 = planEdit[role][idx].t0_ms || 0;
        planEdit[role][idx].t1_ms = Math.max(v0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
      }
    }

    if (btn.classList.contains("clip-x") || btn.classList.contains("clip-del-hard")) {
      if (!planEdit?.[role]?.[idx]) return;
      pushPlanHistory("删除片段");
      // hard delete whole clip
      const removedItem = planEdit[role].splice(idx, 1)[0];
      planDirty = true;
      if ($("plan-edit-hint")) {
        const t = (removedItem?.text || "").slice(0, 18);
        $("plan-edit-hint").textContent = `已删除整段：${t}${t.length >= 18 ? "…" : ""}（可撤销 · 需点重剪才出片）`;
      }
      queueRenderTracks();
      updatePlanHistoryButtons();
      return;
    }
    if (btn.classList.contains("clip-cut-sel")) {
      const ta2 = card.querySelector(".clip-text-edit");
      // re-focus textarea so selection APIs stay consistent; use cache if blur cleared it
      try {
        ta2?.focus?.({ preventScroll: true });
      } catch (_) {}
      cutSelectedTextInClip(role, idx, ta2);
      return;
    }
    if (btn.classList.contains("clip-cut-range")) {
      const c0 = card.querySelector(".clip-cut0s");
      const c1 = card.querySelector(".clip-cut1s");
      if (!c0?.value || !c1?.value) {
        alert("请填写「裁掉从(s)」和「到(s)」，例如 45.2 到 47.0");
        return;
      }
      cutSecondsInClip(role, idx, Number(c0.value), Number(c1.value));
      return;
    }
    if (btn.classList.contains("clip-up")) {
      if (idx > 0) {
        // insert before previous item
        moveClip(role, idx, role, idx - 1);
      }
      return;
    }
    if (btn.classList.contains("clip-down")) {
      if (planEdit?.[role] && idx < planEdit[role].length - 1) {
        // insert after next item => target index = idx + 2 (before adjustment)
        moveClip(role, idx, role, idx + 2);
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
    // allow drag from handle / badge / empty card chrome; block form controls
    if (e.target.closest("button, textarea, input, label, .clip-time-row, .cut-row")) {
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
      if (t0s) planEdit[role][idx].t0_ms = Math.max(0, secInputToMs(t0s.value || 0));
      if (t1s) {
        const v0 = planEdit[role][idx].t0_ms || 0;
        planEdit[role][idx].t1_ms = Math.max(v0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
      }
    }
    card.classList.add("dragging");
    try {
      e.dataTransfer.effectAllowed = "move";
      // some Chromium builds need text/plain set before drop works across panels
      e.dataTransfer.setData(
        "text/plain",
        JSON.stringify({ source: "plan", role: card.dataset.role, idx: Number(card.dataset.idx) })
      );
      e.dataTransfer.setData(
        "application/x-xiaomian-clip",
        JSON.stringify({ source: "plan", role: card.dataset.role, idx: Number(card.dataset.idx) })
      );
    } catch (_) {}
  });

  root.addEventListener("dragend", (e) => {
    const card = e.target.closest?.(".jy-clip");
    card?.classList.remove("dragging");
    root.querySelectorAll(".jy-track-body").forEach((t) => t.classList.remove("drag-over"));
    root.querySelectorAll(".jy-clip").forEach((c) => c.classList.remove("drag-over-left", "drag-over-right"));
  });

  // dragover must always preventDefault over the whole panel so drop is allowed
  root.addEventListener("dragover", (e) => {
    const track =
      e.target.closest?.(".jy-track-body") ||
      e.target.closest?.("#golden-track") ||
      (root.contains(e.target) ? $("golden-track") : null);
    if (!track || !root.contains(track)) return;
    e.preventDefault();
    try {
      // left ASR drag = copy/add; right plan drag = move/reorder
      const types = Array.from(e.dataTransfer?.types || []);
      const asrDrag = types.includes("application/x-xiaomian-clip") || types.includes("text/plain");
      e.dataTransfer.dropEffect = asrDrag ? "copy" : "move";
    } catch (_) {
      e.dataTransfer.dropEffect = "copy";
    }
    track.classList.add("drag-over");
    const overClip = e.target.closest(".jy-clip");
    root.querySelectorAll(".jy-clip").forEach((c) =>
      c.classList.remove("drag-over-left", "drag-over-right", "drag-over-replace")
    );
    // only show insert cues (before/after) — never replace-by-drop
    if (overClip && track.contains(overClip) && !overClip.classList.contains("asr-card")) {
      const rect = overClip.getBoundingClientRect();
      const mid = rect.top + rect.height / 2;
      if (e.clientY < mid) overClip.classList.add("drag-over-left");
      else overClip.classList.add("drag-over-right");
    }
  });

  root.addEventListener("drop", (e) => {
    // Product rule: drag left ASR onto a position => INSERT add.
    // Do NOT replace existing plan cards by dropping on center.
    const track =
      e.target.closest?.(".jy-track-body") ||
      e.target.closest?.("#golden-track") ||
      (root.contains(e.target) ? $("golden-track") : null);
    if (!track || !root.contains(track)) return;
    e.preventDefault();
    e.stopPropagation();
    track.classList.remove("drag-over");
    root.querySelectorAll(".jy-clip").forEach((c) =>
      c.classList.remove("drag-over-left", "drag-over-right", "drag-over-replace")
    );

    let payload = null;
    const raw =
      e.dataTransfer.getData("application/x-xiaomian-clip") ||
      e.dataTransfer.getData("text/plain") ||
      "{}";
    try {
      payload = JSON.parse(raw);
    } catch (_) {
      if ($("plan-edit-hint")) $("plan-edit-hint").textContent = "拖放失败：数据无效，请重试";
      return;
    }
    if (!payload || typeof payload !== "object") return;

    const toRole = (track.id || "golden-track").replace("-track", "") || "golden";
    if (!TRACK_ORDER.includes(toRole)) return;
    if (!planEdit) {
      planEdit = { golden: [], trust: [], cta: [] };
      planOriginal = clonePlan(planEdit);
      planSourceJobId = currentJobId;
      setPlanToolsEnabled(true);
    } else if (!planSourceJobId) {
      planSourceJobId = currentJobId;
    }
    if (!planEdit[toRole]) planEdit[toRole] = [];

    // resolve insert index from pointer position
    const overClip = e.target.closest(".jy-clip");
    let toIdx = planEdit[toRole].length;
    if (overClip && track.contains(overClip) && overClip.dataset.role === toRole) {
      const overIdx = Number(overClip.dataset.idx);
      const rect = overClip.getBoundingClientRect();
      const mid = rect.top + rect.height / 2;
      toIdx = e.clientY < mid ? overIdx : overIdx + 1;
    } else {
      // drop on empty zone / hint: append
      toIdx = planEdit[toRole].length;
    }

    // Left ASR -> add OR move existing unique module to drop position
    if (payload.source === "asr") {
      const aidx = Number(payload.idx);
      if (!asrCards[aidx]) {
        if ($("plan-edit-hint")) $("plan-edit-hint").textContent = "加入失败：找不到口播句";
        return;
      }
      const r = insertAsrUnique(aidx, toIdx);
      if (r.action === "empty") {
        if ($("plan-edit-hint")) $("plan-edit-hint").textContent = "加入失败：口播文案为空";
        return;
      }
      planDirty = true;
      setPlanToolsEnabled(true);
      queueRenderTracks();
      refreshAsrInPlanMarks();
      if ($("plan-edit-hint")) {
        if (r.action === "add") {
          $("plan-edit-hint").textContent = `已添加到位置 #${(r.index >= 0 ? r.index : toIdx) + 1}（唯一 · 需保存并重剪）`;
        } else if (r.action === "move") {
          $("plan-edit-hint").textContent = `该口播已在成片中，已移到位置 #${(r.index >= 0 ? r.index : toIdx) + 1}（不重复）`;
        } else {
          $("plan-edit-hint").textContent = "该口播已在逻辑成片中（每句只保留一份）";
        }
      }
      return;
    }

    // Right plan card reorder / move
    const fromRole = payload.role;
    const fromIdx = Number(payload.idx);
    if (fromRole == null || Number.isNaN(fromIdx)) return;
    if (!planEdit?.[fromRole]?.[fromIdx]) return;
    moveClip(fromRole, fromIdx, toRole, Math.max(0, toIdx));
  });
}

function isLearnEnabled() {
  const el = $("plan-learn");
  // Default ON: auto-learn reverse edits unless user unchecks.
  // If checkbox missing, still learn (product policy: 自动学习反剪).
  if (!el || el.type !== "checkbox") return true;
  return !!el.checked;
}

async function applyPlanEdit() {
  if (!currentJobId || !planEdit) return;
  syncPlanFieldsFromDom();
  // force unique modules before submit
  enforcePlanUniqueness("golden");
  // also drop empty-text or zero-length slots
  const clean = (arr) => {
    const seen = [];
    return (arr || [])
      .filter((s) => s && !s.removed)
      .map((s) => {
        const t0 = Math.max(0, Number(s.t0_ms || 0));
        let t1 = Math.max(t0 + TS_MIN_DUR_MS, Number(s.t1_ms || 0));
        return {
          clip_id: s.clip_id,
          role: s.role,
          text: String(s.text || "").trim(),
          t0_ms: t0,
          t1_ms: t1,
          score: Number(s.score || 0),
          from_asr_idx: s.from_asr_idx,
        };
      })
      .filter((s) => s.t1_ms > s.t0_ms)
      .filter((s) => {
        // final uniqueness net (time / text / asr)
        if (seen.some((x) => sameModule(x, s))) return false;
        seen.push(s);
        return true;
      })
      .map(({ from_asr_idx, ...rest }) => rest); // strip client-only field
  };
  // Auto-learn reverse cut by default (plan-learn checkbox, default checked)
  const learn = isLearnEnabled();
  const payload = {
    reclip: true,
    learn: !!learn,
    golden: clean(planEdit.golden),
    trust: [],
    cta: [],
  };
  // hard safety: never send removed cards
  const allTexts = [...payload.golden, ...payload.trust, ...payload.cta].map((s) => s.text);
  if (!payload.golden.length && !payload.trust.length && !payload.cta.length) {
    alert("请至少保留一个片段");
    return;
  }
  if ($("plan-edit-hint")) {
    $("plan-edit-hint").textContent = learn
      ? `正在重剪 ${allTexts.length} 段（学习）…`
      : `正在重剪 ${allTexts.length} 段…`;
  }
  if ($("plan-apply") && "disabled" in $("plan-apply")) $("plan-apply").disabled = true;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}/plan`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "应用失败");
    // accept server result and clear dirty lock so next server plan can paint
    // undo stack is intentionally cleared after 保存并重剪
    clearPlanEditorState();
    // force preview reload after reclip
    const video = $("preview");
    if (video) {
      try {
        video.pause();
      } catch (_) {}
      video.removeAttribute("src");
      video.load();
    }
    renderJob(data);
    await loadJobs();
    pollJob(currentJobId);
    loadHealth();
    if ($("plan-edit-hint")) {
      $("plan-edit-hint").textContent = `已提交重剪（${allTexts.length} 段），完成后自动刷新预览`;
    }
  } catch (e) {
    alert(String(e.message || e));
    if ($("plan-apply") && "disabled" in $("plan-apply")) $("plan-apply").disabled = false;
  }
}

async function clearLearningData() {
  if (!confirm("确定清空之前的学习数据？此操作会重置全局口味偏好（可重新学）。")) return;
  try {
    const res = await fetch("/api/learning/clear", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "清空失败");
    if ($("plan-learn")) $("plan-learn").checked = false;
    try {
      localStorage.setItem("clipper_learn_on_reclip", "0");
    } catch (_) {}
    await loadHealth();
    if ($("plan-edit-hint")) $("plan-edit-hint").textContent = "学习数据已清空";
    alert("已清空学习数据");
  } catch (e) {
    alert(String(e.message || e));
  }
}

function setupPlanTools() {
  // Toolbar: 撤销/恢复 + 保存并重剪 + 自动学习
  $("plan-reset")?.addEventListener?.("click", () => {
    if (!planOriginal) return;
    pushPlanHistory("还原初始");
    planEdit = clonePlan(planOriginal);
    planDirty = true;
    queueRenderTracks();
    updatePlanHistoryButtons();
  });
  $("plan-balance")?.addEventListener?.("click", () => {
    pushPlanHistory("均分时长");
    balancePlanDurations();
  });
  $("plan-apply")?.addEventListener?.("click", applyPlanEdit);
  $("plan-undo")?.addEventListener?.("click", (e) => {
    e.preventDefault();
    undoPlanEdit();
  });
  $("plan-redo")?.addEventListener?.("click", (e) => {
    e.preventDefault();
    redoPlanEdit();
  });
  // Office-like shortcuts (only when not typing in plain inputs outside plan, or always when plan focused)
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const key = String(e.key || "").toLowerCase();
    // Ctrl+Z undo / Ctrl+Y redo / Ctrl+Shift+Z redo
    if (key === "z" && !e.shiftKey) {
      if (!planEdit || planUndoStack.length === 0) return;
      // allow even inside textarea (Word-like for plan edits)
      e.preventDefault();
      undoPlanEdit();
      return;
    }
    if (key === "y" || (key === "z" && e.shiftKey)) {
      if (!planEdit || planRedoStack.length === 0) return;
      e.preventDefault();
      redoPlanEdit();
    }
  });
  $("learn-clear")?.addEventListener?.("click", clearLearningData);
  const learnEl = $("plan-learn");
  if (learnEl && learnEl.type === "checkbox") {
    // default ON; only honor explicit user opt-out stored as "0"
    try {
      const v = localStorage.getItem("clipper_learn_on_reclip");
      learnEl.checked = v !== "0";
      if (v == null) localStorage.setItem("clipper_learn_on_reclip", "1");
    } catch (_) {
      learnEl.checked = true;
    }
    learnEl.addEventListener("change", () => {
      try {
        localStorage.setItem("clipper_learn_on_reclip", learnEl.checked ? "1" : "0");
      } catch (_) {}
      if ($("plan-edit-hint")) {
        $("plan-edit-hint").textContent = learnEl.checked
          ? "自动学习·开"
          : "自动学习·关";
      }
    });
  }
  updatePlanHistoryButtons();
}

function setAsrToolsEnabled(on) {
  ["asr-reload", "asr-to-golden"].forEach((id) => {
    if ($(id)) $(id).disabled = !on;
  });
}

/** Normalize time window for overlap matching (ms). */
function windowKey(t0, t1) {
  const a = Math.max(0, Math.round(Number(t0) || 0));
  const b = Math.max(a + 1, Math.round(Number(t1) || 0));
  // 200ms bins — slightly looser than 120ms so slight edge edits still match
  return `${Math.round(a / 200)}_${Math.round(b / 200)}`;
}

/** Normalize text for uniqueness (ignore spaces / punctuation noise). */
function textKey(text) {
  return String(text || "")
    .replace(/\s+/g, "")
    .replace(/[，。！？、,.!?;；:：'\"“”‘’…·\-—]/g, "")
    .toLowerCase();
}

/** Soft text containment match (plan may scrub filler so plan text ⊆ asr text). */
function textSoftMatch(a, b) {
  const ta = textKey(a);
  const tb = textKey(b);
  if (!ta || !tb) return false;
  if (ta === tb) return true;
  if (ta.length >= 4 && tb.length >= 4) {
    if (ta.includes(tb) || tb.includes(ta)) return true;
    // shared head / tail 6 chars for near-equal long lines
    if (ta.length >= 8 && tb.length >= 8) {
      if (ta.slice(0, 8) === tb.slice(0, 8)) return true;
      if (ta.slice(-8) === tb.slice(-8)) return true;
    }
  }
  return false;
}

/** Overlap ratio of two ms windows in [0,1] vs shorter length. */
function windowOverlapRatio(a0, a1, b0, b1) {
  const t0a = Math.max(0, Number(a0) || 0);
  const t1a = Math.max(t0a + 1, Number(a1) || 0);
  const t0b = Math.max(0, Number(b0) || 0);
  const t1b = Math.max(t0b + 1, Number(b1) || 0);
  const inter = Math.max(0, Math.min(t1a, t1b) - Math.max(t0a, t0b));
  if (inter <= 0) return 0;
  const shorter = Math.min(t1a - t0a, t1b - t0b) || 1;
  return inter / shorter;
}

/**
 * Identity of a plan / ASR module for uniqueness.
 * Same asr index OR same time window OR same normalized text (len>=4) = same module.
 */
function slotIdentity(slotOrAsr) {
  if (!slotOrAsr) return { asr: null, win: null, text: null };
  const asr =
    slotOrAsr.from_asr_idx != null && slotOrAsr.from_asr_idx !== ""
      ? Number(slotOrAsr.from_asr_idx)
      : null;
  return {
    asr: Number.isFinite(asr) ? asr : null,
    win: windowKey(slotOrAsr.t0_ms, slotOrAsr.t1_ms),
    text: textKey(slotOrAsr.text),
  };
}

function sameModule(a, b) {
  if (!a || !b) return false;
  const ia = slotIdentity(a);
  const ib = slotIdentity(b);
  const ov = windowOverlapRatio(a.t0_ms, a.t1_ms, b.t0_ms, b.t1_ms);
  // Same ASR origin only counts as duplicate when times still heavily overlap.
  // Mid-cut splits re-used from_asr_idx and must NOT collapse L/R halves.
  if (ia.asr != null && ib.asr != null && ia.asr === ib.asr) {
    if (ov >= 0.55) return true;
    // non-overlapping (or barely overlapping) = two different keep segments
    if (ov < 0.25) return false;
  }
  if (ia.win && ib.win && ia.win === ib.win) return true;
  if (ia.text && ib.text && ia.text.length >= 4 && ia.text === ib.text) return true;
  // soft: high time overlap + soft text
  if (ov >= 0.55 && textSoftMatch(a.text, b.text)) return true;
  if (ov >= 0.78) return true; // strong time overlap alone
  if (textSoftMatch(a.text, b.text) && ov >= 0.35) return true;
  return false;
}

/** Active plan slots from editable copy or readonly plan snapshot. */
function getActivePlanSlots(plan) {
  const src = plan || planEdit || {};
  const out = [];
  for (const k of ["golden", "trust", "cta"]) {
    for (const s of src[k] || []) {
      if (!s || s.removed) continue;
      out.push(s);
    }
  }
  return out;
}

/** Build set of active plan windows currently in logical timeline. */
function getPlanWindowKeys(plan) {
  const keys = new Set();
  for (const s of getActivePlanSlots(plan)) {
    keys.add(windowKey(s.t0_ms, s.t1_ms));
    const tk = textKey(s.text);
    if (tk.length >= 4) keys.add(`t:${tk}`);
    if (s.from_asr_idx != null && s.from_asr_idx !== "") keys.add(`a:${Number(s.from_asr_idx)}`);
  }
  return keys;
}

/** True if left ASR card is already represented in right plan (unique module). */
function isAsrInPlan(u, planKeys, asrIdx = null) {
  if (!u) return false;
  const probe = {
    text: u.text,
    t0_ms: u.t0_ms,
    t1_ms: u.t1_ms,
    from_asr_idx: asrIdx != null ? asrIdx : u.from_asr_idx,
  };
  // Full identity scan on current planEdit / any plan snapshot passed via keys build
  if (findModuleIndex(probe) >= 0) return true;

  // Fallback when planEdit empty but keys set was built from readonly plan
  const keys = planKeys || getPlanWindowKeys();
  if (asrIdx != null && keys.has(`a:${Number(asrIdx)}`)) return true;
  if (keys.has(windowKey(u.t0_ms, u.t1_ms))) return true;
  const tk = textKey(u.text);
  if (tk.length >= 4 && keys.has(`t:${tk}`)) return true;
  // Soft text key: any plan key starts with t: and soft-matches
  if (tk.length >= 4) {
    for (const k of keys) {
      if (typeof k === "string" && k.startsWith("t:")) {
        const pt = k.slice(2);
        if (textSoftMatch(tk, pt) || textSoftMatch(u.text, pt)) return true;
      }
    }
  }
  return false;
}

/** Index of first matching module in golden (or any story track), or -1. */
function findModuleIndex(probe, role = "golden") {
  // Prefer editable plan; also scan trust/cta for safety
  const roles = role ? [role, "golden", "trust", "cta"] : ["golden", "trust", "cta"];
  const seen = new Set();
  for (const r of roles) {
    if (seen.has(r)) continue;
    seen.add(r);
    const arr = planEdit?.[r] || [];
    for (let i = 0; i < arr.length; i++) {
      const s = arr[i];
      if (!s || s.removed) continue;
      if (sameModule(s, probe)) return i;
    }
  }
  return -1;
}

/**
 * Keep modules unique in a track (first occurrence wins).
 * Drops later duplicates by asr idx / time window / text.
 */
function uniquePlanTrack(arr) {
  const out = [];
  for (const s of arr || []) {
    if (!s || s.removed) continue;
    if (out.some((x) => sameModule(x, s))) continue;
    out.push(s);
  }
  return out;
}

function enforcePlanUniqueness(role = "golden") {
  if (!planEdit?.[role]) return 0;
  const before = planEdit[role].length;
  planEdit[role] = uniquePlanTrack(planEdit[role]);
  return Math.max(0, before - planEdit[role].length);
}

/** Refresh left card in-plan colors + button labels without full re-render. */
function refreshAsrInPlanMarks() {
  const keys = getPlanWindowKeys();
  document.querySelectorAll(".asr-card").forEach((card) => {
    const idx = Number(card.dataset.idx);
    const u = asrCards[idx];
    const on = isAsrInPlan(u, keys, idx);
    card.classList.toggle("asr-in-plan", on);
    card.classList.toggle("asr-source", !on);
    card.classList.toggle("story", on);
    card.classList.toggle("trust", !on);
    card.dataset.inPlan = on ? "1" : "0";
    // badge / buttons must update even when we don't re-render the whole list
    const badge = card.querySelector(".clip-badge");
    if (badge) {
      // CSS `.asr-in-plan .clip-badge::after` appends " · 已加入" — only set base here
      badge.textContent = `口播 #${idx + 1}`;
    }
    const addBtn = card.querySelector(".asr-add-golden");
    if (addBtn) addBtn.textContent = on ? "已在成片" : "+加入成片";
    const plus = card.querySelector(".asr-add-one");
    if (plus) plus.title = on ? "已在逻辑成片中" : "加入逻辑成片";
  });
}

/**
 * Insert ASR into plan uniquely.
 * - if not present: insert at toIdx (or append)
 * - if already present and toIdx given: MOVE existing card to that position (still unique)
 * - if already present and no move: skip
 * returns {action:'add'|'move'|'skip'|'empty', index}
 */
function insertAsrUnique(aidx, toIdx = null) {
  const item = asrCards[aidx];
  if (!item) return { action: "skip", index: -1 };
  if (!planEdit) {
    planEdit = { golden: [], trust: [], cta: [] };
    planOriginal = clonePlan(planEdit);
    planSourceJobId = currentJobId;
  } else if (!planSourceJobId) {
    planSourceJobId = currentJobId;
  }
  const role = "golden";
  if (!planEdit[role]) planEdit[role] = [];

  const leftCard = document.querySelector(`.asr-card[data-idx="${aidx}"]`);
  let text = item.text;
  let t0 = Number(item.t0_ms || 0);
  let t1 = Number(item.t1_ms || 0);
  if (leftCard) {
    const ta = leftCard.querySelector(".clip-text-edit");
    const t0s = leftCard.querySelector(".clip-t0s");
    const t1s = leftCard.querySelector(".clip-t1s");
    if (ta) text = ta.value;
    if (t0s) t0 = Math.max(0, secInputToMs(t0s.value || 0));
    if (t1s) t1 = Math.max(t0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
    item.text = text;
    item.t0_ms = t0;
    item.t1_ms = t1;
  }
  text = String(text || "").trim();
  if (!text) return { action: "empty", index: -1 };
  t1 = Math.max(t0 + TS_MIN_DUR_MS, t1);

  const probe = { text, t0_ms: t0, t1_ms: t1, from_asr_idx: aidx };
  const existing = findModuleIndex(probe, role);

  // Already in plan: move to drop position if provided, else skip (keep unique)
  if (existing >= 0) {
    if (toIdx == null || Number.isNaN(Number(toIdx))) {
      return { action: "skip", index: existing };
    }
    pushPlanHistory("移动片段");
    let insertAt = Math.max(0, Math.min(Number(toIdx), planEdit[role].length));
    const [slot] = planEdit[role].splice(existing, 1);
    // fix index after removal
    if (existing < insertAt) insertAt -= 1;
    insertAt = Math.max(0, Math.min(insertAt, planEdit[role].length));
    // refresh identity fields
    slot.text = text;
    slot.t0_ms = t0;
    slot.t1_ms = t1;
    slot.from_asr_idx = aidx;
    slot.removed = false;
    planEdit[role].splice(insertAt, 0, slot);
    enforcePlanUniqueness(role);
    return { action: "move", index: insertAt };
  }

  pushPlanHistory("加入片段");
  const slot = {
    clip_id: `asr_${aidx}_${Date.now().toString(36)}_${Math.random().toString(16).slice(2, 6)}`,
    role: roleLabel(role),
    text,
    t0_ms: t0,
    t1_ms: t1,
    score: 20,
    removed: false,
    from_asr_idx: aidx,
  };
  let insertAt =
    toIdx == null || Number.isNaN(Number(toIdx))
      ? planEdit[role].length
      : Math.max(0, Math.min(Number(toIdx), planEdit[role].length));
  planEdit[role].splice(insertAt, 0, slot);
  enforcePlanUniqueness(role);
  // re-find after unique (should be insertAt or nearby)
  const idx = findModuleIndex(probe, role);
  return { action: "add", index: idx >= 0 ? idx : insertAt };
}

function addAsrToTrack(trackKey, indices) {
  // single logical track only
  trackKey = "golden";
  if (!TRACK_ORDER.includes(trackKey)) return;
  if (!planEdit) {
    planEdit = { golden: [], trust: [], cta: [] };
    planOriginal = clonePlan(planEdit);
    planSourceJobId = currentJobId;
  } else if (!planSourceJobId) {
    planSourceJobId = currentJobId;
  }
  setPlanToolsEnabled(true);
  const list = indices && indices.length ? indices : [...selectedAsr];
  if (!list.length) {
    alert("请先勾选左侧口播卡片，或点卡片上的 ＋ / 拖到中间逻辑成片");
    return;
  }
  let added = 0;
  let moved = 0;
  let skippedDup = 0;
  let skippedEmpty = 0;
  list
    .sort((a, b) => a - b)
    .forEach((aidx) => {
      // button/add: append if new; never create a second copy
      const r = insertAsrUnique(aidx, null);
      if (r.action === "add") added += 1;
      else if (r.action === "move") moved += 1;
      else if (r.action === "empty") skippedEmpty += 1;
      else skippedDup += 1;
    });
  selectedAsr.clear();
  // clear checks without full left re-render
  document.querySelectorAll(".asr-card .asr-check").forEach((ck) => {
    ck.checked = false;
  });
  if (added > 0 || moved > 0) {
    planDirty = true;
    queueRenderTracks();
  }
  // Always refresh colors (queueRenderTracks is rAF — refresh again after it)
  refreshAsrInPlanMarks();
  requestAnimationFrame(() => {
    try {
      refreshAsrInPlanMarks();
    } catch (_) {}
  });
  if ($("plan-edit-hint")) {
    if (added > 0) {
      $("plan-edit-hint").textContent = `已加入 ${added} 段（逻辑成片内唯一 · 需保存并重剪）`;
    } else if (moved > 0) {
      $("plan-edit-hint").textContent = `已调整位置 ${moved} 段（模块唯一，不重复添加）`;
    } else if (skippedDup > 0) {
      $("plan-edit-hint").textContent = "这些口播已在逻辑成片中（每句只保留一份）";
    } else if (skippedEmpty > 0) {
      $("plan-edit-hint").textContent = "加入失败：口播文案为空";
    } else {
      $("plan-edit-hint").textContent = "没有可加入的口播句";
    }
  }
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

  const planKeys = getPlanWindowKeys();
  box.innerHTML = asrCards
    .map((u, idx) => {
      const a = msToSecInput(u.t0_ms  || 0);
      const b = msToSecInput(u.t1_ms  || 0);
      const checked = selectedAsr.has(idx) ? "checked" : "";
      const inPlan = isAsrInPlan(u, planKeys, idx);
      const tone = inPlan ? "asr-in-plan story" : "asr-source";
      // badge base only — CSS ::after adds " · 已加入" when .asr-in-plan
      return `<div class="jy-clip asr-card ${tone}" draggable="true" data-source="asr" data-idx="${idx}" data-in-plan="${inPlan ? "1" : "0"}">
        <div class="clip-top">
          <input type="checkbox" class="asr-check" ${checked} title="多选后批量加入成片" />
          <span class="clip-drag" title="按住拖到中间逻辑成片">⠿</span>
          <span class="clip-badge">口播 #${idx + 1}</span>
          <button type="button" class="clip-x asr-add-one" title="${inPlan ? "已在逻辑成片中" : "加入逻辑成片"}">＋</button>
        </div>
        <textarea class="clip-text-edit" rows="${suggestTextareaRows(u.text)}" placeholder="编辑这段口播词…">${escapeHtml(u.text || "")}</textarea>
        <div class="clip-time-row">
          <label>开始(s)<input class="clip-t0s" type="number" step="${TS_UI_STEP}" min="0" value="${a}" /></label>
          <label>结束(s)<input class="clip-t1s" type="number" step="${TS_UI_STEP}" min="0" value="${b}" /></label>
        </div>
        <div class="clip-tools">
          <button type="button" class="asr-add-golden">${inPlan ? "已在成片" : "+加入成片"}</button>
        </div>
      </div>`;
    })
    .join("");

  ensureAsrEventsBound();
  scheduleFitClipTextareas(box);
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
      if (el.classList?.contains("clip-text-edit")) fitTextareaHeight(el, clipTextareaLimits(el));
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
    if (t.classList.contains("clip-text-edit")) {
      asrCards[idx].text = t.value;
      fitTextareaHeight(t, clipTextareaLimits(t));
    }
    if (t.classList.contains("clip-t0s")) asrCards[idx].t0_ms = Math.max(0, secInputToMs(t.value || 0));
    if (t.classList.contains("clip-t1s")) {
      const t0 = asrCards[idx].t0_ms || 0;
      asrCards[idx].t1_ms = Math.max(t0 + TS_MIN_DUR_MS, secInputToMs(t.value || 0));
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
      if (t0s) asrCards[idx].t0_ms = Math.max(0, secInputToMs(t0s.value || 0));
      if (t1s) {
        const t0 = asrCards[idx].t0_ms || 0;
        asrCards[idx].t1_ms = Math.max(t0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
      }
    }
    if (
      btn.classList.contains("asr-add-golden") ||
      btn.classList.contains("asr-add-one") ||
      btn.classList.contains("asr-add-trust") ||
      btn.classList.contains("asr-add-cta")
    ) {
      addAsrToTrack("golden", [idx]);
    }
  });

  box.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".asr-card");
    if (!card) return;
    if (e.target.closest("button, textarea, input, label, .clip-time-row, .clip-tools")) {
      e.preventDefault();
      return;
    }
    const idx = Number(card.dataset.idx);
    const ta = card.querySelector(".clip-text-edit");
    const t0s = card.querySelector(".clip-t0s");
    const t1s = card.querySelector(".clip-t1s");
    if (asrCards[idx]) {
      if (ta) asrCards[idx].text = ta.value;
      if (t0s) asrCards[idx].t0_ms = Math.max(0, secInputToMs(t0s.value || 0));
      if (t1s) {
        const t0 = asrCards[idx].t0_ms || 0;
        asrCards[idx].t1_ms = Math.max(t0 + TS_MIN_DUR_MS, secInputToMs(t1s.value || 0));
      }
    }
    card.classList.add("dragging");
    try {
      e.dataTransfer.effectAllowed = "copyMove";
      const payload = JSON.stringify({ source: "asr", idx });
      e.dataTransfer.setData("text/plain", payload);
      e.dataTransfer.setData("application/x-xiaomian-clip", payload);
    } catch (_) {}
  });
  box.addEventListener("dragend", (e) => {
    e.target.closest?.(".asr-card")?.classList.remove("dragging");
    document.querySelectorAll(".jy-track-body").forEach((t) => t.classList.remove("drag-over"));
  });
}

async function loadTranscript(jobId, opts = {}) {
  const force = !!opts.force;
  const wanted = String(jobId || "");
  if (!wanted) return;
  // Bust browser/proxy cache when switching jobs or after ASR completes
  const bust = force || asrTranscriptJobId !== wanted ? `?v=${Date.now()}` : "";
  const myToken = ++asrLoadToken;
  const box = $("transcript-list");
  // Only blank UI if this is the active job (avoid flash when stale call finishes later)
  if (wanted === String(currentJobId || "")) {
    $("asr-count").textContent = "—";
    asrCards = [];
    asrTranscriptJobId = null;
    selectedAsr.clear();
    setAsrToolsEnabled(false);
    if (box) box.innerHTML = '<div class="jy-empty">加载口播…</div>';
  }
  try {
    // prefer full raw asr for human selection space; fallback kept
    let items = [];
    const resRaw = await fetch(
      `/api/jobs/${encodeURIComponent(wanted)}/files/transcript_asr.json${bust}`
    );
    // Drop stale response if user switched job or a newer load started
    if (myToken !== asrLoadToken || wanted !== String(currentJobId || "")) return;
    if (resRaw.ok) {
      items = await resRaw.json();
    } else {
      const resKept = await fetch(
        `/api/jobs/${encodeURIComponent(wanted)}/files/transcript_for_clipper.json${bust}`
      );
      if (myToken !== asrLoadToken || wanted !== String(currentJobId || "")) return;
      if (!resKept.ok) {
        if (box && wanted === String(currentJobId || "")) {
          box.innerHTML = '<div class="jy-empty">口播尚未生成</div>';
        }
        return;
      }
      items = await resKept.json();
    }
    if (myToken !== asrLoadToken || wanted !== String(currentJobId || "")) return;
    asrCards = (items || [])
      .filter((u) => u && String(u.text || "").trim())
      .map((u, i) => ({
        utt_id: u.utt_id || `a${i}`,
        text: String(u.text || "").trim(),
        t0_ms: Number(u.t0_ms || 0),
        t1_ms: Number(u.t1_ms || 0),
      }));
    asrTranscriptJobId = wanted;
    // Re-link auto plan slots -> ASR idx now that left cards exist
    try {
      if (planEdit?.golden?.length) {
        for (const s of planEdit.golden) {
          if (!s || s.removed) continue;
          if (s.from_asr_idx != null && Number.isFinite(Number(s.from_asr_idx))) continue;
          let best = -1;
          let bestScore = 0;
          for (let i = 0; i < asrCards.length; i++) {
            const u = asrCards[i];
            const ov = windowOverlapRatio(s.t0_ms, s.t1_ms, u.t0_ms, u.t1_ms);
            const soft = textSoftMatch(s.text, u.text) ? 0.35 : 0;
            const sc = ov + soft;
            if (sc > bestScore && (ov >= 0.4 || soft > 0)) {
              bestScore = sc;
              best = i;
            }
          }
          if (best >= 0) s.from_asr_idx = best;
        }
      }
    } catch (_) {}
    renderAsrCards();
    // Critical: colors were often wrong because plan rendered before asrCards loaded
    try {
      refreshAsrInPlanMarks();
    } catch (_) {}
  } catch {
    if (myToken === asrLoadToken && wanted === String(currentJobId || "") && box) {
      box.innerHTML = '<div class="jy-empty">口播加载失败</div>';
    }
  }
}

function setupAsrTools() {
  $("asr-reload")?.addEventListener("click", () => {
    if (currentJobId) loadTranscript(currentJobId);
  });
  $("asr-to-golden")?.addEventListener("click", () => addAsrToTrack("golden"));
}

function resolvePlanPath(data) {
  /** Normalize job meta into a user-facing processing path. */
  const st = data.status || "";
  const processing = ["queued", "processing", "starting", "claimed"].includes(st);
  const stage = String(data.stage || "");
  const pathRaw = String(data.llm_path || "");
  const model = String(data.llm_model || "");
  let status = data.llm_status || "";
  let statusText = data.llm_status_text || "";

  // Infer for older jobs / incomplete meta
  if (!status) {
    if (processing && (stage.includes("llm") || /llm/i.test(String(data.stage_detail || "")))) {
      status = "running";
      statusText = "正在走 LLM 规划…";
    } else if (data.llm_fallback || data.llm_error) {
      status = "failed";
      statusText = "LLM 失败，已回退规则";
    } else if (
      pathRaw === "cloud_or_repaired" ||
      pathRaw.includes("stable_ids_only") ||
      pathRaw.includes("light_asr") ||
      model.startsWith("Qwen") ||
      /gpt|deepseek|glm|qwen/i.test(model)
    ) {
      status = "success";
      statusText = "云端 LLM";
    } else if (pathRaw.includes("local") || model.includes("local")) {
      status = "local_fallback";
      statusText = "本地卖点兜底";
    } else if (pathRaw.includes("rules") || model.includes("rules") || data.planner === "rules") {
      status = "rules_fallback";
      statusText = "规则兜底";
    } else if (data.planner === "llm") {
      status = "success";
      statusText = "LLM 路径";
    } else if (processing) {
      status = "running";
      statusText = "处理中…";
    } else {
      status = "idle";
      statusText = "";
    }
  }

  // Canonical labels for the "处理路径" module
  const pathLabel = {
    success: "云端 LLM",
    local_fallback: "本地卖点兜底",
    rules_fallback: "规则兜底",
    failed: "失败（已回退）",
    disabled: "规则排片",
    running: "处理中",
  }[status] || "";

  const detail =
    statusText ||
    {
      success: "本视频由云端大模型选句排片",
      local_fallback: "云端失败/不可用，使用本地卖点规则补片",
      rules_fallback: "云端失败或时长不足，使用规则排片兜底",
      failed: "LLM 失败，已回退规则",
      disabled: "未启用 LLM，规则排片",
      running: "ASR/规划进行中",
    }[status] ||
    "";

  return { status, pathLabel, detail, pathRaw, model };
}

function renderLlmStatus(data) {
  const card = $("llm-status-card");
  const badge = $("llm-status-badge");
  const text = $("llm-status-text");
  const metaEl = $("llm-status-meta");
  if (!badge || !text) return;

  const { status, pathLabel, detail, pathRaw, model } = resolvePlanPath(data || {});

  // Only show once there is a real path or active processing
  const meaningful = new Set([
    "success",
    "local_fallback",
    "rules_fallback",
    "failed",
    "disabled",
    "running",
  ]);
  const badgeClass = {
    success: "llm-badge-ok",
    local_fallback: "llm-badge-warn",
    rules_fallback: "llm-badge-warn",
    failed: "llm-badge-err",
    disabled: "llm-badge-idle",
    running: "llm-badge-idle",
  }[status] || "llm-badge-idle";

  if (!meaningful.has(status) || !pathLabel) {
    if (card) card.hidden = true;
    badge.className = "llm-badge llm-badge-idle";
    badge.textContent = "";
    text.textContent = "";
    if (metaEl) {
      metaEl.hidden = true;
      metaEl.textContent = "";
    }
    return;
  }
  if (card) card.hidden = false;

  badge.className = `llm-badge ${badgeClass}`;
  badge.textContent = pathLabel;
  // One clear line: which path this video used
  text.textContent = detail;

  if (metaEl) {
    const bits = [];
    if (status === "success") bits.push("路径: 云端 LLM");
    else if (status === "local_fallback") bits.push("路径: 本地兜底");
    else if (status === "rules_fallback" || status === "disabled") bits.push("路径: 规则兜底");
    else if (status === "failed") bits.push("路径: 失败回退");
    else if (status === "running") bits.push("路径: 处理中");
    if (pathRaw) bits.push(`内部: ${pathRaw}`);
    if (model) bits.push(`模型: ${model}`);
    if (data.llm_latency_ms != null) bits.push(`耗时: ${data.llm_latency_ms}ms`);
    if (data.llm_attempt) bits.push(`尝试: ${data.llm_attempt}`);
    if (data.selected_clips != null) bits.push(`段数: ${data.selected_clips}`);
    if (data.final_duration_s != null) bits.push(`成片: ${data.final_duration_s}s`);
    const cov = data.llm_coverage;
    if (cov && typeof cov === "object") {
      bits.push(
        `覆盖: 版型${cov.fit ? "✓" : "×"} 面料${cov.fabric ? "✓" : "×"} 人群${cov.audience ? "✓" : "×"}`
      );
    }
    const err = data.llm_cloud_error || data.llm_error;
    if (err) bits.push(`原因: ${String(err).slice(0, 160)}`);
    if (bits.length) {
      metaEl.hidden = false;
      metaEl.textContent = bits.join("  ·  ");
    } else {
      metaEl.hidden = true;
      metaEl.textContent = "";
    }
  }
}

function renderJob(data) {
  const jobChanged = currentJobId !== data.job_id;
  const prevStatus = window.__xm_lastJobStatus || null;
  currentJobId = data.job_id;
  if (jobChanged) {
    clearPlanEditorState();
  }
  $("current-job-title").textContent = data.video_source || data.job_id;
  const st = data.status || "";
  const waitInfo = formatQueueWaitInfo(data);
  $("current-job-status").textContent = `${STATUS_LABEL[st] || st}${
    waitInfo ? ` · ${waitInfo}` : ""
  }${data.final_duration_s ? ` · 成片${data.final_duration_s}s` : ""}`;
  renderLlmStatus(data);

  // progress
  const pb = $("progress-block");
  const processing = ["queued", "processing", "starting", "claimed"].includes(st);
  if (processing) {
    pb.hidden = false;
    const pct = Number(data.progress || (st === "queued" ? 2 : 15));
    $("progress-bar").style.width = `${pct}%`;
    $("progress-text").textContent = `${pct}%`;
    $("stage-text").textContent = buildStageLine(data);
  } else {
    pb.hidden = st !== "failed";
    if (st === "failed") {
      pb.hidden = false;
      $("progress-bar").style.width = "100%";
      $("progress-text").textContent = "失败";
      $("stage-text").textContent = data.error || "处理失败";
    }
  }

  // video: prefer draft preview for fast loop; export upgrades to final
  const video = $("preview");
  const files = data.files || {};
  const playFile = files.preview ? "preview.mp4" : files.final ? "final.mp4" : "";
  if (playFile) {
    const token = data.render_token || data.finished_at || data.final_size || Date.now();
    const url = `/api/jobs/${encodeURIComponent(data.job_id)}/files/${playFile}?v=${encodeURIComponent(token)}`;
    const shouldRefresh =
      jobChanged ||
      !video.src ||
      (!planDirty && !processing && !video.src.includes(String(token)));
    if (shouldRefresh) {
      const wasPaused = video.paused;
      const t = video.currentTime || 0;
      video.src = url;
      // try keep position only if same job mid-play; otherwise start head
      if (!jobChanged && !wasPaused) {
        video.addEventListener(
          "loadedmetadata",
          () => {
            try {
              video.currentTime = Math.min(t, Math.max(0, (video.duration || t) - 0.1));
            } catch (_) {}
          },
          { once: true }
        );
      }
    }
    // top-bar export button removed from UI; download stays under 当前任务 actions
  } else if (jobChanged) {
    video.removeAttribute("src");
  }

  // tracks / ASR: never clobber while user is editing (planDirty) on SAME job
  const hasTranscriptFile = !!(files.transcript_asr || files.transcript || files.plan);
  const serverPlanN = planSlotCount(data.plan);
  const localPlanN = planSlotCount(planEdit);
  const planJobMismatch = !!(planEdit && planSourceJobId && planSourceJobId !== data.job_id);
  // Server finished / plan arrived while left empty, or plan grew after processing
  const justFinished =
    !processing &&
    ["success", "success_partial"].includes(st) &&
    prevStatus &&
    ["queued", "processing", "starting", "claimed"].includes(prevStatus);
  const needForcePlan =
    jobChanged ||
    planJobMismatch ||
    (!planDirty &&
      (justFinished ||
        (serverPlanN > 0 && localPlanN === 0) ||
        (serverPlanN > 0 && serverPlanN !== localPlanN && !planDirty && planSourceJobId === data.job_id && !processing)));

  if (jobChanged) {
    clearPlanEditorState();
    // clear left ASR immediately so job2 never shows job1 text while loading
    asrCards = [];
    asrTranscriptJobId = null;
    selectedAsr.clear();
    setAsrToolsEnabled(false);
    if ($("transcript-list")) {
      $("transcript-list").innerHTML = '<div class="jy-empty">切换任务，加载口播…</div>';
    }
    if ($("asr-count")) $("asr-count").textContent = "—";
    renderTracks(data.plan || {}, { forceServer: true });
    loadTranscript(data.job_id, { force: true });
  } else if (planDirty && planSourceJobId === data.job_id) {
    // keep user edits for this job only
  } else {
    if (needForcePlan || !planEdit || planSourceJobId !== data.job_id) {
      renderTracks(data.plan || {}, { forceServer: true });
    }
    // Same job: when ASR finishes after empty start, force reload left transcript
    const asrMissingOrWrong = asrTranscriptJobId !== data.job_id || !asrCards.length;
    if (hasTranscriptFile && (asrMissingOrWrong || justFinished)) {
      loadTranscript(data.job_id, { force: true });
    }
  }
  window.__xm_lastJobStatus = st;
  // review text panel removed from UI; status shown in player bar / progress

  // actions — primary downloads on top; plan.json / review.md folded by default
  const actions = [];
  const devLinks = [];
  const srcName = String(data.video_source || data.job_id || "video");
  const srcStem = srcName.replace(/\.[^.\\/]+$/, "").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || "video";
  const dlFinal = `${srcStem}final.mp4`;
  const dlPreview = `${srcStem}preview.mp4`;
  if (files.preview) {
    actions.push(
      `<a class="jy-btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/preview.mp4" download="${escapeHtml(dlPreview)}">下载预览</a>`
    );
  }
  if (files.final) {
    actions.push(
      `<a class="jy-btn primary" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4" download="${escapeHtml(dlFinal)}">下载成片</a>`
    );
  }
  if (files.plan || files.final || files.preview) {
    actions.push(
      `<button type="button" class="jy-btn" id="export-final-btn">导出终稿</button>`
    );
  }
  if (files.plan) {
    devLinks.push(
      `<a class="jy-btn jy-btn-dev" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/plan.json" target="_blank">plan.json</a>`
    );
  }
  if (files.review) {
    devLinks.push(
      `<a class="jy-btn jy-btn-dev" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/review.md" target="_blank">review.md</a>`
    );
  }
  if (st === "failed") {
    actions.push(`<button type="button" class="jy-btn" id="retry-btn">重试</button>`);
  }
  let html = actions.join("") || '<span class="muted">暂无导出</span>';
  if (devLinks.length) {
    // default collapsed — expand only when user needs debug files
    let devOpen = false;
    try {
      devOpen = localStorage.getItem("clipper_dev_files_open") === "1";
    } catch (_) {}
    html += `
      <div class="jy-dev-files ${devOpen ? "open" : "collapsed"}" id="dev-files-box">
        <button type="button" class="jy-dev-files-toggle" id="dev-files-toggle" aria-expanded="${devOpen ? "true" : "false"}">
          <span class="jy-dev-caret">${devOpen ? "▾" : "▸"}</span>
          调试文件
          <span class="muted jy-dev-count">${devLinks.length}</span>
        </button>
        <div class="jy-dev-files-body" ${devOpen ? "" : "hidden"}>
          ${devLinks.join("")}
        </div>
      </div>`;
  }
  $("actions").innerHTML = html;
  const exportFinalBtn = $("export-final-btn");
  if (exportFinalBtn) {
    exportFinalBtn.onclick = async () => {
      exportFinalBtn.disabled = true;
      exportFinalBtn.textContent = "导出中…";
      try {
        await fetch(`/api/jobs/${encodeURIComponent(data.job_id)}/export-final`, {
          method: "POST",
        });
      } finally {
        // status poll will refresh buttons
      }
    };
  }
  const retry = $("retry-btn");
  if (retry) {
    retry.onclick = async () => {
      await fetch(`/api/jobs/${encodeURIComponent(data.job_id)}/retry`, { method: "POST" });
      pollJob(data.job_id);
    };
  }
  const devToggle = $("dev-files-toggle");
  if (devToggle) {
    devToggle.onclick = (e) => {
      e.preventDefault();
      const box = $("dev-files-box");
      const body = box?.querySelector?.(".jy-dev-files-body");
      if (!box || !body) return;
      const open = box.classList.toggle("open");
      box.classList.toggle("collapsed", !open);
      body.hidden = !open;
      devToggle.setAttribute("aria-expanded", open ? "true" : "false");
      const caret = devToggle.querySelector(".jy-dev-caret");
      if (caret) caret.textContent = open ? "▾" : "▸";
      try {
        localStorage.setItem("clipper_dev_files_open", open ? "1" : "0");
      } catch (_) {}
    };
  }

  // leftover path: if tracks block didn't load ASR (e.g. planDirty edge), still sync
  if (!planDirty && (jobChanged || asrTranscriptJobId !== data.job_id || !asrCards.length)) {
    if (files.transcript_asr || files.transcript || files.plan || jobChanged) {
      loadTranscript(data.job_id, { force: jobChanged || asrTranscriptJobId !== data.job_id });
    }
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
    clearTimeout(pollTimer);
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  // 1s while queued so "已等Xs" ticks live; slow down when processing
  let delay = 1000;
  const tick = async () => {
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const st = data.status || "";
      const stage = String(data.stage || "");
      const inQueueUi =
        st === "queued" ||
        stage === "queued" ||
        stage === "wait_asr" ||
        stage === "wait_llm" ||
        stage === "warm_extract";
      delay = inQueueUi ? 1000 : 2500;

      if (planDirty && data.job_id === currentJobId) {
        const waitInfo = formatQueueWaitInfo(data);
        $("current-job-status").textContent = `${STATUS_LABEL[st] || st}${
          waitInfo ? ` · ${waitInfo}` : ""
        }${data.final_duration_s ? ` · 成片${data.final_duration_s}s` : ""}`;
        const processing = ["queued", "processing", "starting", "claimed"].includes(st);
        const pb = $("progress-block");
        if (processing) {
          pb.hidden = false;
          const pct = Number(data.progress || 15);
          $("progress-bar").style.width = `${pct}%`;
          $("progress-text").textContent = `${pct}%`;
          $("stage-text").textContent = buildStageLine(data);
        }
        if (!processing) {
          loadJobs();
          if (pollTimer) clearInterval(pollTimer);
          pollTimer = null;
        } else {
          if (pollTimer) clearInterval(pollTimer);
          pollTimer = setTimeout(tick, delay);
        }
        return;
      }
      renderJob(data);
      loadJobs();
      if (!["queued", "processing", "starting", "claimed"].includes(data.status)) {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
        return;
      }
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setTimeout(tick, delay);
    } catch (_) {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setTimeout(tick, 2000);
    }
  };
  pollTimer = setTimeout(tick, 300);
}

function renderJobList(jobs) {
  const box = $("job-list");
  const empty = $("job-list-empty");
  if (!box) return;
  const list = Array.isArray(jobs) ? jobs : [];
  if (!list.length) {
    box.innerHTML = `<div class="jy-empty" id="job-list-empty">暂无任务。左侧选择视频后点「开始服装切片」。</div>`;
    return;
  }
  box.innerHTML = list
    .slice(0, 12)
    .map((j) => {
      const id = j.job_id || "unknown";
      const st = j.status || "unknown";
      const title = j.video_source || id;
      const wait = formatQueueWaitInfo(j);
      const active = id === currentJobId ? "active" : "";
      const badge = STATUS_LABEL[st] || st;
      const sub = [badge, wait || j.stage_detail || j.stage || "", j.has_final ? "有成片" : ""]
        .filter(Boolean)
        .join(" · ");
      return `<div class="jy-job-item ${active}" data-job-id="${escapeHtml(id)}" title="点击查看该任务">
        <div class="t">${escapeHtml(title)}</div>
        <div class="s">${escapeHtml(sub)}</div>
      </div>`;
    })
    .join("");
  // click bind once via delegation
  if (!box.dataset.bound) {
    box.dataset.bound = "1";
    box.addEventListener("click", (e) => {
      const item = e.target.closest?.(".jy-job-item");
      if (!item) return;
      const id = item.getAttribute("data-job-id");
      if (id) showJob(id);
    });
  }
}

async function loadJobs() {
  try {
    const res = await fetch("/api/jobs?limit=12");
    const data = await res.json();
    const jobs = data.jobs || [];
    renderJobList(jobs);
    // job-run-hint UI removed (user request): queue detail stays in job status line
    if (currentJobId) {
      const cur = jobs.find((j) => j.job_id === currentJobId);
      if (cur && ["queued", "processing", "starting", "claimed"].includes(cur.status)) {
        // poll handles detailed progress
      } else if (cur) {
        try {
          const r = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}`);
          if (r.ok) renderJob(await r.json());
        } catch (_) {}
      }
    }
  } catch (e) {
    if (hint) hint.textContent = "状态刷新失败：" + e;
  }
}

function setupForm() {
  // Restored from working portable zip pattern, plus safer file retention + clearer errors.
  const form = $("job-form");
  const fileInput = $("video");
  const drop = $("drop-zone");
  const err = $("form-error");
  const btn = $("submit-btn");
  const pickBtn = $("pick-video-btn");
  let selectedFile = null; // drag-drop fallback when input.files assignment fails

  if (!form || !fileInput || !drop || !btn) {
    console.error("[setupForm] missing elements", { form: !!form, fileInput: !!fileInput, drop: !!drop, btn: !!btn });
    return;
  }

  const setFileName = (f) => {
    selectedFile = f || null;
    const nameEl = $("file-name");
    if (!nameEl) return;
    if (f) {
      const mb = f.size ? ` · ${(f.size / 1024 / 1024).toFixed(1)}MB` : "";
      nameEl.textContent = `已选择：${f.name}${mb}`;
      nameEl.style.color = "#0f766e";
      nameEl.style.fontWeight = "600";
    } else {
      nameEl.textContent = "支持 mp4 / mov / mkv / webm / ts";
      nameEl.style.color = "";
      nameEl.style.fontWeight = "";
    }
  };

  const showErr = (msg) => {
    err.hidden = false;
    err.style.color = "#b91c1c";
    err.textContent = String(msg || "未知错误");
  };
  const showOk = (msg) => {
    err.hidden = false;
    err.style.color = "#0f766e";
    err.textContent = String(msg || "");
  };

  pickBtn?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    fileInput.click();
  });

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
    if (!f) return;
    try {
      fileInput.files = e.dataTransfer.files;
    } catch (_) {}
    setFileName(f);
    showOk(`已选中：${f.name}（请点「开始服装切片」）`);
  });
  fileInput.addEventListener("change", () => {
    const f = fileInput.files?.[0] || null;
    setFileName(f);
    if (f) showOk(`已选中：${f.name}（请点「开始服装切片」）`);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    const file = selectedFile || fileInput.files?.[0] || null;
    if (!file) {
      showErr("请选择视频（点上方区域或「选择视频文件」）");
      return;
    }
    btn.disabled = true;
    btn.textContent = "上传并启动…";
    try {
      const fd = new FormData();
      fd.append("video", file, file.name || "video.mp4");
      // defaults: 60s target + always render (UI controls removed)
      fd.append("target_seconds", $("target_seconds")?.value || "60");
      const renderEl = $("render");
      const renderOn =
        !renderEl
          ? true
          : renderEl.type === "checkbox"
            ? !!renderEl.checked
            : String(renderEl.value || "true").toLowerCase() !== "false";
      fd.append("render", renderOn ? "true" : "false");
      fd.append("auto_process", "true");
      // attach page LLM fields if present (compatible with multi-user zip)
      try {
        if ($("llm_base_url")?.value) fd.append("llm_base_url", $("llm_base_url").value.trim());
        if ($("llm_model")?.value) fd.append("llm_model", $("llm_model").value.trim());
        if ($("llm_api_key")?.value) fd.append("llm_api_key", $("llm_api_key").value.trim());
        if ($("llm_plan")) fd.append("llm_plan", $("llm_plan").checked ? "true" : "false");
      } catch (_) {}

      const res = await fetch("/api/jobs", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail || data.error || res.statusText || "创建失败";
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      if (!data.job_id) throw new Error("上传成功但未返回任务ID，请重启服务后再试");

      // force UI to current job immediately
      currentJobId = data.job_id;
      renderJob(data);
      await loadJobs();
      pollJob(data.job_id);
      showOk(`已入队：${data.job_id}${data.stage_detail ? " · " + data.stage_detail : ""}`);
      try {
        $("current-job-title") && ($("current-job-title").scrollIntoView({ behavior: "smooth", block: "nearest" }));
      } catch (_) {}
    } catch (ex) {
      const msg = String(ex?.message || ex || "上传失败");
      if (/Failed to fetch|NetworkError|fetch/i.test(msg)) {
        showErr("无法连接本地服务 127.0.0.1:8787。请先启动小面，再 Ctrl+F5 刷新。");
      } else if (/WinError 32|PermissionError|另一个程序正在使用/i.test(msg)) {
        showErr("任务文件写入冲突。请「停止小面」后「启动小面」，再重试。");
      } else {
        showErr(msg);
      }
      console.error("[upload]", ex);
    } finally {
      btn.disabled = false;
      btn.textContent = "开始服装切片";
    }
  });
}

let transcriptCache = [];

// 口播稿抽屉已从 UI 移除；左侧「口播时间轴」即主编辑入口。
function openTranscriptDrawer() {
  /* no-op: drawer stripped */
}
function closeTranscriptDrawer() {
  /* no-op */
}
function setupTranscriptModule() {
  /* no-op: top-bar 口播稿 removed */
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
    btn.title = collapsed ? "展开口播时间轴" : "收起口播时间轴";
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
  // Convenience: clicking the panel title also expands when collapsed
  panel.querySelector(".jy-panel-head")?.addEventListener("click", (e) => {
    if (!panel.classList.contains("collapsed")) return;
    if (e.target.closest("button")) return;
    e.preventDefault();
    apply(false);
  });
}

/** LLM 配置面板：默认可折叠，状态栏仍显示就绪摘要。 */
function setupLlmPanelToggle() {
  const panel = $("panel-llm");
  const btn = $("toggle-llm-panel");
  if (!panel || !btn) return;
  const apply = (collapsed) => {
    panel.classList.toggle("collapsed", collapsed);
    btn.textContent = collapsed ? "展开" : "收起";
    btn.title = collapsed ? "展开 LLM 配置" : "收起 LLM 配置";
    try {
      localStorage.setItem("clipper_llm_panel_collapsed", collapsed ? "1" : "0");
    } catch (_) {}
  };
  // default collapsed on first visit to keep right rail clean
  let collapsed = true;
  try {
    const v = localStorage.getItem("clipper_llm_panel_collapsed");
    if (v === "0") collapsed = false;
    else if (v === "1") collapsed = true;
    else localStorage.setItem("clipper_llm_panel_collapsed", "1");
  } catch (_) {}
  apply(collapsed);
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    apply(!panel.classList.contains("collapsed"));
  });
  // click header (not inputs/buttons) to toggle
  panel.querySelector(".jy-panel-head")?.addEventListener("click", (e) => {
    if (e.target.closest("button, input, a, label")) return;
    e.preventDefault();
    apply(!panel.classList.contains("collapsed"));
  });
}

async function loadLlmConfig(opts = {}) {
  const keepMsg = !!opts.keepMsg;
  const st = $("llm-cfg-status");
  const msg = $("llm-cfg-msg");
  try {
    const res = await fetch("/api/system/config");
    const cfg = await res.json();
    if ($("llm_plan")) $("llm_plan").checked = cfg.llm_plan_enabled !== false;
    // always sync base/model from saved config unless user is mid-edit with values
    if ($("llm_base_url") && (!keepMsg || !$("llm_base_url").value)) {
      $("llm_base_url").value = cfg.llm_base_url || $("llm_base_url").value || "";
    }
    if ($("llm_model") && (!keepMsg || !$("llm_model").value)) {
      $("llm_model").value = cfg.llm_model || $("llm_model").value || "";
    }
    // if fields empty, fill saved values
    if ($("llm_base_url") && !$("llm_base_url").value) $("llm_base_url").value = cfg.llm_base_url || "";
    if ($("llm_model") && !$("llm_model").value) $("llm_model").value = cfg.llm_model || "";
    if (st) {
      if (cfg.llm_plan_ready) st.textContent = `已就绪 · ${cfg.llm_model || ""}`;
      else if (cfg.has_llm_key) st.textContent = "有Key·检查模型/开关";
      else st.textContent = "请填写 Base/Model/Key";
    }
    if (msg && !keepMsg) {
      msg.textContent = cfg.llm_plan_ready
        ? `用户配置已启用：${cfg.llm_model || "-"} @ ${cfg.llm_base_url || "-"}（Key ***${cfg.api_key_hint || ""}）。不读环境变量。`
        : "请填写 Base URL + API Key，点「自动匹配」或手动填 Model，再「测试连通」。";
    }
    return cfg;
  } catch (e) {
    if (st) st.textContent = "配置读取失败";
    return null;
  }
}

function fillModelDatalist(models) {
  const dl = $("llm-model-list");
  if (!dl) return;
  const arr = Array.isArray(models) ? models : [];
  dl.innerHTML = arr
    .slice(0, 200)
    .map((m) => `<option value="${escapeHtml(String(m))}"></option>`)
    .join("");
}

function formatLlmProbeError(probe, data) {
  const raw = String((probe && (probe.error || probe.detail)) || data.detail || "unknown");
  if (
    /auth_invalid|Token is invalid|30014|API Key 无效|Token 无效/i.test(raw)
  ) {
    return "Token 无效：请到 SiliconFlow 控制台重新复制 API Key 后保存并重试";
  }
  if (/missing_api_key|missing_llm/i.test(raw)) {
    return "请先填写并保存 API Key";
  }
  // keep short
  return raw.length > 220 ? raw.slice(0, 220) + "…" : raw;
}

function setupLlmConfig() {
  const form = $("llm-config-form");
  if (!form) return;
  loadLlmConfig();

  const collectBody = () => {
    const body = {
      persist: true,
      llm_plan: !!$("llm_plan")?.checked,
      llm_enabled: true,
      llm_base_url: $("llm_base_url")?.value?.trim() || "",
      llm_model: $("llm_model")?.value?.trim() || "",
      organization: $("llm_org")?.value?.trim() || "",
    };
    const key = $("llm_api_key")?.value?.trim();
    if (key) body.llm_api_key = key;
    return body;
  };

  $("llm-fetch-models")?.addEventListener("click", async () => {
    const msg = $("llm-cfg-msg");
    if (msg) msg.textContent = "正在从 Base URL 拉取模型列表…";
    try {
      const base_url = $("llm_base_url")?.value?.trim() || "";
      const api_key = $("llm_api_key")?.value?.trim() || "";
      const preferred = $("llm_model")?.value?.trim() || "";
      if (!base_url) throw new Error("请先填写 Base URL");
      if (api_key && /^https?:\/\//i.test(api_key)) {
        throw new Error("API Key 填成网址了。网址请填 Base URL，Key 填 sk-... 字符串");
      }
      // key can be previously saved
      const body = { base_url, preferred };
      if (api_key) body.api_key = api_key;
      const t0 = performance.now();
      const res = await fetch("/api/system/llm/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      const clientMs = Math.round(performance.now() - t0);
      if (!res.ok) throw new Error(data.detail || "拉取模型失败");
      const models = data.models || [];
      fillModelDatalist(models);
      if (data.picked && $("llm_model")) $("llm_model").value = data.picked;
      const ms = data.latency_ms != null ? data.latency_ms : clientMs;
      if (msg) {
        msg.textContent = models.length
          ? `已匹配 ${models.length} 个模型，当前：${data.picked || "-"} · 延迟 ${ms} ms`
          : `未拉到模型列表（中转可能禁用 /models）。可手动填 Model。· 延迟 ${ms} ms`;
      }
      loadHealth();
      await loadLlmConfig({ keepMsg: true });
    } catch (e) {
      if (msg) msg.textContent = "自动匹配失败：" + (e.message || e);
    }
  });

  $("llm-probe")?.addEventListener("click", async () => {
    const msg = $("llm-cfg-msg");
    const st = $("llm-cfg-status");
    if (msg) msg.textContent = "测试连通与延迟中…";
    try {
      const body = collectBody();
      if (!body.llm_base_url) throw new Error("请先填写 Base URL");
      if (body.llm_api_key && /^https?:\/\//i.test(body.llm_api_key)) {
        throw new Error("API Key 填成网址了。网址请填 Base URL，Key 填 sk-... 字符串");
      }
      // save only when fields changed / key provided; avoid extra RTT every probe
      const needSave =
        !!body.llm_api_key ||
        !!body.llm_base_url ||
        !!body.llm_model ||
        body.llm_plan != null;
      if (needSave) {
        const saveRes = await fetch("/api/system/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const saveData = await saveRes.json().catch(() => ({}));
        if (!saveRes.ok) throw new Error(saveData.detail || "保存配置失败");
      }

      const t0 = performance.now();
      const res = await fetch("/api/system/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: "llm" }),
      });
      const data = await res.json().catch(() => ({}));
      const clientMs = Math.round(performance.now() - t0);
      // /api/system/probe returns {ok, probe, status}
      const probe = (data && data.probe) ? data.probe : data;
      const ok = !!(probe && probe.ok === true);
      const models = (probe && probe.models) || [];
      if (models.length) fillModelDatalist(models);
      if (probe && probe.model && $("llm_model")) {
        if (probe.auto_picked || !$("llm_model").value) $("llm_model").value = probe.model;
      }
      const lat = (probe && probe.latency) || {};
      // Prefer server chat latency (true API delay). Fall back to total/client.
      const chatMs = lat.chat_ms != null ? lat.chat_ms : probe && probe.latency_ms != null ? probe.latency_ms : null;
      const total =
        chatMs != null
          ? chatMs
          : lat.total_ms != null
            ? lat.total_ms
            : clientMs;
      const modelName = (probe && probe.model) || $("llm_model")?.value || "-";
      const endpoint = (probe && probe.endpoint) || "";
      if (msg) {
        msg.textContent = ok
          ? `连通成功 · ${modelName} · API延迟 ${total}ms${endpoint ? ` · ${endpoint}` : ""}`
          : `连通失败 · ${total}ms：${formatLlmProbeError(probe, data)}`;
      }
      if (st) st.textContent = ok ? `连通OK · ${total}ms` : "连通失败";
      if (!ok && String((probe && probe.error) || "").includes("API Key 填成网址")) {
        if ($("llm_api_key")) $("llm_api_key").value = "";
      }
      // lightweight health refresh; do not block/overwrite message
      loadHealth();
      await loadLlmConfig({ keepMsg: true });
    } catch (e) {
      if (msg) msg.textContent = "测试失败：" + (e.message || e);
      if (st) st.textContent = "测试失败";
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("llm-cfg-msg");
    if (msg) msg.textContent = "保存中…";
    try {
      const body = collectBody();
      if (!body.llm_base_url) throw new Error("请填写 Base URL");
      const cur = await (await fetch("/api/system/config")).json();
      if (!body.llm_api_key && !cur.has_llm_key) throw new Error("请填写 API Key");
      // if model empty, try auto match first
      if (!body.llm_model) {
        const mres = await fetch("/api/system/llm/models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            base_url: body.llm_base_url,
            api_key: body.llm_api_key,
          }),
        });
        const md = await mres.json().catch(() => ({}));
        if (md.picked) {
          body.llm_model = md.picked;
          if ($("llm_model")) $("llm_model").value = md.picked;
          fillModelDatalist(md.models || []);
        }
      }
      if (!body.llm_model) throw new Error("请填写或自动匹配 Model");
      const res = await fetch("/api/system/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "保存失败");
      if ($("llm_api_key")) $("llm_api_key").value = "";
      if (msg) msg.textContent = `已保存：${body.llm_model} @ ${body.llm_base_url}（用户配置，非 env）`;
      loadHealth();
      loadLlmConfig();
    } catch (ex) {
      if (msg) msg.textContent = "保存失败：" + (ex.message || ex);
    }
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
setupLlmPanelToggle();
setupLlmConfig();
// reflow-safe fit when fonts/layout settle or panel width changes
window.addEventListener("resize", () => scheduleFitClipTextareas(document));
if (document.fonts?.ready) {
  document.fonts.ready.then(() => scheduleFitClipTextareas(document)).catch(() => {});
}
setTimeout(() => scheduleFitClipTextareas(document), 300);
