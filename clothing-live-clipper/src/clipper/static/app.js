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
    asr: "GPU 口播打轴（medium+降噪，通常1–3分钟）",
    asr_done: "听写完成",
    filter: "过滤无效词",
    llm_plan: "LLM 全量小句提取主要内容并重排反剪",
    clipper: "规则逻辑排序",
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
    const llmReady = !!data.llm_plan_ready;
    el.textContent = ok
      ? `本机就绪 · ffmpeg${data.ffmpeg ? "✓" : "·"} · ${llmReady ? "用户LLM✓" : "待填LLM/规则"}`
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
      const top = (L.top_hook || []).slice(0, 5).map((x) => x[0]).join(" / ");
      if ($("learn-stat")) $("learn-stat").textContent = n > 0 ? `已学 ${n} 次` : "人机闭环";
      if ($("learn-hint")) {
        $("learn-hint").textContent =
          n > 0
            ? `学习已生效：${n} 次（保留 ${L.kept_slots || 0} / 丢弃 ${L.dropped_slots || 0}）。偏好：${top || "—"}。注意：必须勾选「学习这次重剪」才会写入；新视频需重新上传才会用到。`
            : "学习为空。精修后务必勾选「学习这次重剪」再保存，否则只改当前片、不记全局。";
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
  // flatten legacy 3-section plans into one logical sequence
  const flat = [
    ...(plan.golden || []).map((s) => copySlot(s, "story")),
    ...(plan.trust || []).map((s) => copySlot(s, "story")),
    ...(plan.cta || []).map((s) => copySlot(s, "story")),
  ];
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
  el.textContent = `${n} 段 · ${(avg / 1000).toFixed(1)}s均`;
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

function _splitClipByCut(role, idx, cut0ms, cut1ms, textLeft, textRight) {
  if (!planEdit?.[role]?.[idx]) return false;
  const s = planEdit[role][idx];
  const t0 = Math.max(0, Number(s.t0_ms || 0));
  const t1 = Math.max(t0 + 300, Number(s.t1_ms || 0));
  let c0 = Math.max(t0, Math.min(t1, Number(cut0ms)));
  let c1 = Math.max(t0, Math.min(t1, Number(cut1ms)));
  if (c1 < c0) [c0, c1] = [c1, c0];
  // ignore tiny cuts
  if (c1 - c0 < 200) return false;
  const leftOk = c0 - t0 >= 250;
  const rightOk = t1 - c1 >= 250;
  if (!leftOk && !rightOk) return false;

  const base = { ...s, role: roleLabel(role), removed: false };
  const parts = [];
  if (leftOk) {
    parts.push({
      ...base,
      clip_id: `${s.clip_id || "c"}_L_${Date.now().toString(36)}`,
      text: String(textLeft ?? s.text ?? "").trim(),
      t0_ms: t0,
      t1_ms: c0,
    });
  }
  if (rightOk) {
    parts.push({
      ...base,
      clip_id: `${s.clip_id || "c"}_R_${Date.now().toString(36)}`,
      text: String(textRight ?? s.text ?? "").trim(),
      t0_ms: c1,
      t1_ms: t1,
    });
  }
  if (!parts.length) return false;
  planEdit[role].splice(idx, 1, ...parts);
  planDirty = true;
  return true;
}

/** Delete a sub-range inside one clip by text selection ratio (approx without word timestamps). */
function cutSelectedTextInClip(role, idx, ta) {
  if (!planEdit?.[role]?.[idx] || !ta) return false;
  const text = String(ta.value || "");
  const a = Number(ta.selectionStart ?? 0);
  const b = Number(ta.selectionEnd ?? 0);
  if (!(b > a) || !text.length) {
    alert("请先在口播框里用鼠标选中要删掉的那几个字（例如“199再来一次”）");
    return false;
  }
  const s = planEdit[role][idx];
  const t0 = Math.max(0, Number(s.t0_ms || 0));
  const t1 = Math.max(t0 + 300, Number(s.t1_ms || 0));
  const dur = t1 - t0;
  const r0 = a / text.length;
  const r1 = b / text.length;
  // pad a little so cut catches spoken phrase
  const pad = Math.min(400, Math.round(dur * 0.04));
  const cut0 = Math.max(t0, Math.round(t0 + dur * r0) - pad);
  const cut1 = Math.min(t1, Math.round(t0 + dur * r1) + pad);
  const leftText = (text.slice(0, a) + text.slice(b)).replace(/\s{2,}/g, " ").trim();
  // keep left full remaining text on first part if right empty, etc.
  const textLeft = text.slice(0, a).trim();
  const textRight = text.slice(b).trim();
  const ok = _splitClipByCut(role, idx, cut0, cut1, textLeft || leftText, textRight || leftText);
  if (!ok) {
    alert("选中范围太短或会裁空整段，请扩大选中或改用「裁掉秒数」");
    return false;
  }
  if ($("plan-edit-hint")) {
    $("plan-edit-hint").textContent = `已裁掉约 ${((cut1 - cut0) / 1000).toFixed(1)}s（按选中文字估算），请点重剪生效`;
  }
  queueRenderTracks();
  return true;
}

/** Explicit second-range cut inside one clip: cutFromS/cutToS absolute seconds on source. */
function cutSecondsInClip(role, idx, fromS, toS) {
  if (!planEdit?.[role]?.[idx]) return false;
  const s = planEdit[role][idx];
  const t0 = Math.max(0, Number(s.t0_ms || 0));
  const t1 = Math.max(t0 + 300, Number(s.t1_ms || 0));
  let c0 = Math.round(Number(fromS) * 1000);
  let c1 = Math.round(Number(toS) * 1000);
  if (!(c1 > c0)) {
    alert("结束秒必须大于开始秒");
    return false;
  }
  if (c0 < t0 - 50 || c1 > t1 + 50) {
    alert(`裁剪范围需在当前片段内：${(t0 / 1000).toFixed(1)}s ~ ${(t1 / 1000).toFixed(1)}s`);
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
    $("plan-edit-hint").textContent = `已裁掉 ${(c0 / 1000).toFixed(1)}s–${(c1 / 1000).toFixed(1)}s，请点重剪生效`;
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
            <span class="clip-badge">逻辑 #${idx + 1}</span>
            <button type="button" class="clip-x" title="${removed ? "恢复" : "删除"}">${removed ? "+" : "×"}</button>
          </div>
          <textarea class="clip-text-edit" rows="4" placeholder="编辑这段口播词…">${escapeHtml(s.text || "")}</textarea>
          <div class="clip-time-row">
            <label>开始(s)<input class="clip-t0s" type="number" step="0.1" min="0" value="${a}" /></label>
            <label>结束(s)<input class="clip-t1s" type="number" step="0.1" min="0" value="${b}" /></label>
          </div>
          <div class="clip-time-row cut-row">
            <label>裁掉从(s)<input class="clip-cut0s" type="number" step="0.1" min="0" value="" placeholder="${a}" /></label>
            <label>到(s)<input class="clip-cut1s" type="number" step="0.1" min="0" value="" placeholder="${b}" /></label>
            <button type="button" class="clip-cut-range" title="按秒数裁掉中间一段">裁掉这段</button>
            <button type="button" class="clip-cut-sel" title="删除口播框中选中文字对应时间">删选中文字段</button>
          </div>
          <div class="meta">时长 ${((Number(s.t1_ms || 0) - Number(s.t0_ms || 0)) / 1000).toFixed(1)}s</div>
        </div>`;
      })
      .join("");
  };

  // single logical sequence in golden
  if ($("golden-track")) $("golden-track").innerHTML = mk(src.golden, "story", "golden");
  if ($("trust-track")) $("trust-track").innerHTML = "";
  if ($("cta-track")) $("cta-track").innerHTML = "";
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

    if (btn.classList.contains("clip-x") || btn.classList.contains("clip-del-hard")) {
      if (!planEdit?.[role]?.[idx]) return;
      // hard delete whole clip
      const removedItem = planEdit[role].splice(idx, 1)[0];
      planDirty = true;
      if ($("plan-edit-hint")) {
        const t = (removedItem?.text || "").slice(0, 18);
        $("plan-edit-hint").textContent = `已删除整段：${t}${t.length >= 18 ? "…" : ""}（需点重剪才生效）`;
      }
      queueRenderTracks();
      return;
    }
    if (btn.classList.contains("clip-cut-sel")) {
      const ta = card.querySelector(".clip-text-edit");
      cutSelectedTextInClip(role, idx, ta);
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

function isLearnEnabled() {
  const el = $("plan-learn");
  // Hidden placeholder is not a checkbox after UI simplify — never force learn
  return !!(el && el.type === "checkbox" && el.checked);
}

async function applyPlanEdit() {
  if (!currentJobId || !planEdit) return;
  syncPlanFieldsFromDom();
  // also drop empty-text or zero-length slots
  const clean = (arr) =>
    (arr || [])
      .filter((s) => s && !s.removed)
      .map((s) => {
        const t0 = Math.max(0, Number(s.t0_ms || 0));
        let t1 = Math.max(t0 + 300, Number(s.t1_ms || 0));
        return {
          clip_id: s.clip_id,
          role: s.role,
          text: String(s.text || "").trim(),
          t0_ms: t0,
          t1_ms: t1,
          score: Number(s.score || 0),
        };
      })
      .filter((s) => s.t1_ms > s.t0_ms);
  const learn = isLearnEnabled();
  const payload = {
    reclip: true,
    learn,
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
    // accept server result and clear dirty lock
    planDirty = false;
    planEdit = null;
    planOriginal = null;
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
  // Simplified toolbar: only 「保存并重剪」 is shown.
  // Hidden legacy nodes keep null-safe hooks if re-enabled later.
  $("plan-reset")?.addEventListener?.("click", () => {
    if (!planOriginal) return;
    planEdit = clonePlan(planOriginal);
    planDirty = true;
    queueRenderTracks();
  });
  $("plan-balance")?.addEventListener?.("click", () => balancePlanDurations());
  $("plan-apply")?.addEventListener?.("click", applyPlanEdit);
  $("learn-clear")?.addEventListener?.("click", clearLearningData);
  const learnEl = $("plan-learn");
  if (learnEl && learnEl.type === "checkbox") {
    try {
      learnEl.checked = localStorage.getItem("clipper_learn_on_reclip") === "1";
    } catch (_) {
      learnEl.checked = false;
    }
    learnEl.addEventListener("change", () => {
      try {
        localStorage.setItem("clipper_learn_on_reclip", learnEl.checked ? "1" : "0");
      } catch (_) {}
    });
  }
}

function setAsrToolsEnabled(on) {
  ["asr-reload", "asr-to-golden"].forEach((id) => {
    if ($(id)) $(id).disabled = !on;
  });
}

function addAsrToTrack(trackKey, indices) {
  // single logical track only
  trackKey = "golden";
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
          <button type="button" class="clip-x asr-add-one" title="加入逻辑成片">＋</button>
        </div>
        <textarea class="clip-text-edit" rows="5" placeholder="编辑这段口播词…">${escapeHtml(u.text || "")}</textarea>
        <div class="clip-time-row">
          <label>开始(s)<input class="clip-t0s" type="number" step="0.1" min="0" value="${a}" /></label>
          <label>结束(s)<input class="clip-t1s" type="number" step="0.1" min="0" value="${b}" /></label>
        </div>
        <div class="meta">拖到逻辑成片，或点 ＋ / 「+加入成片」</div>
        <div class="clip-tools">
          <button type="button" class="asr-add-golden">+加入成片</button>
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
  renderLlmStatus(data);

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
    $("export-btn").disabled = false;
    $("export-btn").onclick = async () => {
      // request final-quality re-render then open download when ready
      try {
        await fetch(`/api/jobs/${encodeURIComponent(data.job_id)}/export-final`, {
          method: "POST",
        });
        const finalUrl = `/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4?v=${Date.now()}`;
        // open current best immediately; poll briefly for final upgrade
        window.open(files.final ? finalUrl : url, "_blank");
      } catch (_) {
        window.open(url, "_blank");
      }
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
  if (files.preview) {
    actions.push(
      `<a class="jy-btn" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/preview.mp4" download>下载预览</a>`
    );
  }
  if (files.final) {
    actions.push(
      `<a class="jy-btn primary" href="/api/jobs/${encodeURIComponent(data.job_id)}/files/final.mp4" download>下载成片</a>`
    );
  }
  if (files.plan || files.final || files.preview) {
    actions.push(
      `<button type="button" class="jy-btn" id="export-final-btn">导出终稿</button>`
    );
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
  // history list removed; keep as lightweight status refresher for current job
  const hint = $("job-run-hint");
  try {
    const res = await fetch("/api/jobs?limit=12");
    const data = await res.json();
    const jobs = data.jobs || [];
    const running = jobs.filter((j) =>
      ["queued", "processing", "starting", "claimed"].includes(j.status)
    );
    if (hint) {
      hint.textContent = running.length
        ? `并发中 ${running.length} 个任务（听写串行，LLM/渲染并行）。当前：${currentJobId || "无"}`
        : `当前任务：${currentJobId || "无"}。可连续上传，任务互不影响。`;
    }
    if (currentJobId) {
      // refresh current only
      const cur = jobs.find((j) => j.job_id === currentJobId);
      if (cur && ["queued", "processing", "starting", "claimed"].includes(cur.status)) {
        // poll handles detailed progress
      } else if (cur) {
        // finished: soft refresh
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
setupLlmConfig();
