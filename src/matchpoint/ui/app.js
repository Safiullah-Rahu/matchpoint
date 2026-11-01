"use strict";
// Matchpoint UI. Plain JS, no build step. Every dynamic value goes through esc():
// traces and invoices contain vendor-supplied text, which is untrusted.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n, cur = "USD") => (n == null ? "—" : `${cur} ${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const pct = (x, d = 1) => `${(Number(x) * 100).toFixed(d)}%`;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let d;
    try { d = await r.json(); } catch { d = { detail: r.statusText }; }
    throw new Error(typeof d.detail === "string" ? d.detail : r.statusText);
  }
  return r.json();
}

const S = { queue: [], sel: null, runId: null, view: null, trace: [], timer: null, filter: "all", q: "", health: {}, config: {}, evalSel: null };

const NODES = [
  ["intake", "Intake", "guard"], ["triage", "Triage", "fast"], ["investigate", "Investigate", "tools"],
  ["policy", "Policy", "RAG"], ["decide", "Decide", "resolver"], ["review", "Review", "engine + LLM"],
  ["human_approval", "Approval", "HITL"], ["execute", "Execute", "idempotent"], ["communicate", "Email", "guarded"],
  ["finalize", "Finalize", "memory"],
];
const STATUS = {
  exception: ["Open", ""], approved_for_payment: ["Approved", "green"], on_hold: ["On hold", "amber"],
  rejected: ["Rejected", "red"], escalated: ["Escalated", "violet"], pending_approval: ["Awaiting approval", "blue"],
};
const ACTION = {
  APPROVE_PAYMENT: ["Approve payment", "green"], REQUEST_CREDIT_MEMO: ["Request credit memo", "amber"],
  HOLD: ["Hold", "amber"], REJECT_DUPLICATE: ["Reject duplicate", "red"], ESCALATE: ["Escalate", "violet"],
};
const FLAG_CLS = { FRAUD_SUSPECTED: "red", INJECTION_DETECTED: "rose", AGENT_FAILURE: "red", REVIEW_DEADLOCK: "amber", APPROVER_REJECTED: "violet" };

const tag = (text, cls = "") => `<span class="tag ${cls}">${esc(text)}</span>`;
const statusTag = (s) => { const [t, c] = STATUS[s] || [s || "—", ""]; return tag(t, c); };
const actionTag = (a) => { const [t, c] = ACTION[a] || [a, ""]; return tag(t, c); };

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 2800);
}

// ------------------------------------------------------------------ chrome
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (name === "approvals") loadApprovals();
  if (name === "evals") loadEvals();
  if (name === "arch") renderArch();
}
function openDrawer(title, html) {
  $("#drawer-title").textContent = title; $("#drawer-body").innerHTML = html;
  $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }
$("#drawer-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("#reset").onclick = async () => {
  if (!confirm("Re-seed the synthetic ERP and clear all runs, approvals and traces?")) return;
  await api("/api/reset", { method: "POST" });
  S.sel = null; S.runId = null; S.view = null; stopPoll();
  $("#detail").innerHTML = `<div class="empty"><h2>Demo data reset</h2><p>52 exception invoices are back in the queue.</p></div>`;
  await loadQueue(); loadApprovalCount(); toast("Demo data re-seeded");
};

async function showPolicy(id) {
  try {
    const p = await api(`/api/policy/${encodeURIComponent(id)}`);
    openDrawer(`${p.section_id} — ${p.title}`, `<p class="muted small">Source: ${esc(p.document)}.md</p><p>${esc(p.text)}</p>`);
  } catch (e) { toast(e.message); }
}
function showSpan(i) {
  const s = S.trace[i];
  if (!s) return;
  const body = { kind: s.kind, status: s.status, error: s.error, duration_ms: s.duration_ms, attributes: s.attributes, events: s.events };
  openDrawer(s.name, `<pre>${esc(JSON.stringify(body, null, 2))}</pre>`);
}
document.addEventListener("click", (e) => {
  const sec = e.target.closest("[data-sec]");
  if (sec) return showPolicy(sec.dataset.sec);
  const sp = e.target.closest("[data-span]");
  if (sp) return showSpan(Number(sp.dataset.span));
});

// ------------------------------------------------------------------- queue
async function loadQueue() { S.queue = await api("/api/queue"); renderQueue(); }
function renderQueue() {
  const q = S.q.toLowerCase();
  const rows = S.queue.filter((r) => {
    if (S.filter === "exception" && r.status !== "exception") return false;
    if (S.filter === "done" && r.status === "exception") return false;
    if (S.filter === "security" && !r.security) return false;
    return !q || [r.invoice_id, r.vendor_name, r.exception_code, r.invoice_number].join(" ").toLowerCase().includes(q);
  });
  $("#queue-list").innerHTML = rows.map((r) => `
    <div class="qitem ${S.sel === r.invoice_id ? "sel" : ""}" data-id="${esc(r.invoice_id)}">
      <div class="qrow"><span class="qid">${esc(r.invoice_id)}</span><span class="qamt">${esc(money(r.total, r.currency))}</span></div>
      <div class="qvendor">${esc(r.vendor_name)} · ${esc(r.invoice_number)}</div>
      <div class="qrow"><span>${tag(r.exception_code || "NO CODE")}${r.security ? " " + tag("security", "rose") : ""}</span>${statusTag(r.status)}</div>
    </div>`).join("") || `<div class="empty small">No invoices match.</div>`;
  $$(".qitem").forEach((el) => (el.onclick = () => select(el.dataset.id)));
}
$("#q").addEventListener("input", (e) => { S.q = e.target.value; renderQueue(); });
$$("#filters button").forEach((b) => (b.onclick = () => {
  S.filter = b.dataset.f; $$("#filters button").forEach((x) => x.classList.toggle("active", x === b)); renderQueue();
}));

function stopPoll() { clearTimeout(S.timer); S.timer = null; }

async function select(id) {
  stopPoll();
  S.sel = id; S.view = null; S.trace = [];
  const row = S.queue.find((r) => r.invoice_id === id);
  S.runId = row && row.last_run ? row.last_run.run_id : null;
  renderQueue(); renderDetail();
  if (S.runId) refreshRun();
}

async function refreshRun() {
  const runId = S.runId;
  if (!runId) return;
  try {
    const [view, trace] = await Promise.all([api(`/api/runs/${runId}`), api(`/api/runs/${runId}/trace`)]);
    if (runId !== S.runId) return;
    S.view = view; S.trace = trace; renderDetail();
    if (view.run.status === "running") S.timer = setTimeout(refreshRun, 450);
    else {
      stopPoll(); loadApprovalCount();
      await loadQueue();
      if (runId === S.runId) renderDetail();
    }
  } catch (e) { toast(e.message); }
}

async function runAgent() {
  const btn = $("#runbtn"); btn.disabled = true; btn.textContent = "Starting…";
  try {
    S.prov = $("#prov").value;
    const { run_id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ invoice_id: S.sel, provider: S.prov }) });
    S.runId = run_id; S.view = null; S.trace = []; refreshRun();
  } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Run agent"; }
}

async function decide(approvalId, approved, comment = "") {
  try {
    const { run_id } = await api(`/api/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST", body: JSON.stringify({ approved, comment, approver: "controller (demo)" }),
    });
    toast(approved ? "Approved — run resumed from its checkpoint" : "Rejected — escalating back to the controller");
    return run_id;
  } catch (e) { toast(e.message); return null; }
}

// ------------------------------------------------------------------ detail
function nodeStates(v) {
  const s = {}, counts = {};
  if (!v) return { s, counts };
  for (const h of v.history || []) {
    counts[h.node] = (counts[h.node] || 0) + (h.status === "interrupted" ? 0 : 1);
    s[h.node] = h.status === "interrupted" ? "waiting" : h.status === "error" ? "error" : "done";
  }
  const r = v.run;
  if (r.status === "running" && r.current_node && r.current_node !== "__end__") s[r.current_node] = "active";
  if (r.status === "interrupted" && r.current_node) s[r.current_node] = "waiting";
  if (r.status === "completed" || r.status === "failed") for (const [n] of NODES) if (!s[n]) s[n] = "skipped";
  return { s, counts };
}

function flowHtml(v) {
  const { s, counts } = nodeStates(v);
  const nodes = NODES.map(([id, label, sub], i) => {
    const st = s[id] || "";
    const badge = counts[id] > 1 ? `<span class="badge">×${counts[id]}</span>` : "";
    return `${i ? '<span class="arrow"></span>' : ""}<div class="node ${st}" title="${esc(id)}">${esc(label)}<small>${esc(sub)}</small>${badge}</div>`;
  }).join("");
  const fs = s.fail_safe ? (s.fail_safe === "skipped" ? "" : "error") : "";
  return `<div class="flow">${nodes}<span class="sep"></span><div class="node ${fs}" title="fail_safe">Fail-safe<small>escalate, never pay</small></div></div>`;
}

function renderDetail() {
  const row = S.queue.find((r) => r.invoice_id === S.sel);
  if (!row) return;
  const v = S.view, run = v && v.run;
  const running = run && run.status === "running";
  const resolved = row.status !== "exception" && !running;
  const anthropic = S.health.anthropic_key;
  const status = v && v.erp && v.erp.status ? v.erp.status : row.status;
  $("#detail").innerHTML = `
    <div class="card">
      <div class="inv-head">
        <div>
          <div class="title">${esc(row.invoice_id)} <span class="muted">· ${esc(row.invoice_number)}</span></div>
          <div class="sub">${esc(row.vendor_name)} · ${row.po_id ? "PO " + esc(row.po_id) : "no purchase order"} · dated ${esc(row.invoice_date)} · ${tag(row.exception_code || "NO CODE")}</div>
        </div>
        <div class="amount"><b>${esc(money(row.total, row.currency))}</b><div>${statusTag(status)}</div></div>
        <div class="actions">
          <select id="prov" title="Model provider">
            <option value="sim">sim (offline reference)</option>
            <option value="sim-gullible">sim · gullible model (obeys injections)</option>
            <option value="sim-chaos">sim · 30% injected faults</option>
            <option value="anthropic" ${anthropic ? "" : "disabled"}>Claude${anthropic ? "" : " (set ANTHROPIC_API_KEY)"}</option>
          </select>
          <button class="primary" id="runbtn" ${running || resolved ? "disabled" : ""}>${running ? "Running…" : status === "pending_approval" ? "Awaiting approval" : resolved ? "Resolved" : "Run agent"}</button>
        </div>
      </div>
      ${flowHtml(v)}
    </div>
    ${v && v.state ? bodyHtml(v) : `<div class="card" style="margin-top:14px"><div class="card-b muted">${run ? "Starting run…" : "No run yet. Run the agent to see the investigation, the decision, the guardrails that checked it and the full trace."}</div></div>`}`;
  const btn = $("#runbtn");
  if (btn) btn.onclick = runAgent;
  const prov = $("#prov");
  if (prov) {
    prov.value = S.prov || (S.health.provider === "anthropic" ? "anthropic" : "sim");
    prov.onchange = (e) => { S.prov = e.target.value; };
  }
  $$("[data-decide]").forEach((b) => (b.onclick = async () => {
    const ok = b.dataset.decide === "approve";
    const runId = await decide(b.dataset.id, ok, ($("#appr-comment") || {}).value || "");
    if (runId) { S.runId = runId; setTimeout(refreshRun, 150); }
  }));
}

function bodyHtml(v) {
  const st = v.state, run = v.run, cur = (st.snapshot && st.snapshot.currency) || "USD";
  return `<div class="grid2"><div>${approvalBanner(v)}${decisionCard(st, cur)}${findingsCard(st)}${guardCard(st, run)}${emailCard(st)}${erpCard(v, cur)}</div><div>${traceCard(st)}</div></div>`;
}

function approvalBanner(v) {
  const r = v.run;
  if (r.status !== "interrupted" || !r.interrupt) return "";
  const i = r.interrupt;
  return `<div class="card" style="border-color:var(--blue)"><div class="card-h"><h3>Controller approval required</h3>${tag("run paused · checkpointed", "blue")}</div>
    <div class="card-b"><p style="margin:0 0 10px">${esc(i.summary)}</p>
    <div class="appr"><textarea id="appr-comment" placeholder="Comment (optional)"></textarea>
    <div style="display:flex;gap:8px"><button class="danger" data-decide="reject" data-id="${esc(i.approval_id)}">Reject</button>
    <button class="primary" data-decide="approve" data-id="${esc(i.approval_id)}">Approve release</button></div></div></div></div>`;
}

function decisionCard(st, cur) {
  const r = st.resolution;
  const flags = (st.flags || []).map((f) => tag(f, FLAG_CLS[f] || "")).join(" ") || '<span class="muted">none</span>';
  const meta = st.revisions ? `${st.revisions} revision${st.revisions > 1 ? "s" : ""} after review` : r ? "accepted on first pass" : "";
  if (!r) return `<div class="card"><div class="card-h"><h3>Decision</h3></div><div class="card-b muted">Pending…</div></div>`;
  const cites = (r.citations || []).map((c) => `<span class="tag blue link" data-sec="${esc(c)}">${esc(c)}</span>`).join(" ");
  const routed = st.models_used && st.models_used.resolver ? `${st.risk} risk → resolver on <code>${esc(st.models_used.resolver)}</code>` : esc(st.risk);
  return `<div class="card"><div class="card-h"><h3>Decision</h3><span class="meta">${esc(meta)}</span></div><div class="card-b">
    <div class="decision">${actionTag(r.action)}<span class="act">${esc((ACTION[r.action] || [r.action])[0])}</span><span class="amt">${r.amount != null ? esc(money(r.amount, cur)) : ""}</span></div>
    <dl class="kv">
      ${r.hold_reason ? `<dt>Hold reason</dt><dd>${tag(r.hold_reason, "amber")}</dd>` : ""}
      ${r.escalate_to ? `<dt>Escalated to</dt><dd>${tag(r.escalate_to, "violet")}</dd>` : ""}
      <dt>Policy cited</dt><dd>${cites || '<span class="muted">none</span>'}</dd>
      <dt>Risk flags</dt><dd>${flags}</dd>
      <dt>Routing</dt><dd>${routed}</dd>
      <dt>Confidence</dt><dd>${esc(Math.round((r.confidence || 0) * 100))}%</dd>
    </dl>
    <div class="rationale">${esc(r.rationale)}</div></div></div>`;
}

function findingsCard(st) {
  const f = st.findings;
  if (!f) return "";
  const t = st.triage;
  const triage = t ? `${tag("triage guess: " + t.category_guess)} ${tag("triage risk: " + t.risk, t.risk === "high" ? "red" : "")}` : tag("triage skipped", "amber");
  return `<div class="card"><div class="card-h"><h3>Investigation</h3><span>${triage}</span></div><div class="card-b">
    <div>${tag(f.diagnosis, "blue")} <span class="muted small">diagnosis after evidence</span></div>
    <p style="margin:10px 0 0">${esc(f.summary)}</p>
    <ul class="evidence">${(f.evidence || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div></div>`;
}

function guardCard(st, run) {
  const rows = [];
  const inj = st.injection || {};
  rows.push(inj.suspicious
    ? [`bad`, `Prompt-injection screen: vendor text tries to direct the AP process (${esc((inj.matches || []).join(", "))}) in ${esc((inj.sources || []).join(", "))}. Flagged, spotlighted as untrusted data, routed to the frontier model; the text has no authority.`]
    : [`ok`, `Prompt-injection screen: no instruction-like vendor text.`]);
  (st.violation_log || []).forEach((vs, i) => {
    rows.push(vs.length
      ? [`warn`, `Policy engine, review ${i + 1}: blocked the proposal and sent it back:<br>${vs.map((x) => `<code>${esc(x)}</code>`).join("<br>")}`]
      : [`ok`, `Policy engine, review ${i + 1}: proposal satisfies every invariant.`]);
  });
  if (st.needs_approval || st.approval) {
    const a = st.approval;
    rows.push([a ? (a.approved ? "ok" : "warn") : "warn", a ? `Human approval: ${a.approved ? "approved" : "rejected"} by ${esc(a.approver)}${a.comment ? ` — “${esc(a.comment)}”` : ""}.` : `Human approval: amount above the release limit; run paused for the controller.`]);
  }
  if (st.retrieved_sections && st.retrieved_sections.length)
    rows.push([`ok`, `Grounding: cited sections must come from retrieval. Retrieved: ${st.retrieved_sections.map((s) => `<span class="tag link" data-sec="${esc(s)}">${esc(s)}</span>`).join(" ")}`]);
  if (st.email) rows.push([`ok`, `Email output guard: draft references the invoice and amount, contains no bank details or internal controls.`]);
  if ((st.flags || []).includes("FRAUD_SUSPECTED")) rows.push([`warn`, `Fraud control: no vendor email; callback verification task created against the vendor-master phone (AP-POL-6.2).`]);
  (st.notes || []).forEach((n) => rows.push([`warn`, esc(n)]));
  if ((st.flags || []).includes("AGENT_FAILURE")) rows.push([`bad`, `Fail-safe: ${esc((st.errors || []).slice(-1)[0] || "error")} → escalated to the controller, nothing paid.`]);
  const icon = { ok: "✓", warn: "!", bad: "✕" };
  return `<div class="card"><div class="card-h"><h3>Controls</h3><span class="meta">deterministic checks around the agents</span></div><div class="card-b">
    ${rows.map(([k, h]) => `<div class="guard"><span class="ico ${k}">${icon[k]}</span><div>${h}</div></div>`).join("")}</div></div>`;
}

function emailCard(st) {
  if (!st.email) return "";
  return `<div class="card"><div class="card-h"><h3>Vendor email draft</h3><span class="meta">to ${esc(st.email_to)} · saved as draft, not sent</span></div>
    <div class="card-b"><b>${esc(st.email.subject)}</b><pre class="email">${esc(st.email.body)}</pre></div></div>`;
}

function erpCard(v, cur) {
  const e = v.erp || {};
  const items = [];
  (e.payment_schedule || []).forEach((p) => items.push(`Payment scheduled: ${esc(money(p.amount, cur))} on ${esc(p.scheduled_for)} (approved by ${esc(p.approved_by)})`));
  (e.credit_memo_requests || []).forEach((c) => items.push(`Credit memo requested: ${esc(money(c.amount, cur))} (${esc(c.reason)})`));
  (e.escalations || []).forEach((x) => items.push(`Escalated to ${esc(x.target)}`));
  (e.verification_tasks || []).forEach((t) => items.push(`Verification task: ${esc(t.method)} → ${esc(t.contact)}`));
  (e.email_drafts || []).forEach((d) => items.push(`Email draft saved for ${esc(d.to_addr)}`));
  const audit = (e.audit_log || []).map((a) => `<li><code>${esc(a.action)}</code> by ${esc(a.actor)} · <span class="muted">${esc(a.idempotency_key.split(":").slice(-1)[0])}</span></li>`).join("");
  return `<div class="card"><div class="card-h"><h3>ERP write-back</h3>${statusTag(e.status)}</div><div class="card-b">
    ${items.length ? `<ul class="evidence">${items.map((i) => `<li>${i}</li>`).join("")}</ul>` : '<span class="muted">No writes yet.</span>'}
    ${audit ? `<div class="muted small" style="margin-top:10px">Audit log (one row per idempotency key)</div><ul class="evidence small">${audit}</ul>` : ""}</div></div>`;
}

function traceCard(st) {
  const u = st.usage || {};
  const spans = S.trace;
  const t0 = spans.length ? Math.min(...spans.map((s) => s.start)) : 0;
  const t1 = spans.length ? Math.max(...spans.map((s) => s.end || s.start)) : 1;
  const span = Math.max(1e-6, t1 - t0);
  const byId = Object.fromEntries(spans.map((s) => [s.span_id, s]));
  const depth = (s) => { let d = 0, p = s.parent_id; while (p && byId[p] && d < 12) { d++; p = byId[p].parent_id; } return d; };
  const label = (s) => {
    const a = s.attributes || {};
    if (s.kind === "llm") return `${s.name} · ${a["gen_ai.request.model"] || ""}${(a["matchpoint.tool_calls"] || []).length ? " → " + a["matchpoint.tool_calls"].join(", ") : ""}`;
    return s.name;
  };
  const rows = spans.map((s, i) => {
    const left = ((s.start - t0) / span) * 100, width = Math.max(0.4, (((s.end || s.start) - s.start) / span) * 100);
    return `<div class="span ${s.status === "error" ? "err" : ""}" data-span="${i}" title="${esc(label(s))}">
      <div class="nm" style="padding-left:${depth(s) * 12}px"><span class="k">${esc(s.kind === "guardrail" ? "guard" : s.kind)}</span>${esc(label(s))}</div>
      <div class="bar-wrap"><div class="bar ${esc(s.kind)}" style="left:${left}%;width:${width}%"></div></div>
      <div class="dur">${esc(s.duration_ms >= 1000 ? (s.duration_ms / 1000).toFixed(2) + "s" : Math.round(s.duration_ms) + "ms")}</div></div>`;
  }).join("");
  const models = Object.entries(st.models_used || {}).map(([k, m]) => `${tag(k)} <code>${esc(m)}</code>`).join(" &nbsp; ");
  const dur = spans.length ? (span >= 1 ? span.toFixed(1) + "s" : Math.round(span * 1000) + "ms") : "—";
  return `<div class="card"><div class="card-h"><h3>Trace</h3><span class="meta">${spans.length} spans · click a row for attributes</span></div>
    <div class="stats">
      <div><b>${esc(dur)}</b><span>wall clock</span></div>
      <div><b>${esc(u.llm_calls ?? 0)}</b><span>LLM calls</span></div>
      <div><b>${esc(((u.input_tokens || 0) + (u.output_tokens || 0)).toLocaleString())}</b><span>tokens</span></div>
      <div><b>$${esc((u.cost_usd || 0).toFixed(4))}</b><span>cost${S.view && S.view.run.provider === "sim" ? " (est.)" : ""}</span></div>
      <div><b>${esc(u.retries ?? 0)}</b><span>retries</span></div>
      <div><b>${esc(u.fallbacks ?? 0)}</b><span>fallbacks</span></div>
    </div>
    ${models ? `<div class="card-b small" style="border-bottom:1px solid var(--border)">${models}</div>` : ""}
    <div class="trace">${rows || '<div class="card-b muted">Spans appear as each step completes.</div>'}</div>
    <div class="legend">${["run", "node", "agent", "llm", "tool", "guardrail"].map((k) => `<span><i class="bar ${k}" style="position:static"></i>${k}</span>`).join("")}</div></div>`;
}

// --------------------------------------------------------------- approvals
async function loadApprovalCount() {
  try {
    const items = await api("/api/approvals");
    const c = $("#appr-count"); c.textContent = items.length; c.hidden = !items.length;
    return items;
  } catch { return []; }
}
async function loadApprovals() {
  const items = await loadApprovalCount();
  const limit = S.config.policy ? money(S.config.policy.approval_limit) : "the release limit";
  $("#approvals").innerHTML = `<h2>Approvals</h2>
    <p class="lede">Fully matched invoices above ${esc(limit)} pause here (AP-POL-8.1). The run is checkpointed at the approval node:
    approving resumes it from that node and releases the payment; rejecting escalates it back to the controller. Nothing is paid while it waits.</p>
    ${items.length ? items.map((a) => `<div class="card"><div class="card-b appr"><div>
      <div><b>${esc(a.invoice_id)}</b> · ${esc(money(a.amount))} ${tag("pending", "blue")}</div>
      <div class="muted small" style="margin:4px 0 8px">${esc(a.summary)}<br>approval ${esc(a.approval_id)} · run ${esc(a.run_id.slice(0, 12))} · requested ${esc(a.created_at)}</div>
      <textarea placeholder="Comment (optional)" data-c="${esc(a.approval_id)}"></textarea></div>
      <div style="display:flex;gap:8px"><button class="danger" data-act="reject" data-id="${esc(a.approval_id)}" data-inv="${esc(a.invoice_id)}">Reject</button>
      <button class="primary" data-act="approve" data-id="${esc(a.approval_id)}" data-inv="${esc(a.invoice_id)}">Approve release</button></div></div></div>`).join("")
      : `<div class="card"><div class="card-b muted">No pending approvals. Run an invoice with exception code APPROVAL_LIMIT to create one.</div></div>`}`;
  $$("#approvals [data-act]").forEach((b) => (b.onclick = async () => {
    const comment = ($(`#approvals textarea[data-c="${CSS.escape(b.dataset.id)}"]`) || {}).value || "";
    const runId = await decide(b.dataset.id, b.dataset.act === "approve", comment);
    if (!runId) return;
    showTab("queue"); await loadQueue(); await select(b.dataset.inv); S.runId = runId; refreshRun();
  }));
}

// ------------------------------------------------------------------- evals
const SUITE_ORDER = ["reference", "chaos-controls-on", "chaos-controls-off", "faulty-model"];
const SUITE_DESC = {
  "reference": "Reference policy, 3 trials per case. Validates labels, graders and plumbing.",
  "chaos-controls-on": "25% of model calls fail (429/529/5xx/timeout) or misbehave (bad args, unknown tool, no tool call). All controls on.",
  "chaos-controls-off": "Same faults with retries, fallback, validation repair, nudges and loop guard switched off.",
  "faulty-model": "35% wrong first-pass decisions (many unsafe) and obeys injected instructions; reviewer misses 35% of safe-but-wrong ones.",
};
async function loadEvals() {
  const reps = await api("/api/evals");
  const names = Object.keys(reps).sort((a, b) => (SUITE_ORDER.indexOf(a) + 1 || 99) - (SUITE_ORDER.indexOf(b) + 1 || 99));
  if (!names.length) {
    $("#evals").innerHTML = `<h2>Evals</h2><p class="lede">No reports yet. Run <code>matchpoint suite</code> (offline, about a minute) or <code>matchpoint eval --provider anthropic --yes</code>.</p>`;
    return;
  }
  S.evalSel = S.evalSel && reps[S.evalSel] ? S.evalSel : names[0];
  const row = (n) => {
    const s = reps[n].summary, c = reps[n].config;
    const faults = Object.values(s.chaos_injected || {}).reduce((a, b) => a + b, 0);
    return `<tr class="${n === S.evalSel ? "hl" : ""}" data-rep="${esc(n)}" style="cursor:pointer">
      <td><b>${esc(n)}</b><div class="muted small">${esc(SUITE_DESC[n] || `${c.provider}, ${s.trials} trial(s)`)}</div></td>
      <td>${esc(c.provider)}</td><td>${s.runs}</td><td><b>${pct(s.pass_at_1)}</b></td><td>${pct(s.pass_hat_k)}</td>
      <td><b style="color:${s.safety_violations ? "var(--red)" : "var(--green)"}">${s.safety_violations}</b></td>
      <td>${s.unsafe_proposals_blocked ?? "—"}</td><td>${s.fail_safe_runs}</td><td>${pct(s.components.first_pass_action)}</td><td>${faults}</td><td>${s.retries_total}</td></tr>`;
  };
  $("#evals").innerHTML = `<h2>Evals</h2>
    <p class="lede">52 labelled exception invoices (13 scenarios × 4 variants, including fraud and prompt-injection cases). Each run gets an isolated ERP copy; graders read the
    ERP end state, not the agent's claims. pass^k counts a case only if all k trials pass. Safety violation = money moved or a vendor contacted when policy forbids it.</p>
    <div class="card"><table class="t"><thead><tr><th>Suite</th><th>Provider</th><th>Runs</th><th>pass@1</th><th>pass^k</th><th>Safety violations</th><th>Unsafe proposals blocked</th><th>Fail-safe</th><th>First-pass action</th><th>Faults injected</th><th>Retries</th></tr></thead>
    <tbody>${names.map(row).join("")}</tbody></table></div>
    <div id="eval-detail" style="margin-top:16px"></div>`;
  $$("#evals [data-rep]").forEach((tr) => (tr.onclick = () => { S.evalSel = tr.dataset.rep; loadEvals(); }));
  renderEvalDetail(await api(`/api/evals/${encodeURIComponent(S.evalSel)}`));
}
function meter(x) { const cls = x >= 0.95 ? "" : x >= 0.8 ? "warn" : "bad"; return `<div class="meter ${cls}"><i style="width:${(x * 100).toFixed(1)}%"></i></div>`; }
function renderEvalDetail(rep) {
  const s = rep.summary;
  const tiles = [
    ["pass@1", pct(s.pass_at_1), s.pass_at_1 >= 0.95 ? "good" : ""], [`pass^${s.trials}`, pct(s.pass_hat_k), ""],
    ["Safety violations", s.safety_violations, s.safety_violations ? "bad" : "good"], ["Fail-safe runs", s.fail_safe_runs, ""],
    ["LLM calls / run", s.llm_calls_per_run, ""], ["Cost / run", "$" + s.cost_usd_per_run.toFixed(4), ""],
    ["Latency p95", s.latency_p95_s + "s", ""], ["Avg revisions", s.avg_revisions, ""],
  ];
  const bars = (obj) => Object.entries(obj).map(([k, v]) => `<span>${esc(k)}</span>${meter(v)}<span class="small">${pct(v, 0)}</span>`).join("");
  const scen = Object.entries(rep.scenarios).map(([n, x]) => `<tr><td>${esc(n)}</td><td>${x.runs}</td><td>${meter(x.pass_rate)}</td><td>${pct(x.pass_rate, 0)}</td><td>${x.safety_violations}</td></tr>`).join("");
  const fails = (rep.failures || []).slice(0, 25).map((f) => `<li><code>${esc(f.case_id)}</code> t${f.trial}: ${esc(f.action)} vs ${esc(f.expected)} — ${esc(f.failed.join("; ").slice(0, 220))}${f.error ? `<br><span class="muted small">${esc(f.error.slice(0, 200))}</span>` : ""}</li>`).join("");
  $("#eval-detail").innerHTML = `<div class="tiles">${tiles.map(([k, v, c]) => `<div class="tile ${c}"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>
    <div class="cols"><div class="card"><div class="card-h"><h3>Outcome graders (gating)</h3><span class="meta">${esc(rep.name)}</span></div><div class="card-b bars">${bars(s.outcome)}</div></div>
    <div class="card"><div class="card-h"><h3>Component diagnostics</h3><span class="meta">where a failure starts</span></div><div class="card-b bars">${bars(s.components)}</div></div></div>
    <div class="cols" style="margin-top:14px"><div class="card"><div class="card-h"><h3>By scenario</h3></div><table class="t"><thead><tr><th>Scenario</th><th>Runs</th><th></th><th>Pass</th><th>Unsafe</th></tr></thead><tbody>${scen}</tbody></table></div>
    <div class="card"><div class="card-h"><h3>Failures</h3><span class="meta">${(rep.failures || []).length}</span></div><div class="card-b">${fails ? `<ul class="evidence small">${fails}</ul>` : '<span class="muted">None.</span>'}</div></div></div>`;
}

// ------------------------------------------------------------ architecture
function renderArch() {
  const c = S.config;
  const roles = c.roles ? Object.entries(c.roles).map(([r, x]) => `<tr><td>${esc(r)}</td><td>${esc(x.tier)}</td><td><code>${esc(c.models[x.tier])}</code></td><td>${esc(x.effort)}</td><td>${x.max_turns}</td></tr>`).join("") : "";
  const lane = (cls, label, boxes) => `<div class="lane ${cls}"><div class="lbl">${label}</div><div class="boxes">${boxes.map(([b, t]) => `<div class="box"><b>${esc(b)}</b>${esc(t)}</div>`).join("")}</div></div>`;
  $("#arch").innerHTML = `<h2>Architecture</h2>
    <p class="lede">Models propose; deterministic code disposes. Control flow is a typed state graph, the LLM agents run inside its nodes,
    and everything that moves money or talks to a vendor passes deterministic checks first.</p>
    <div class="arch">
      ${lane("llm", "LLM agents<br>(proposals)", [["Triage", "fast tier · sets routing risk"], ["Investigator", "read-only tools · findings"], ["Policy analyst", "BM25 RAG · grounded rules"], ["Resolver", "balanced → frontier when high risk"], ["Reviewer", "frontier · second opinion"], ["Communicator", "fast tier · vendor email"]])}
      ${lane("det", "Harness<br>(control)", [["State graph", "checkpoint per node · resume · max steps"], ["Agent loop", "budgets · repair · nudges · loop guard"], ["Model router", "tiers · retries · breaker · fallback"], ["Policy engine", "invariants on ERP facts"], ["Executor", "idempotent writes · TOCTOU re-check"], ["Human approval", "interrupt / resume above 25k"]])}
      ${lane("det", "Guardrails", [["Injection screen", "unicode-tag decoding · spotlighting"], ["Redaction", "bank numbers masked at the tool boundary"], ["Least privilege", "tool subset per role"], ["SQL authorizer", "read-only · table allow-list"], ["Email guard", "no bank data, controls or suspicions"], ["Memory", "written only from verified outcomes"]])}
      ${lane("obs", "Observability<br>& evals", [["Tracing", "OTel GenAI attributes · OTLP bridge"], ["Metrics", "Prometheus /metrics"], ["Budgets", "tokens · cost · calls · wall clock"], ["Eval set", "52 labelled cases × k trials"], ["Ablations", "chaos · faulty model · controls off"], ["CI gate", "thresholds.yaml · zero safety violations"]])}
      ${lane("data", "Data &<br>integration", [["ERP gateway", "SQLite stand-in for SAP/Oracle/NetSuite"], ["Policy corpus", "markdown manual · section ids"], ["REST API", "FastAPI · optional bearer auth"], ["Deploy", "container → ECS / Cloud Run / Container Apps"]])}
    </div>
    ${roles ? `<div class="card" style="margin-top:18px;max-width:1100px"><div class="card-h"><h3>Model routing</h3><span class="meta">high-risk resolver → ${esc(c.routing.high_risk_tier)} · fallback ${esc(JSON.stringify(c.fallback_chain))}</span></div>
      <table class="t"><thead><tr><th>Role</th><th>Tier</th><th>Model</th><th>Effort</th><th>Max turns</th></tr></thead><tbody>${roles}</tbody></table></div>` : ""}`;
}

// -------------------------------------------------------------------- boot
(async function boot() {
  try {
    [S.health, S.config] = await Promise.all([api("/api/health"), api("/api/config")]);
    const pill = $("#provider-pill");
    pill.textContent = S.health.provider === "anthropic" ? "provider: Claude (live)" : `provider: sim${S.health.anthropic_key ? " · Claude key found" : ""}`;
    pill.classList.toggle("live", S.health.provider === "anthropic");
    await loadQueue();
    loadApprovalCount();
    // Deep links: ?invoice=INV-10087 selects an invoice, ?tab=evals|approvals|arch opens a tab, ?eval=<report>.
    const params = new URLSearchParams(location.search);
    if (params.get("eval")) S.evalSel = params.get("eval");
    if (params.get("invoice")) await select(params.get("invoice"));
    if (params.get("tab")) showTab(params.get("tab"));
  } catch (e) { toast("API unavailable: " + e.message); }
})();
