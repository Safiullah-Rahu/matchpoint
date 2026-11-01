"use strict";
/* Matchpoint UI. Plain JS, no build step.
   Every dynamic value passes through esc(): invoices and traces carry vendor-supplied, untrusted text. */

// ------------------------------------------------------------------ utils
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n, cur = "USD") => (n == null ? "—" : `${cur === "USD" ? "$" : cur + " "}${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const moneyShort = (n) => (n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `$${(n / 1e3).toFixed(1)}k` : money(n));
const pct = (x, d = 1) => `${(Number(x) * 100).toFixed(d)}%`;
const dur = (ms) => (ms >= 60000 ? `${(ms / 60000).toFixed(1)} min` : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.max(1, Math.round(ms))} ms`);
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let d;
    try { d = await r.json(); } catch { d = {}; }
    throw new Error(typeof d.detail === "string" ? d.detail : r.statusText);
  }
  return r.json();
}

// ------------------------------------------------------------------ icons
const P = {
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  alert: '<path d="M12 3.8l9 15.7H3z"/><path d="M12 10v4.2M12 17.2v.01"/>',
  shield: '<path d="M12 3l8 3v6c0 4.6-3.4 8.3-8 9-4.6-.7-8-4.4-8-9V6z"/>',
  shieldCheck: '<path d="M12 3l8 3v6c0 4.6-3.4 8.3-8 9-4.6-.7-8-4.4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7.5l8.5 6 8.5-6"/>',
  db: '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c1.5-3.8 4.3-5.5 7.5-5.5s6 1.7 7.5 5.5"/>',
  sparkle: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.6A8.2 8.2 0 1 1 9.4 4a6.6 6.6 0 0 0 10.6 10.6z"/>',
  reset: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  scan: '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16M7.5 12h9"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5v-15M8 8h8M8 11.5h6"/>',
  scale: '<path d="M12 3.5v17M6 20.5h12M5 7h14M5 7l-3 6.5a3 3 0 0 0 6 0zM19 7l-3 6.5a3 3 0 0 0 6 0z"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  bank: '<path d="M3 10l9-6 9 6M5.5 10v7.5M9.8 10v7.5M14.2 10v7.5M18.5 10v7.5M3 20.5h18"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>',
  activity: '<path d="M3 12h4l3-7.5 4 15 3-7.5h4"/>',
  layers: '<path d="M12 3.5l9 5-9 5-9-5z"/><path d="M3 13.5l9 5 9-5"/>',
  inbox: '<path d="M3.5 13l3-8h11l3 8v6.5h-17z"/><path d="M3.5 13H8l1.2 3h5.6L16 13h4.5"/>',
  flag: '<path d="M5.5 21V4M5.5 4h11.5l-2 4 2 4H5.5"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2.5"/><path d="M9.5 2.5v3.5M14.5 2.5v3.5M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
  pen: '<path d="M4 20h4.5L19.5 9a2.1 2.1 0 0 0-3-3L5.5 17z"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  git: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5c0 3.5-4 4-9.5 6"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l2.5 2.5M15 8l2 2"/>',
  route: '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h6.5"/>',
  dollar: '<path d="M12 2.5v19M16.5 6.5c-.9-1.4-2.6-2-4.5-2-2.5 0-4.5 1.3-4.5 3.4 0 4.7 9.5 2.5 9.5 7.6 0 2.1-2 3.5-5 3.5-2.2 0-4.1-.8-5.2-2.4"/>',
  file: '<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5M8.5 13h7M8.5 16.5h5"/>',
  chevron: '<path d="M9.5 6l6 6-6 6"/>',
};
const icon = (name, cls = "") => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || ""}</svg>`;

// ----------------------------------------------------------------- labels
const EXC = {
  PRICE_VARIANCE: "Price variance", QTY_VARIANCE: "Quantity variance", RECEIPT_PENDING: "Receipt pending",
  POSSIBLE_DUPLICATE: "Possible duplicate", BANK_MISMATCH: "Bank details changed", VENDOR_STATUS: "Vendor status",
  NO_PO: "No purchase order", CURRENCY_MISMATCH: "Currency mismatch", APPROVAL_LIMIT: "Above approval limit", MATCH_REVIEW: "Match review",
};
const DIAG = {
  CLEAN_WITHIN_TOLERANCE: "Clean match within tolerance", PRICE_OVER_TOLERANCE: "Price above tolerance", QTY_OVERBILLED: "Quantity billed beyond the PO",
  RECEIPT_PENDING: "Goods not yet received", DUPLICATE: "Duplicate invoice", BANK_DETAILS_RISK: "Bank-detail risk", VENDOR_ON_HOLD: "Vendor on hold",
  NON_PO: "No purchase order", CURRENCY_MISMATCH: "Currency mismatch", OTHER: "Other",
};
const STATUS = {
  exception: ["Open", ""], approved_for_payment: ["Approved", "ok"], pending_approval: ["Awaiting approval", "info"],
  on_hold: ["On hold", "warn"], rejected: ["Rejected", "bad"], escalated: ["Escalated", "esc"],
};
const FLAG = {
  FRAUD_SUSPECTED: ["Fraud suspected", "bad"], INJECTION_DETECTED: ["Prompt injection flagged", "bad"], AGENT_FAILURE: ["Fail-safe used", "warn"],
  REVIEW_DEADLOCK: ["Review deadlock", "warn"], APPROVER_REJECTED: ["Controller declined", "esc"],
};
const ROLE = { triage: "Triage agent", investigator: "Investigator", policy_analyst: "Policy analyst", resolver: "Resolver", reviewer: "Reviewer", communicator: "Communicator" };
const MODEL = { "claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5" };
const modelName = (m) => MODEL[m] || m || "";
const PROVIDERS = [
  ["sim", "Reference model (offline)"], ["sim-gullible", "Reference · gullible model"], ["sim-chaos", "Reference · 30% faults"], ["anthropic", "Claude (live)"],
];

function resolutionMeta(r) {
  if (!r) return ["Pending", "info", "clock"];
  switch (r.action) {
    case "APPROVE_PAYMENT": return ["Approved for payment", "ok", "check"];
    case "REQUEST_CREDIT_MEMO": return ["Credit memo requested", "warn", "dollar"];
    case "REJECT_DUPLICATE": return ["Rejected as duplicate", "bad", "copy"];
    case "ESCALATE": return [r.escalate_to === "budget_owner" ? "Escalated to budget owner" : "Escalated to controller", "esc", "route"];
    case "HOLD":
      if (r.hold_reason === "BANK_VERIFICATION") return ["Held for bank verification", "bad", "lock"];
      if (r.hold_reason === "AWAITING_RECEIPT") return ["Held until goods arrive", "warn", "clock"];
      if (r.hold_reason === "VENDOR_ON_HOLD") return ["Held: vendor on hold", "warn", "lock"];
      return ["On hold", "warn", "lock"];
    default: return [r.action, "info", "check"];
  }
}
const pill = (text, tone = "", dot = true) => `<span class="pill ${tone}">${dot ? '<span class="dot"></span>' : ""}${esc(text)}</span>`;
const statusPill = (s) => { const [t, c] = STATUS[s] || [s || "—", ""]; return pill(t, c); };
const flagPills = (flags) => (flags || []).map((f) => { const [t, c] = FLAG[f] || [f, "warn"]; return pill(t, c); }).join(" ");
function hueOf(name) { let h = 0; for (const ch of String(name || "")) h = (h * 31 + ch.charCodeAt(0)) % 360; return h; }
function avatar(name, lg = false) {
  const initials = String(name || "?").replace(/[^A-Za-z ]/g, " ").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  return `<span class="avatar ${lg ? "lg" : ""}" style="--h:${hueOf(name)}">${esc(initials)}</span>`;
}

// ------------------------------------------------------------------ state
const S = {
  queue: [], health: {}, config: {}, policy: {}, page: "overview", sel: null, runId: null, view: null, trace: [], timer: null,
  filter: "open", q: "", tab: "timeline", prov: null, evalSel: null, pendingRun: null,
};
const qrow = (id) => S.queue.find((r) => r.invoice_id === id);

function toast(msg, kind = "check") {
  const t = $("#toast");
  t.innerHTML = `${icon(kind)}<span>${esc(msg)}</span>`;
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), 3200);
}
function openDrawer(title, html) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = html;
  $("#drawer").classList.add("open"); $("#scrim").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }

// --------------------------------------------------------------- routing
const PAGES = ["overview", "exceptions", "approvals", "evals", "how"];
const TABS = ["timeline", "evidence", "safeguards", "email", "record", "trace"];
function parseHash() {
  const [page, arg, sub] = location.hash.replace(/^#\/?/, "").split("/");
  return { page: PAGES.includes(page) ? page : "overview", arg: arg ? decodeURIComponent(arg) : null, sub: TABS.includes(sub) ? sub : null };
}
async function route() {
  const { page, arg, sub } = parseHash();
  if (sub) S.wantTab = sub;
  S.page = page;
  $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === page));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${page}`));
  closeDrawer();
  if (page !== "exceptions") stopPoll();
  try {
    if (page === "overview") await renderOverview();
    else if (page === "exceptions") {
      if (!S.queue.length) await loadQueue();
      renderList();
      if (arg && arg !== S.sel) await select(arg);
      else if (arg) { if (S.wantTab) { S.tab = S.wantTab; S.wantTab = null; } renderDetail(); if (S.runId && !S.timer) refreshRun(); }
      else if (S.sel) history.replaceState(null, "", `#/exceptions/${S.sel}`), renderDetail();
      else renderDetailEmpty();
    } else if (page === "approvals") await renderApprovals();
    else if (page === "evals") await renderEvals();
    else if (page === "how") renderHow();
  } catch (e) { toast(e.message, "alert"); }
}
const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };

// ------------------------------------------------------------------ data
async function loadQueue() { S.queue = await api("/api/queue"); updateCounts(); }
async function loadApprovalBadge() {
  try {
    const items = await api("/api/approvals");
    const b = $("#appr-badge"); b.textContent = items.length; b.hidden = !items.length;
    return items;
  } catch { return []; }
}
const isOpen = (r) => r.status === "exception" || r.status === "pending_approval";
function updateCounts() {
  const q = S.queue;
  $("#n-open").textContent = q.filter(isOpen).length;
  $("#n-done").textContent = q.filter((r) => !isOpen(r)).length;
  $("#n-flagged").textContent = q.filter((r) => r.security).length;
  $("#n-all").textContent = q.length;
}

// --------------------------------------------------------------- overview
const SHOWCASE = [
  { scenario: "injection_bank_change", icon: "bank", tone: "bad", provider: "sim", title: "New bank details, and a note to the AI",
    text: "The invoice tells the agent to update the vendor's bank account and skip the callback. Watch it hold the payment instead." },
  { scenario: "injection_price_over", icon: "shield", tone: "warn", provider: "sim-gullible", title: "A model that gets fooled",
    text: "This run uses a model that obeys injected instructions. Its approval is blocked by the policy engine and revised." },
  { scenario: "over_approval_limit", icon: "user", tone: "info", provider: "sim", title: "Above the approval limit",
    text: "Fully matched, but over 25,000. The run pauses, checkpointed, until a controller signs off." },
  { scenario: "duplicate_number", icon: "copy", tone: "esc", provider: "sim", title: "A duplicate in disguise",
    text: "Same invoice number, different formatting. The agent finds the paid original and notifies the vendor." },
];
function outcomeLine(a) {
  if (a.status === "running") return "Resolving now";
  if (a.status === "interrupted") return "Waiting for controller approval";
  if (!a.action) return a.status === "failed" ? "Run failed" : "In progress";
  return resolutionMeta(a)[0];
}
async function renderOverview() {
  const el = $("#overview");
  if (!el.innerHTML) el.innerHTML = `<div class="skeleton" style="height:220px;border-radius:20px"></div>`;
  const [ov, feed, sc] = await Promise.all([api("/api/overview"), api("/api/activity?limit=7"), api("/api/scenarios"), loadQueue()]);
  const bySc = Object.fromEntries(sc.map((s) => [s.scenario, s]));
  const resolvedFoot = [ov.released_usd ? `${moneyShort(ov.released_usd)} released` : "", ov.credit_requested_usd ? `${moneyShort(ov.credit_requested_usd)} in credits requested` : ""].filter(Boolean).join(" · ");
  const kpis = [
    ["inbox", "Waiting for the agent", ov.open, `${moneyShort(ov.open_value_usd)} not yet worked`],
    ["check", "Resolved by the agent", ov.resolved, resolvedFoot || "Approvals, holds, credits and escalations"],
    ["user", "Awaiting your approval", ov.awaiting_approval, "Payments above the 25,000 release limit"],
    ["shield", "Fraud attempts held", ov.fraud_held, "Bank-detail mismatches put on hold"],
  ];
  const attention = ov.open + ov.awaiting_approval;
  const max = Math.max(1, ...Object.values(ov.by_exception));
  el.innerHTML = `
    <section class="hero fade-in">
      <p class="eyebrow">Accounts payable · exception desk</p>
      <h1 class="page-title">Invoice exceptions, investigated and resolved, with controls you can audit.</h1>
      <p class="page-sub">${ov.total} invoices failed automatic matching; ${attention} still need attention. Matchpoint's agents gather the evidence,
        apply the AP policy manual and propose a resolution. Deterministic checks decide whether anything moves money.</p>
      <div class="hero-actions">
        <a class="btn primary lg" href="#/exceptions">${icon("inbox")} Open the queue</a>
        <a class="btn lg" href="#/how">${icon("layers")} How it works</a>
      </div>
    </section>
    <div class="grid g4" style="margin-top:16px">
      ${kpis.map(([ic, label, value, foot]) => `<div class="card kpi fade-in"><div class="label">${icon(ic, "sm")}${esc(label)}</div>
        <div class="value">${esc(value)}</div><div class="foot">${esc(foot)}</div></div>`).join("")}
    </div>
    <div class="section-head"><div><h2 class="section-title">Try a scenario</h2><div class="muted small">Four cases that show what the agent does, and what stops it.</div></div>
      <a class="small" href="#/exceptions">All 13 scenarios ${icon("arrow", "sm")}</a></div>
    <div class="grid g4">
      ${SHOWCASE.map((c) => {
        const pick = bySc[c.scenario] || {};
        const row = qrow(pick.invoice_id) || {};
        const open = pick.status === "exception";
        return `<div class="card scenario" data-sc="${esc(c.scenario)}" tabindex="0" role="button">
          <div class="s-ico tint-${c.tone}">${icon(c.icon, "lg")}</div>
          <h4>${esc(c.title)}</h4><p>${esc(c.text)}</p>
          <div class="s-foot"><span class="faint num">${esc(pick.invoice_id || "")} · ${esc(money(row.total, row.currency))}</span>
            <span class="go">${open ? "Run it" : "View result"} ${icon("arrow", "sm")}</span></div></div>`;
      }).join("")}
    </div>
    <div class="grid g2" style="margin-top:32px">
      <div class="card">
        <div class="card-head"><h3>Recent activity</h3><span class="meta">${ov.runs} run${ov.runs === 1 ? "" : "s"}</span></div>
        ${feed.length ? feed.map((a) => {
          const [, tone] = a.action ? resolutionMeta(a) : ["", a.status === "interrupted" ? "info" : ""];
          return `<div class="feed-row" data-inv="${esc(a.invoice_id)}">${avatar(a.vendor_name || a.invoice_id)}
            <div><div class="t">${esc(a.vendor_name || a.invoice_id)} <span class="faint">· ${esc(a.invoice_number || a.invoice_id)}</span></div>
            <div class="s">${esc(outcomeLine(a))} · ${esc(ago(a.updated_at))}</div></div>
            <div>${a.action ? pill(outcomeLine(a), tone) : a.status === "running" ? '<span class="pill accent"><span class="spin" style="width:10px;height:10px;border-width:1.5px"></span>Running</span>' : pill(outcomeLine(a), "info")}</div></div>`;
        }).join("") : `<div class="empty-state" style="padding:40px 24px"><div class="es-ico">${icon("activity", "lg")}</div>
          <h3>No runs yet</h3><p>Pick a scenario above, or open the queue and resolve any invoice.</p></div>`}
      </div>
      <div class="card">
        <div class="card-head"><h3>Exceptions by type</h3><span class="meta">${ov.total} invoices</span></div>
        <div class="card-body">${Object.entries(ov.by_exception).map(([code, n]) => `<div class="hbar"><span>${esc(EXC[code] || code)}</span>
          <div class="track"><div class="fill" style="width:${(n / max) * 100}%"></div></div><span class="num faint" style="text-align:right">${n}</span></div>`).join("")}</div>
      </div>
    </div>`;
  $$(".scenario", el).forEach((card) => {
    const run = () => {
      const c = SHOWCASE.find((x) => x.scenario === card.dataset.sc);
      const pick = bySc[c.scenario];
      if (!pick) return;
      if (pick.status === "exception") S.pendingRun = { invoice: pick.invoice_id, provider: c.provider };
      go(`#/exceptions/${pick.invoice_id}`);
    };
    card.onclick = run;
    card.onkeydown = (e) => { if (e.key === "Enter") run(); };
  });
  $$(".feed-row", el).forEach((r) => (r.onclick = () => go(`#/exceptions/${r.dataset.inv}`)));
}

// ------------------------------------------------------------- exceptions
function renderList() {
  const q = S.q.trim().toLowerCase();
  const rows = S.queue.filter((r) => {
    if (S.filter === "open" && !isOpen(r)) return false;
    if (S.filter === "done" && isOpen(r)) return false;
    if (S.filter === "flagged" && !r.security) return false;
    return !q || [r.invoice_id, r.vendor_name, r.invoice_number, EXC[r.exception_code], r.exception_code].join(" ").toLowerCase().includes(q);
  });
  S.visible = rows.map((r) => r.invoice_id);
  $("#q-count").textContent = `${rows.length} shown`;
  $("#list").innerHTML = rows.map((r) => {
    const running = r.last_run && r.last_run.status === "running";
    return `<div class="row ${S.sel === r.invoice_id ? "sel" : ""}" data-id="${esc(r.invoice_id)}" role="option" aria-selected="${S.sel === r.invoice_id}">
      ${avatar(r.vendor_name)}
      <div class="r-main"><div class="r-title">${esc(r.vendor_name)}</div>
        <div class="r-sub">${esc(r.invoice_number)} · ${esc(EXC[r.exception_code] || r.exception_code)}${r.security ? ` · <span style="color:var(--bad)">flagged</span>` : ""}</div></div>
      <div class="r-end"><div class="r-amt">${esc(money(r.total, r.currency))}</div>
        ${running ? '<span class="pill accent"><span class="spin" style="width:10px;height:10px;border-width:1.5px"></span>Running</span>' : statusPill(r.status)}</div>
    </div>`;
  }).join("") || `<div class="list-empty">${icon("search", "lg")}<div style="margin-top:8px">No invoices match.</div></div>`;
  $$(".row").forEach((el) => (el.onclick = () => go(`#/exceptions/${el.dataset.id}`)));
}
$("#q").addEventListener("input", (e) => { S.q = e.target.value; renderList(); });
$$("#filters button").forEach((b) => (b.onclick = () => {
  S.filter = b.dataset.f;
  $$("#filters button").forEach((x) => x.classList.toggle("active", x === b));
  renderList();
}));

function stopPoll() { clearTimeout(S.timer); S.timer = null; }

async function select(id) {
  stopPoll();
  S.sel = id; S.view = null; S.trace = []; S.tab = S.wantTab || "timeline"; S.wantTab = null;
  if (!S.queue.length) await loadQueue();
  const row = qrow(id);
  if (!row) { renderDetailEmpty("That invoice isn't in the queue."); return; }
  if (!isOpen(row) && S.filter === "open") {
    S.filter = "all";
    $$("#filters button").forEach((x) => x.classList.toggle("active", x.dataset.f === "all"));
  }
  S.runId = row.last_run ? row.last_run.run_id : null;
  renderList(); renderDetail();
  $(".row.sel")?.scrollIntoView({ block: "nearest" });
  if (S.runId) await refreshRun();
  const pending = S.pendingRun;
  if (pending && pending.invoice === id) {
    S.pendingRun = null;
    if (row.status === "exception" && !(S.view && ["running", "interrupted"].includes(S.view.run.status))) startRun(pending.provider);
  }
}

async function refreshRun() {
  const runId = S.runId;
  if (!runId) return;
  try {
    const [view, trace] = await Promise.all([api(`/api/runs/${runId}`), api(`/api/runs/${runId}/trace`)]);
    if (runId !== S.runId) return;
    S.view = view; S.trace = trace;
    renderDetail();
    if (view.run.status === "running") S.timer = setTimeout(refreshRun, 450);
    else {
      stopPoll();
      await loadQueue();
      loadApprovalBadge();
      if (runId === S.runId && S.page === "exceptions") { renderList(); renderDetail(); }
    }
  } catch (e) { toast(e.message, "alert"); }
}

async function startRun(provider) {
  const prov = provider || S.prov || "sim";
  S.prov = prov;
  try {
    const { run_id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ invoice_id: S.sel, provider: prov }) });
    S.runId = run_id; S.view = null; S.trace = []; S.tab = "timeline";
    const row = qrow(S.sel);
    if (row) row.last_run = { run_id, status: "running" };
    renderList();
    refreshRun();
  } catch (e) { toast(e.message, "alert"); }
}

async function decide(approvalId, approved, comment = "") {
  try {
    const { run_id } = await api(`/api/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST", body: JSON.stringify({ approved, comment, approver: "Controller (demo)" }),
    });
    toast(approved ? "Approved. The run resumed from its checkpoint." : "Declined. Escalating back to the controller.", approved ? "check" : "route");
    return run_id;
  } catch (e) { toast(e.message, "alert"); return null; }
}

function renderDetailEmpty(msg) {
  $("#detail").innerHTML = `<div class="empty-state" style="padding-top:18vh"><div class="es-ico">${icon("inbox", "lg")}</div>
    <h3>${esc(msg || "Select an invoice")}</h3>
    <p>Each invoice here failed automatic three-way matching. Open one to see what the agent finds, what it decides and which controls checked it.</p></div>`;
}

// timeline model --------------------------------------------------------
const STEPS = [
  { node: "intake", title: "Screen vendor text", icon: "scan", desc: "Checks notes and attachments for instructions aimed at the AI and masks bank numbers." },
  { node: "triage", title: "Triage", icon: "compass", role: "triage", desc: "A first look at the header. Sets the risk level used for model routing." },
  { node: "investigate", title: "Investigate", icon: "search", role: "investigator", desc: "Gathers evidence with read-only tools: vendor master, three-way match, duplicates and history." },
  { node: "policy", title: "Find the policy", icon: "book", role: "policy_analyst", desc: "Retrieves the governing sections of the AP policy manual." },
  { node: "decide", title: "Propose a resolution", icon: "scale", role: "resolver", desc: "Weighs the findings against policy and proposes what to do." },
  { node: "review", title: "Check the proposal", icon: "shieldCheck", role: "reviewer", desc: "The deterministic policy engine first, then an independent reviewer model." },
  { node: "human_approval", title: "Controller approval", icon: "user", desc: "Only for payments above the release limit." },
  { node: "execute", title: "Write to the ERP", icon: "db", desc: "Applies the resolution exactly once, re-checked against fresh ERP data." },
  { node: "communicate", title: "Vendor email", icon: "mail", role: "communicator", desc: "Drafts a message when the vendor needs to act. Withheld on fraud cases." },
  { node: "finalize", title: "Record the outcome", icon: "check", desc: "Writes a verified note to the vendor's history." },
];

function traceIndex() {
  const byId = Object.fromEntries(S.trace.map((s) => [s.span_id, s]));
  const nodeOf = (s) => { let p = s; let n = 0; while (p && n++ < 20) { if (p.kind === "node") return p.name.replace("node.", ""); p = byId[p.parent_id]; } return null; };
  const time = {}, tools = {}, last = {};
  for (const s of S.trace) {
    const node = nodeOf(s);
    if (!node) continue;
    if (s.kind === "node") time[node] = (time[node] || 0) + s.duration_ms;
    if (s.kind === "tool") (tools[node] = tools[node] || []).push(s.attributes["tool.name"] || s.name.replace("tool.", ""));
    if (s.kind === "tool" || s.kind === "llm") if (!last[node] || s.end > last[node].end) last[node] = s;
  }
  return { time, tools, last };
}

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
  if (["completed", "failed"].includes(r.status)) for (const st of STEPS) if (!s[st.node]) s[st.node] = "skipped";
  return { s, counts };
}

function stepResult(node, st, v) {
  const f = st.findings, r = st.resolution;
  switch (node) {
    case "intake": {
      const inj = st.injection || {};
      return inj.suspicious
        ? [`Instructions aimed at the AI found in ${(inj.sources || []).map((x) => x.replace("invoice.", "invoice ")).join(" and ")}. Flagged and passed on as untrusted data.`, "warn"]
        : ["No instruction-like text found. Bank numbers masked.", ""];
    }
    case "triage":
      if (!st.triage) return ["Triage unavailable, so the case was treated as high risk.", "warn"];
      return [`First guess: ${DIAG[st.triage.category_guess] || st.triage.category_guess}, ${st.triage.risk} risk.${st.risk === "high" && st.triage.risk !== "high" ? " Raised to high risk by the guardrails." : ""}`, ""];
    case "investigate":
      return f ? [`${DIAG[f.diagnosis] || f.diagnosis}. ${f.evidence.length} pieces of evidence, each from a tool result.`, ""] : ["", ""];
    case "policy":
      return st.analysis ? [`Applied ${st.analysis.rules.length} of ${st.retrieved_sections.length} retrieved sections.`, ""] : ["", ""];
    case "decide": {
      const fp = st.first_resolution;
      if (!fp) return ["", ""];
      if (st.revisions && r) return [`First proposal: ${resolutionMeta(fp)[0]}. Revised to: ${resolutionMeta(r)[0]}.`, "warn"];
      return [`Proposed: ${resolutionMeta(fp)[0]}.`, ""];
    }
    case "review": {
      const log = st.violation_log || [];
      if (!log.length) return ["", ""];
      const blocked = log.filter((x) => x.length);
      if (blocked.length) return [`Blocked ${blocked.length} proposal${blocked.length > 1 ? "s" : ""} (${[...new Set(blocked.flat().map((x) => x.slice(1).split("]")[0]))].join(", ")}), then passed.`, "warn"];
      return ["Passed every policy invariant. The reviewer agreed.", ""];
    }
    case "human_approval":
      if (st.approval) return [st.approval.approved ? `Approved by ${st.approval.approver}.` : `Declined by ${st.approval.approver}.`, st.approval.approved ? "" : "warn"];
      if (v.run.status === "interrupted") return ["Waiting for the controller.", ""];
      return ["Not needed for this resolution.", ""];
    case "execute":
      return st.execution ? [`Invoice set to ${(STATUS[st.execution.invoice_status] || [st.execution.invoice_status])[0].toLowerCase()}${st.execution.applied ? "" : " (already applied)"}.`, ""] : ["", ""];
    case "communicate":
      if (st.email) return [`Draft saved for ${st.email_to}. Nothing is sent automatically.`, ""];
      if ((st.flags || []).includes("FRAUD_SUSPECTED")) return ["Withheld: the vendor is called back on the number on file instead.", ""];
      if ((st.notes || []).some((n) => n.includes("manual drafting"))) return ["Left for manual drafting after the email guard rejected the draft.", "warn"];
      return ["Not needed for this resolution.", ""];
    case "finalize": return ["Outcome noted in the vendor's history.", ""];
    default: return ["", ""];
  }
}

function timelineHtml(v) {
  const st = v && v.state, { s, counts } = nodeStates(v), idx = v ? traceIndex() : { time: {}, tools: {}, last: {} };
  const failSafe = st && (st.flags || []).includes("AGENT_FAILURE");
  const steps = STEPS.map((step) => {
    const state = s[step.node] || "";
    const model = st && step.role && st.models_used[step.role];
    const agent = step.role ? `${ROLE[step.role]}${model ? ` · ${modelName(model)}` : ""}` : "Deterministic";
    let text = step.desc, tone = "";
    if (st && (state === "done" || state === "waiting" || state === "skipped")) {
      const [t, k] = stepResult(step.node, st, v);
      if (t) { text = t; tone = k; }
    }
    if (state === "active") {
      const last = idx.last[step.node];
      text = last ? (last.kind === "tool" ? `Calling ${last.attributes["tool.name"] || last.name}…` : `Thinking with ${modelName(last.attributes["gen_ai.request.model"])}…`) : "Working…";
    }
    const chips = [];
    if (state === "done" && idx.tools[step.node]) {
      const uniq = [...new Set(idx.tools[step.node])];
      chips.push(...uniq.slice(0, 6).map((t) => `<span class="chip">${esc(t)}</span>`));
    }
    if (st && step.node === "policy" && st.analysis) chips.push(...st.analysis.rules.map((r) => `<button class="cite" data-sec="${esc(r.section_id)}"><b>${esc(r.section_id)}</b>${esc((S.policy[r.section_id] || "").slice(0, 34))}</button>`));
    const badge = counts[step.node] > 1 ? `<span class="pill warn" style="height:20px">×${counts[step.node]}</span>` : "";
    const dotIcon = state === "active" ? '<span class="spin"></span>' : state === "done" ? icon(tone === "warn" ? "alert" : "check") : icon(step.icon);
    const t = idx.time[step.node];
    return `<div class="step ${state} ${tone === "warn" && state === "done" ? "warn" : ""}">
      <div class="s-dot">${dotIcon}</div>
      <div><div class="s-title">${esc(step.title)} <span class="s-agent">${esc(agent)}</span>${badge}</div>
        <div class="s-text">${esc(text)}</div>${chips.length ? `<div class="s-chips">${chips.join("")}</div>` : ""}</div>
      <div class="s-time">${t ? esc(dur(t)) : ""}</div></div>`;
  }).join("");
  const fs = failSafe ? `<div class="step error"><div class="s-dot">${icon("alert")}</div><div><div class="s-title">Fail-safe</div>
    <div class="s-text">${esc((st.errors || []).slice(-1)[0] || "An error occurred")}. The invoice was escalated to the controller and nothing was paid.</div></div><div></div></div>` : "";
  return `<div class="card card-body"><div class="timeline">${steps}${fs}</div></div>`;
}

// explanation -----------------------------------------------------------
function explain(st) {
  const r = st.resolution, f = st.findings || {}, cur = (st.snapshot || {}).currency || "USD";
  const flags = st.flags || [];
  let s;
  if (flags.includes("AGENT_FAILURE")) s = "The agent could not finish this case safely, so the harness escalated it to the controller. Nothing was paid.";
  else if (flags.includes("APPROVER_REJECTED")) s = `The controller declined the release${st.approval && st.approval.comment ? ` ("${st.approval.comment}")` : ""}, so the invoice went back to the controller's queue. Nothing was paid.`;
  else switch (r.action) {
    case "APPROVE_PAYMENT":
      s = st.approval ? `Every check passed. Because the amount is above the release limit, a controller signed off before ${money(r.amount, cur)} was released for payment.`
        : `Every check passed in policy order, so the agent released ${money(r.amount, cur)} for payment.`;
      break;
    case "REQUEST_CREDIT_MEMO":
      s = f.diagnosis === "QTY_OVERBILLED"
        ? `The invoice bills more units than the purchase order allows. The agent put it on hold and asked the vendor for a credit memo of ${money(r.amount, cur)}.`
        : `The unit price is above the purchase order by more than the agreed tolerance. The agent put the invoice on hold and asked the vendor for a credit memo of ${money(r.amount, cur)}.`;
      break;
    case "HOLD":
      s = {
        BANK_VERIFICATION: "The remit-to bank details don't match the vendor master, or changed recently without verification. Payment is on hold until the vendor is called back on the number on file. No email is sent, because the contact details may be compromised.",
        AWAITING_RECEIPT: "Not all of the invoiced goods have been received yet. The invoice is on hold and will release once the goods receipt is posted.",
        VENDOR_ON_HOLD: "This vendor is on hold in the vendor master, so no payment can be released. Vendor Management owns the next step.",
      }[r.hold_reason] || "The invoice was put on hold.";
      break;
    case "REJECT_DUPLICATE":
      s = `This invoice duplicates ${f.duplicate_of || "an invoice"} that was already processed, so it was rejected and the vendor was asked to withdraw it.`;
      break;
    case "ESCALATE":
      s = r.escalate_to === "budget_owner"
        ? "There is no purchase order to match against, so the invoice went to the budget owner for coding and approval."
        : f.diagnosis === "CURRENCY_MISMATCH" ? "The invoice is in a different currency from the purchase order. AP doesn't convert currencies, so it was escalated to the controller."
          : "The case needs a controller's judgement, so it was escalated. Nothing was paid.";
      break;
    default: s = r.rationale;
  }
  if (flags.includes("INJECTION_DETECTED")) s += " The invoice also contained instructions aimed at the AI. They were flagged and had no effect.";
  if (st.revisions) s += ` The first proposal didn't pass review and was revised ${st.revisions === 1 ? "once" : `${st.revisions} times`}.`;
  return s;
}

function runDurationMs() {
  return S.trace.filter((s) => s.kind === "run").reduce((a, s) => a + s.duration_ms, 0);
}

function outcomeHtml(st, run) {
  const r = st.resolution;
  if (!r || run.status === "running" || run.status === "interrupted") return "";
  const [label, tone, ic] = resolutionMeta(r);
  const cur = (st.snapshot || {}).currency || "USD";
  const cites = (r.citations || []).map((c) => `<button class="cite" data-sec="${esc(c)}"><b>${esc(c)}</b>${esc((S.policy[c] || "").slice(0, 40))}</button>`).join("");
  const reviewer = st.models_used && st.models_used.reviewer ? modelName(st.models_used.reviewer) : null;
  const u = st.usage || {};
  return `<section class="outcome ${tone} fade-in">
    <div class="o-top"><div class="o-ico">${icon(ic, "lg")}</div>
      <div><div class="o-eyebrow">Resolution</div><div class="o-title">${esc(label)}</div></div>
      ${r.amount != null ? `<div class="o-amt">${esc(money(r.amount, cur))}</div>` : ""}</div>
    <p class="o-text">${esc(explain(st))}</p>
    <div class="o-chips">${flagPills(st.flags)}${cites}</div>
    <div class="o-foot">
      <span>${icon("shieldCheck", "sm")}Checked by the policy engine${reviewer ? ` and ${esc(reviewer)}` : ""}</span>
      <span>${icon("clock", "sm")}Resolved in ${esc(dur(runDurationMs()))}</span>
      <span>${icon("cpu", "sm")}${u.llm_calls || 0} model calls · $${(u.cost_usd || 0).toFixed(3)}${run.provider && run.provider.includes("sim") ? " (simulated)" : ""}</span>
      <span>${icon("activity", "sm")}Confidence ${Math.round((r.confidence || 0) * 100)}%</span>
    </div></section>`;
}

function approvalHtml(v) {
  const r = v.run;
  if (r.status !== "interrupted" || !r.interrupt) return "";
  const i = r.interrupt;
  return `<section class="approve-box fade-in">
    <div class="ab-row"><div class="o-ico" style="--tone:var(--info)">${icon("user", "lg")}</div>
      <div style="flex:1"><div class="o-eyebrow" style="color:var(--info)">Waiting for you</div>
        <div class="o-title">Controller approval required</div>
        <p class="o-text" style="margin-top:8px">${esc(i.summary)} The run is checkpointed; nothing is paid while it waits.</p></div>
      <div class="o-amt">${esc(money(i.amount))}</div></div>
    <div style="display:grid;grid-template-columns:1fr auto;gap:12px;margin-top:16px;align-items:start">
      <textarea id="appr-comment" placeholder="Add a note for the audit log (optional)"></textarea>
      <div style="display:flex;gap:8px"><button class="btn danger" data-decide="no" data-id="${esc(i.approval_id)}">${icon("x")}Decline</button>
        <button class="btn primary" data-decide="yes" data-id="${esc(i.approval_id)}">${icon("check")}Approve release</button></div>
    </div></section>`;
}

function guardItems(st, v) {
  const items = [];
  const inj = st.injection || {};
  items.push(inj.suspicious
    ? ["bad", "alert", "Prompt injection flagged", `Vendor text tries to direct the AP process (${(inj.matches || []).join(", ")}). It reached the models inside an untrusted-data envelope, the case was routed to the frontier model, and the text had no authority over the outcome.`]
    : ["ok", "scan", "Prompt-injection screen", "No instruction-like vendor text. Vendor text is still wrapped as untrusted data."]);
  const violation = (x) => {
    const code = x.slice(1).split("]")[0], msg = x.slice(code.length + 2).trim();
    const short = msg.split(" (")[0].replace(/:$/, "");
    return `<div style="margin-top:4px"><span class="chip bad">${esc(code)}</span> <span title="${esc(msg)}">${esc(short)}</span></div>`;
  };
  (st.violation_log || []).forEach((vs, i) => {
    items.push(vs.length
      ? ["warn", "shield", `Policy engine blocked proposal ${i + 1} and sent it back`, vs.map(violation).join("")]
      : ["ok", "shieldCheck", `Policy engine passed proposal ${i + 1}`, "Checked against facts recomputed from the ERP, not against what the agent reported."]);
  });
  if (st.review && st.models_used && st.models_used.reviewer) items.push(["ok", "eye", "Independent reviewer", `${esc(modelName(st.models_used.reviewer))} reviewed the final proposal and agreed.`]);
  if (st.needs_approval || st.approval) {
    const a = st.approval;
    items.push(a ? [a.approved ? "ok" : "warn", "user", `Controller ${a.approved ? "approved" : "declined"}`, `${esc(a.approver)}${a.comment ? `: “${esc(a.comment)}”` : ""}`]
      : ["info", "user", "Controller approval pending", "Above the 25,000 release limit (AP-POL-8.1)."]);
  }
  if ((st.flags || []).includes("FRAUD_SUSPECTED")) items.push(["warn", "lock", "Fraud control", "No vendor email. A callback task was created against the phone number in the vendor master (AP-POL-6.2)."]);
  items.push(["ok", "key", "Bank numbers masked", "Full account numbers never reach a model; tools only expose the last four digits."]);
  if (st.retrieved_sections && st.retrieved_sections.length) items.push(["ok", "book", "Grounded citations", `Cited sections must come from retrieval. Retrieved: ${st.retrieved_sections.map((x) => `<button class="cite" data-sec="${esc(x)}"><b>${esc(x)}</b></button>`).join(" ")}`]);
  if (st.email) items.push(["ok", "mail", "Email output guard", "The draft references the invoice and amount, and contains no bank details, controls or suspicions."]);
  if (st.execution) items.push(["ok", "db", "Idempotent write", `Applied once under key <code>${esc(st.execution.idempotency_key.split(":").slice(-2).join(":"))}</code>. A retry or resume cannot apply it twice.`]);
  (st.notes || []).forEach((n) => items.push(["warn", "alert", "Note", esc(n)]));
  if ((st.flags || []).includes("AGENT_FAILURE")) items.push(["bad", "alert", "Fail-safe used", `${esc((st.errors || []).slice(-1)[0] || "error")}. Escalated to the controller; nothing paid.`]);
  return items;
}

function evidenceHtml(st) {
  const f = st.findings;
  if (!f) return `<div class="card card-body muted">Evidence appears once the investigator finishes.</div>`;
  const CHECKS = [
    ["Vendor is active in the vendor master", f.vendor_status === "active", "AP-POL-2.2"],
    ["Bank details match the vendor master", f.bank_details_match && !f.bank_change_recent_unverified, "AP-POL-6.2"],
    ["Not a duplicate of an earlier invoice", !f.duplicate_of, "AP-POL-4.1"],
    ["A purchase order exists", f.po_found, "AP-POL-7.1"],
    ["Currency matches the purchase order", f.currency_match, "AP-POL-3.6"],
    ["Invoiced goods have been received", f.receipt_complete, "AP-POL-3.4"],
    ["Quantity is within the purchase order", f.diagnosis !== "QTY_OVERBILLED", "AP-POL-3.3"],
    ["Price is within tolerance", f.price_within_tolerance, "AP-POL-3.2"],
  ];
  const first = CHECKS.findIndex((c) => !c[1]);
  const rows = CHECKS.map(([name, ok, sec], i) => {
    const cls = first >= 0 && i > first ? "skip" : ok ? "pass" : "fail";
    const note = cls === "skip" ? "Not evaluated: an earlier check decided the case" : "";
    return `<div class="check ${cls}"><div class="c-ico">${icon(cls === "pass" ? "check" : cls === "fail" ? "x" : "clock", "sm")}</div>
      <div><div class="c-name">${esc(name)}${i === first ? '<span class="decisive">Decisive</span>' : ""}</div>${note ? `<div class="c-note">${note}</div>` : ""}</div>
      <button class="cite" data-sec="${sec}"><b>${sec}</b></button></div>`;
  }).join("");
  const rules = (st.analysis ? st.analysis.rules : []).map((r) => `<div class="guard"><div class="g-ico tint-accent">${icon("book")}</div>
    <div><div class="g-title"><button class="cite" data-sec="${esc(r.section_id)}"><b>${esc(r.section_id)}</b>${esc(S.policy[r.section_id] || "")}</button></div>
    <div class="g-text">${esc(r.rule)}</div><div class="g-text faint">${esc(r.applies_because)}</div></div></div>`).join("");
  return `<div class="grid g2">
    <div class="card"><div class="card-head"><h3>Checks, in policy order</h3><span class="meta">AP-POL-1.2</span></div><div class="card-body" style="padding-top:4px;padding-bottom:4px">${rows}
      ${first < 0 ? `<div class="check pass"><div class="c-ico">${icon("check", "sm")}</div><div><div class="c-name">All checks passed</div><div class="c-note">Payments above 25,000 still need a controller (AP-POL-8.1).</div></div><span></span></div>` : ""}</div></div>
    <div class="stack">
      <div class="card"><div class="card-head"><h3>What the investigator found</h3>${pill(DIAG[f.diagnosis] || f.diagnosis, "accent", false)}</div>
        <div class="card-body"><p style="margin:0 0 10px">${esc(f.summary)}</p>
        <ul class="evidence">${f.evidence.map((e) => `<li>${icon("chevron", "sm")}<span>${esc(e)}</span></li>`).join("")}</ul></div></div>
      ${rules ? `<div class="card"><div class="card-head"><h3>Policy applied</h3><span class="meta">from retrieval</span></div><div class="card-body" style="padding-top:2px;padding-bottom:2px">${rules}</div></div>` : ""}
    </div></div>`;
}

function recordHtml(v, cur) {
  const e = v.erp || {};
  const writes = [];
  (e.payment_schedule || []).forEach((p) => writes.push(["Payment scheduled", `${money(p.amount, cur)} on ${p.scheduled_for}`, `approved by ${p.approved_by}`]));
  (e.credit_memo_requests || []).forEach((c) => writes.push(["Credit memo requested", money(c.amount, cur), c.reason]));
  (e.escalations || []).forEach((x) => writes.push(["Escalation", x.target, (x.reason || "").slice(0, 80)]));
  (e.verification_tasks || []).forEach((t) => writes.push(["Verification task", t.method.replaceAll("_", " "), t.contact]));
  (e.email_drafts || []).forEach((d) => writes.push(["Email draft", d.to_addr, d.status]));
  const audit = (e.audit_log || []).map((a) => `<tr><td><code>${esc(a.action)}</code></td><td>${esc(a.actor)}</td><td><code>${esc(a.idempotency_key.split(":").slice(-1)[0])}</code></td><td class="faint">${esc(new Date(a.created_at).toLocaleString())}</td></tr>`).join("");
  return `<div class="grid g2">
    <div class="card"><div class="card-head"><h3>ERP write-back</h3>${statusPill(e.status)}</div>
      ${writes.length ? `<table class="table"><tbody>${writes.map(([a, b, c]) => `<tr><td><b style="font-weight:600">${esc(a)}</b></td><td class="num">${esc(b)}</td><td class="faint">${esc(c)}</td></tr>`).join("")}</tbody></table>` : '<div class="card-body muted">No writes yet.</div>'}</div>
    <div class="card"><div class="card-head"><h3>Audit log</h3><span class="meta">one row per idempotency key</span></div>
      ${audit ? `<table class="table"><thead><tr><th>Action</th><th>Actor</th><th>Key</th><th>When</th></tr></thead><tbody>${audit}</tbody></table>` : '<div class="card-body muted">Empty.</div>'}</div></div>`;
}

function emailHtml(st) {
  if (!st.email) {
    const [t] = stepResult("communicate", st, S.view);
    return `<div class="card"><div class="empty-state" style="padding:48px 24px"><div class="es-ico">${icon("mail", "lg")}</div><h3>No vendor email</h3><p>${esc(t)}</p></div></div>`;
  }
  return `<div class="mail"><div class="mail-head"><div><span class="faint">To</span> &nbsp;<b>${esc(st.email_to)}</b></div>
    <div><span class="faint">Subject</span> &nbsp;<b>${esc(st.email.subject)}</b></div></div>
    <div class="mail-body">${esc(st.email.body)}</div></div>
    <p class="muted small" style="margin-top:12px">${icon("shieldCheck", "sm")} Saved as a draft for the AP mailbox after passing the email guard. Nothing is sent automatically.</p>`;
}

function traceHtml(st) {
  const u = st.usage || {}, spans = S.trace;
  const t0 = spans.length ? Math.min(...spans.map((s) => s.start)) : 0;
  const t1 = spans.length ? Math.max(...spans.map((s) => s.end || s.start)) : 1;
  const span = Math.max(1e-6, t1 - t0);
  const byId = Object.fromEntries(spans.map((s) => [s.span_id, s]));
  const depth = (s) => { let d = 0, p = s.parent_id; while (p && byId[p] && d < 12) { d++; p = byId[p].parent_id; } return d; };
  const label = (s) => s.kind === "llm" ? `${s.name} · ${modelName(s.attributes["gen_ai.request.model"])}${(s.attributes["matchpoint.tool_calls"] || []).length ? " → " + s.attributes["matchpoint.tool_calls"].join(", ") : ""}` : s.name;
  const rows = spans.map((s, i) => {
    const left = ((s.start - t0) / span) * 100, width = Math.max(0.4, (((s.end || s.start) - s.start) / span) * 100);
    return `<div class="span-row ${s.status === "error" ? "err" : ""}" data-span="${i}" title="${esc(label(s))}">
      <div class="nm" style="padding-left:${depth(s) * 14}px"><span class="k">${esc(s.kind === "guardrail" ? "guard" : s.kind)}</span>${esc(label(s))}</div>
      <div class="bw"><div class="bar ${esc(s.kind)}" style="left:${left}%;width:${width}%"></div></div>
      <div class="dur">${esc(dur(s.duration_ms))}</div></div>`;
  }).join("");
  return `<div class="card"><div class="stats">
      <div><b>${esc(dur(runDurationMs()))}</b><span>active time</span></div>
      <div><b>${u.llm_calls || 0}</b><span>model calls</span></div>
      <div><b>${((u.input_tokens || 0) + (u.output_tokens || 0)).toLocaleString()}</b><span>tokens</span></div>
      <div><b>$${(u.cost_usd || 0).toFixed(4)}</b><span>cost${S.view.run.provider.includes("sim") ? " (simulated)" : ""}</span></div>
      <div><b>${u.retries || 0}</b><span>retries</span></div>
      <div><b>${u.fallbacks || 0}</b><span>fallbacks</span></div></div>
    <div class="card-body" style="border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12.5px">
      ${Object.entries(st.models_used || {}).map(([k, m]) => `<span><span class="faint">${esc(ROLE[k] || k)}</span> &nbsp;<code>${esc(m)}</code></span>`).join("") || '<span class="faint">No model calls yet.</span>'}</div>
    <div class="trace">${rows || '<div class="card-body muted">Spans appear as each step completes.</div>'}</div>
    <div class="legend">${["run", "node", "agent", "llm", "tool", "guardrail"].map((k) => `<span><i class="bar ${k}"></i>${k === "guardrail" ? "guardrail" : k}</span>`).join("")}
      <span style="margin-left:auto">Trace id = run id · OpenTelemetry GenAI attributes · click a row for details</span></div></div>`;
}

function renderDetail() {
  const row = qrow(S.sel);
  if (!row) return renderDetailEmpty();
  const v = S.view, st = v && v.state, run = v && v.run;
  const running = run && run.status === "running";
  const status = (v && v.erp && v.erp.status) || row.status;
  const canRun = row.status === "exception" && !running && !(run && run.status === "interrupted");
  const anthropic = S.health.anthropic_key;
  const prov = S.prov || (S.health.provider === "anthropic" ? "anthropic" : "sim");
  const { s } = nodeStates(v);
  const doneCount = STEPS.filter((x) => s[x.node] === "done" || s[x.node] === "skipped").length;
  const activeStep = STEPS.find((x) => s[x.node] === "active");
  const cur = row.currency;

  let runbar = "";
  if (canRun) {
    runbar = `<div class="runbar"><div class="avatar" style="--h:245;border-radius:11px">${icon("sparkle")}</div>
      <div class="rb-text"><div class="rb-title">Ready to resolve</div><div class="rb-sub">The agents investigate and propose; deterministic controls decide whether anything moves money.</div></div>
      <select class="select" id="prov" aria-label="Model">${PROVIDERS.map(([k, label]) => `<option value="${k}" ${k === prov ? "selected" : ""} ${k === "anthropic" && !anthropic ? "disabled" : ""}>${esc(label)}${k === "anthropic" && !anthropic ? " (no API key)" : ""}</option>`).join("")}</select>
      <button class="btn primary" id="runbtn">${icon("play")}Resolve with agent</button></div>`;
  } else if (running) {
    runbar = `<div class="runbar"><span class="spin" style="color:var(--accent)"></span>
      <div class="rb-text"><div class="rb-title">Resolving${activeStep ? `: ${esc(activeStep.title.toLowerCase())}` : "…"}</div>
        <div class="rb-sub">${run.provider && run.provider.includes("anthropic") ? "Running on Claude" : "Running on the offline reference model"} · step ${Math.min(doneCount + 1, STEPS.length)} of ${STEPS.length}</div>
        <div class="progress"><i style="width:${(doneCount / STEPS.length) * 100}%"></i></div></div></div>`;
  }

  const tabs = st ? [
    ["timeline", "Timeline", null],
    ["evidence", "Evidence", null],
    ["safeguards", "Safeguards", (() => { const n = guardItems(st, v).filter((g) => g[0] === "bad" || g[0] === "warn").length; return n ? [n, "warn"] : null; })()],
    ["email", "Vendor email", null],
    ["record", "ERP & audit", null],
    ["trace", "Trace", [S.trace.length, ""]],
  ] : [];
  let body;
  if (!run) {
    body = `<div class="section-head" style="margin-top:28px"><h2 class="section-title">What happens when you resolve</h2><span class="faint small">10 steps · about 3 seconds offline</span></div>${timelineHtml(null)}`;
  } else if (!st) {
    body = `<div class="card card-body" style="margin-top:20px"><div class="skeleton" style="height:16px;width:40%"></div><div class="skeleton" style="height:12px;width:70%;margin-top:12px"></div></div>`;
  } else {
    const tabBody = {
      timeline: () => timelineHtml(v), evidence: () => evidenceHtml(st), safeguards: () => `<div class="card card-body" style="padding-top:4px;padding-bottom:4px">${guardItems(st, v).map(([tone, ic, title, text]) => `<div class="guard"><div class="g-ico tint-${tone}">${icon(ic)}</div><div><div class="g-title">${esc(title)}</div><div class="g-text">${text}</div></div></div>`).join("")}</div>`,
      email: () => emailHtml(st), record: () => recordHtml(v, cur), trace: () => traceHtml(st),
    }[S.tab] || (() => timelineHtml(v));
    body = `${approvalHtml(v)}${outcomeHtml(st, run)}
      <div class="tabs" role="tablist">${tabs.map(([k, label, c]) => `<button data-tab="${k}" class="${S.tab === k ? "active" : ""}" role="tab">${esc(label)}${c ? `<span class="count ${c[1]}">${c[0]}</span>` : ""}</button>`).join("")}</div>
      <div class="tab-body">${tabBody()}</div>`;
  }

  $("#detail").innerHTML = `<div class="detail-inner">
    <div class="d-head">${avatar(row.vendor_name, true)}
      <div style="min-width:0"><div class="d-title">${esc(row.vendor_name)}</div>
        <div class="d-meta"><span>${icon("file", "sm")}${esc(row.invoice_number)}</span><span class="faint">${esc(row.invoice_id)}</span>
          <span>${row.po_id ? esc(row.po_id) : "No purchase order"}</span><span>Dated ${esc(row.invoice_date)}</span>
          ${pill(EXC[row.exception_code] || row.exception_code, row.security ? "bad" : "", false)}</div></div>
      <div class="d-amt"><div class="v">${esc(money(row.total, cur))}</div>${running ? pill("Resolving", "accent") : statusPill(status)}</div>
    </div>
    ${runbar}${body}</div>`;

  const btn = $("#runbtn");
  if (btn) btn.onclick = () => { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Starting'; startRun($("#prov").value); };
  const sel = $("#prov");
  if (sel) sel.onchange = (e) => { S.prov = e.target.value; };
  $$("[data-tab]", $("#detail")).forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; renderDetail(); }));
  $$("[data-decide]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    const runId = await decide(b.dataset.id, b.dataset.decide === "yes", ($("#appr-comment") || {}).value || "");
    if (runId) { S.runId = runId; setTimeout(refreshRun, 120); loadApprovalBadge(); }
  }));
}

// --------------------------------------------------------------- approvals
async function renderApprovals() {
  const el = $("#approvals");
  const items = await loadApprovalBadge();
  if (!S.queue.length) await loadQueue();
  const limit = S.config.policy ? money(S.config.policy.approval_limit) : "$25,000.00";
  el.innerHTML = `<p class="eyebrow">Human in the loop</p><h1 class="page-title">Approvals</h1>
    <p class="page-sub">Fully matched invoices above ${esc(limit)} pause here (AP-POL-8.1). Each run is checkpointed at the approval step:
      approving resumes it from that step and releases the payment; declining escalates it back to the controller.</p>
    <div class="stack" style="margin-top:28px">
    ${items.length ? items.map((a) => {
      const row = qrow(a.invoice_id) || {};
      return `<div class="card appr-card fade-in">
        <div><div style="display:flex;gap:12px;align-items:center">${avatar(row.vendor_name || a.invoice_id)}<div><div style="font-weight:600">${esc(row.vendor_name || a.invoice_id)}</div>
          <div class="faint small">${esc(row.invoice_number || "")} · ${esc(a.invoice_id)}</div></div></div>
          <div class="amt" style="margin-top:14px">${esc(money(a.amount, row.currency || "USD"))}</div>
          <div class="faint small">requested ${esc(ago(a.created_at))}</div></div>
        <div><div class="small muted" style="margin-bottom:10px">${esc(a.summary)}</div>
          <div style="display:flex;flex-wrap:wrap;gap:6px">${pill("Three-way match passed", "ok")}${pill("Bank details verified", "ok")}${pill("No duplicates", "ok")}${pill("Above release limit", "info")}</div>
          <a class="small" href="#/exceptions/${esc(a.invoice_id)}" style="display:inline-flex;gap:5px;align-items:center;margin-top:12px">Review the evidence ${icon("arrow", "sm")}</a></div>
        <div class="actions"><textarea placeholder="Note for the audit log (optional)" data-c="${esc(a.approval_id)}"></textarea>
          <div class="btns"><button class="btn danger" data-act="no" data-id="${esc(a.approval_id)}" data-inv="${esc(a.invoice_id)}">${icon("x")}Decline</button>
          <button class="btn primary" data-act="yes" data-id="${esc(a.approval_id)}" data-inv="${esc(a.invoice_id)}">${icon("check")}Approve</button></div></div>
      </div>`;
    }).join("") : `<div class="card"><div class="empty-state"><div class="es-ico">${icon("check", "lg")}</div><h3>Nothing waiting for approval</h3>
      <p>When the agent fully matches an invoice above the release limit, it pauses here for a controller.</p>
      <div style="margin-top:18px"><button class="btn primary" id="try-hitl">${icon("play")}Run an above-limit invoice</button></div></div></div>`}
    </div>`;
  $$("[data-act]", el).forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    const comment = ($(`textarea[data-c="${CSS.escape(b.dataset.id)}"]`, el) || {}).value || "";
    const runId = await decide(b.dataset.id, b.dataset.act === "yes", comment);
    if (!runId) { b.disabled = false; return; }
    await loadQueue();
    S.sel = null;
    go(`#/exceptions/${b.dataset.inv}`);
  }));
  const tryBtn = $("#try-hitl");
  if (tryBtn) tryBtn.onclick = async () => {
    const sc = await api("/api/scenarios");
    const pick = sc.find((x) => x.scenario === "over_approval_limit");
    if (!pick) return;
    if (pick.status === "exception") S.pendingRun = { invoice: pick.invoice_id, provider: "sim" };
    go(`#/exceptions/${pick.invoice_id}`);
  };
}

// ------------------------------------------------------------------- evals
const SUITES = {
  "claude-live": ["Claude, live", "Haiku, Sonnet and Opus 5.5 through the real API, one variant of each of the 13 scenarios."],
  "reference": ["Reference run", "The offline reference policy, three trials per case. Validates labels, graders and plumbing; must be 100%."],
  "chaos-controls-on": ["25% faults, controls on", "A quarter of model calls fail (429, 529, 5xx, timeouts) or misbehave (bad arguments, unknown tools, no tool call)."],
  "chaos-controls-off": ["25% faults, controls off", "The same faults with retries, fallback, validation repair, nudges and loop guard switched off."],
  "faulty-model": ["A faulty, gullible model", "Wrong first-pass decisions 35% of the time, many unsafe; obeys injected instructions."],
};
async function renderEvals() {
  const el = $("#evals");
  const reps = await api("/api/evals");
  const order = Object.keys(SUITES);
  const names = Object.keys(reps).filter((n) => n !== "adhoc").sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  if (!names.length) {
    el.innerHTML = `<p class="eyebrow">Quality</p><h1 class="page-title">Evaluations</h1><div class="card" style="margin-top:24px"><div class="empty-state"><div class="es-ico">${icon("activity", "lg")}</div>
      <h3>No reports yet</h3><p>Run <code>matchpoint suite</code> (offline, about a minute) to generate them.</p></div></div>`;
    return;
  }
  S.evalSel = S.evalSel && reps[S.evalSel] ? S.evalSel : names[0];
  const totalRuns = names.reduce((a, n) => a + reps[n].summary.runs, 0);
  const unsafe = names.reduce((a, n) => a + reps[n].summary.safety_violations, 0);
  const blocked = names.reduce((a, n) => a + (reps[n].summary.unsafe_proposals_blocked || 0), 0);
  el.innerHTML = `<p class="eyebrow">Quality</p><h1 class="page-title">Evaluations</h1>
    <p class="page-sub">52 labelled exception invoices, 13 scenarios including fraud and prompt-injection cases. Each run gets its own copy of the ERP,
      and graders read what the system actually wrote, not what the agent says it did. pass^k counts a case only if every trial passes.</p>
    <div class="banner" style="margin-top:28px;${unsafe ? "border-color:var(--bad-line);background:linear-gradient(120deg,var(--bad-soft),var(--bg-elev) 70%)" : ""}">
      <div class="b-ico" style="${unsafe ? "background:var(--bad)" : ""}">${icon(unsafe ? "alert" : "shieldCheck", "lg")}</div>
      <div><div class="b-title">${unsafe ? `${unsafe} unsafe actions executed` : "No unsafe action executed"} across ${totalRuns.toLocaleString()} runs</div>
      <div class="b-sub">A safety violation is money moved or a vendor contacted when policy forbids it. ${blocked} unsafe proposals were blocked by the policy engine before they reached the ERP.</div></div></div>
    <div class="grid ${names.length >= 5 ? "g5" : "g4"}" style="margin-top:16px">${names.map((n) => {
      const s = reps[n].summary, c = reps[n].config, [title, desc] = SUITES[n] || [n, `${c.provider}, ${s.trials} trial(s)`];
      const faults = Object.values(s.chaos_injected || {}).reduce((a, b) => a + b, 0);
      return `<div class="card suite ${n === S.evalSel ? "sel" : ""}" data-rep="${esc(n)}" tabindex="0">
        <div class="su-name">${esc(title)}</div><div class="su-desc">${esc(desc)}</div>
        <div class="su-big">${pct(s.pass_at_1)}<small>pass@1</small></div>
        <div class="su-rows"><div>pass^${s.trials}<b>${pct(s.pass_hat_k)}</b></div>
          <div>Safety violations<b style="color:${s.safety_violations ? "var(--bad)" : "var(--ok)"}">${s.safety_violations}</b></div>
          <div>Unsafe proposals blocked<b>${s.unsafe_proposals_blocked ?? 0}</b></div>
          <div>${faults ? "Faults injected" : c.provider === "anthropic" ? "Cost per run" : "Cost per run (est.)"}<b>${faults ? faults : `$${s.cost_usd_per_run.toFixed(3)}`}</b></div></div></div>`;
    }).join("")}</div>
    <div id="eval-detail"></div>`;
  $$("[data-rep]", el).forEach((card) => (card.onclick = () => { S.evalSel = card.dataset.rep; renderEvals(); }));
  const rep = await api(`/api/evals/${encodeURIComponent(S.evalSel)}`);
  const s = rep.summary, title = (SUITES[rep.name] || [rep.name])[0];
  const meter = (x) => `<div class="meter ${x >= 0.95 ? "" : x >= 0.8 ? "warn" : "bad"}"><i style="width:${(x * 100).toFixed(1)}%"></i></div>`;
  const bars = (obj, names) => Object.entries(obj).map(([k, x]) => `<div class="meter-row"><span>${esc(names[k] || k)}</span>${meter(x)}<span class="num small" style="text-align:right">${pct(x, 0)}</span></div>`).join("");
  const GRADER = { completed: "Run completed", action: "Right action", amount: "Right amount", erp_state: "ERP end state", safety: "Safety", hitl: "Approval when required", flags: "Risk flags", citations: "Policy cited", email: "Vendor email" };
  const COMP = { diagnosis: "Investigator diagnosis", retrieval_recall: "Policy retrieval", first_pass_action: "Resolver, first pass", model_flags: "Flags raised by the model" };
  const scen = Object.entries(rep.scenarios).map(([n, x]) => `<tr><td>${esc(n.replaceAll("_", " "))}</td><td class="num">${x.runs}</td><td style="width:40%">${meter(x.pass_rate)}</td><td class="num">${pct(x.pass_rate, 0)}</td><td class="num">${x.safety_violations}</td></tr>`).join("");
  const fails = (rep.failures || []).slice(0, 12).map((f) => `<div class="guard"><div class="g-ico tint-warn">${icon("alert")}</div><div><div class="g-title"><code>${esc(f.case_id)}</code> · trial ${f.trial + 1}</div>
    <div class="g-text">${esc(f.failed.join("; ").slice(0, 240))}</div></div></div>`).join("");
  $("#eval-detail").innerHTML = `
    <div class="section-head"><div><h2 class="section-title">${esc(title)}</h2><div class="muted small">${s.runs} runs · ${s.cases} cases × ${s.trials} trial(s) · p95 ${s.latency_p95_s}s per run · ${s.llm_calls_per_run} model calls per run</div></div></div>
    <div class="grid g2">
      <div class="card"><div class="card-head"><h3>Outcome graders</h3><span class="meta">all must pass</span></div><div class="card-body">${bars(s.outcome, GRADER)}</div></div>
      <div class="card"><div class="card-head"><h3>Where a failure starts</h3><span class="meta">component diagnostics</span></div><div class="card-body">${bars(s.components, COMP)}
        <div class="muted small" style="margin-top:12px">Flags count the model's own flags, before deterministic flags are merged.</div></div></div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h3>By scenario</h3></div><table class="table"><thead><tr><th>Scenario</th><th>Runs</th><th></th><th>Pass</th><th>Unsafe</th></tr></thead><tbody>${scen}</tbody></table></div>
      <div class="card"><div class="card-head"><h3>Failures</h3><span class="meta">${(rep.failures || []).length}</span></div>
        <div class="card-body" style="padding-top:4px;padding-bottom:4px">${fails || `<div class="muted" style="padding:14px 0">None. Every run passed every outcome grader.</div>`}</div></div>
    </div>`;
}

// ------------------------------------------------------------ how it works
function renderHow() {
  const c = S.config;
  const AGENTS = [
    ["triage", "compass", "accent", "Reads the invoice header and sets a risk level. Risk only goes up from here."],
    ["investigator", "search", "info", "Gathers evidence with read-only tools: vendor master, three-way match, duplicates, history, SQL."],
    ["policy_analyst", "book", "esc", "Retrieves the governing sections of the AP policy manual. Citations must come from retrieval."],
    ["resolver", "scale", "warn", "Proposes the resolution. Moves to the frontier model when the case is high risk."],
    ["reviewer", "eye", "ok", "An independent second opinion, after the deterministic policy engine has passed the proposal."],
    ["communicator", "mail", "bad", "Drafts the vendor email when the vendor needs to act. Checked by an output guard."],
  ];
  const SAFE = [
    ["shieldCheck", "Policy engine", "Every proposal is checked against facts recomputed from the ERP. Payments only for clean matches, at the exact total."],
    ["user", "Human approval", "Payments above the release limit pause the run until a controller signs off. The run is checkpointed while it waits."],
    ["db", "Exactly-once writes", "The executor is the only writer. Every side effect has an idempotency key; a crash and resume cannot pay twice."],
    ["scan", "Injection screening", "Vendor text is scanned (including hidden Unicode), wrapped as untrusted data, and given no authority."],
    ["key", "Data minimisation", "Bank numbers are masked before any model sees them; the SQL tool is read-only with a table allow-list."],
    ["alert", "Fail closed", "Any unrecoverable error escalates the invoice to the controller. Nothing is ever paid on an error path."],
  ];
  const tierModel = (role) => c.roles && c.models ? c.models[c.roles[role].tier] : "";
  $("#how").innerHTML = `<p class="eyebrow">Architecture</p><h1 class="page-title">Models propose. Code disposes.</h1>
    <p class="page-sub">Control flow is a typed state graph written in plain code. Six Claude agents work inside it where judgement is needed;
      everything that moves money or contacts a vendor passes deterministic checks first.</p>
    <div class="principle">${pill("Checkpoint after every step", "accent", false)}${pill("Typed hand-offs, not transcripts", "accent", false)}${pill("Retries, breakers, fallbacks", "accent", false)}${pill("Trace per run", "accent", false)}</div>
    <div class="flow3" style="margin-top:28px">
      <div class="card"><div class="n">1</div><h4>Investigate</h4><p>Agents gather evidence with read-only tools and submit typed findings. The exception code is treated as a hint, not a fact.</p></div>
      <div class="card"><div class="n">2</div><h4>Decide</h4><p>The policy manual is retrieved, a resolver proposes, the policy engine and a reviewer check. Rejected proposals go back with reasons.</p></div>
      <div class="card"><div class="n">3</div><h4>Guarantee</h4><p>Approval above the limit, an exactly-once write to the ERP, a guarded vendor email and a verified note in the vendor's history.</p></div>
    </div>
    <div class="section-head"><div><h2 class="section-title">The agents</h2><div class="muted small">Each one ends by submitting a validated artifact. Models are routed by role and risk.</div></div></div>
    <div class="grid g3">${AGENTS.map(([role, ic, tone, text]) => `<div class="card agent"><div class="a-top"><div class="a-ico tint-${tone}">${icon(ic)}</div>
      <div><h4 style="margin:0">${esc(ROLE[role])}</h4><div class="a-model">${esc(tierModel(role))}${role === "resolver" && c.models ? ` → ${esc(c.models[c.routing.high_risk_tier])}` : ""}</div></div></div><p>${esc(text)}</p></div>`).join("")}</div>
    <div class="section-head"><div><h2 class="section-title">The controls</h2><div class="muted small">Deterministic code that no model output can bypass.</div></div></div>
    <div class="grid g3">${SAFE.map(([ic, title, text]) => `<div class="card safe"><div class="s-ico">${icon(ic)}</div><div><h4>${esc(title)}</h4><p>${esc(text)}</p></div></div>`).join("")}</div>
    ${c.roles ? `<div class="section-head"><div><h2 class="section-title">Model routing</h2><div class="muted small">Fallback chain: ${esc(Object.entries(c.fallback_chain).map(([k, v]) => `${k} → ${v.join(", ")}`).join(" · "))}</div></div></div>
      <div class="card"><table class="table"><thead><tr><th>Role</th><th>Tier</th><th>Model</th><th>Effort</th><th>Max turns</th><th>Max tokens</th></tr></thead><tbody>
      ${Object.entries(c.roles).filter(([r]) => r !== "judge").map(([r, x]) => `<tr><td>${esc(ROLE[r] || r)}</td><td>${esc(x.tier)}</td><td><code>${esc(c.models[x.tier])}</code></td><td>${esc(x.effort)}</td><td class="num">${x.max_turns}</td><td class="num">${x.max_tokens.toLocaleString()}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
    <div class="card pad" style="margin-top:28px;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <div class="a-ico tint-accent" style="width:40px;height:40px;border-radius:12px;display:grid;place-items:center">${icon("git", "lg")}</div>
      <div style="flex:1;min-width:240px"><div style="font-weight:650">Source, tests and eval reports</div><div class="muted small">63 tests, an eval gate in CI, and design notes on the harness, security model and deployment.</div></div>
      <a class="btn" href="https://github.com/Safiullah-Rahu/matchpoint" target="_blank" rel="noopener">${icon("external")}View on GitHub</a></div>`;
}

// ------------------------------------------------------------- global ui
document.addEventListener("click", async (e) => {
  const sec = e.target.closest("[data-sec]");
  if (sec) {
    e.stopPropagation();
    try {
      const p = await api(`/api/policy/${encodeURIComponent(sec.dataset.sec)}`);
      openDrawer(`${p.section_id} · ${p.title}`, `<p class="faint small" style="margin-top:0">AP Policy Manual · ${esc(p.document)}</p><p>${esc(p.text)}</p>`);
    } catch (err) { toast(err.message, "alert"); }
    return;
  }
  const sp = e.target.closest("[data-span]");
  if (sp) {
    const s = S.trace[Number(sp.dataset.span)];
    if (s) openDrawer(s.name, `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">${pill(s.kind, "accent", false)}${pill(dur(s.duration_ms), "", false)}${s.status === "error" ? pill("error", "bad") : ""}</div>
      <pre>${esc(JSON.stringify({ attributes: s.attributes, events: s.events, error: s.error }, null, 2))}</pre>`);
  }
});
$("#scrim").onclick = closeDrawer;
$("#drawer-close").innerHTML = icon("x");
$("#drawer-close").onclick = closeDrawer;

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("mp-theme", t); } catch { /* storage unavailable */ }
  $("#theme").innerHTML = icon(t === "dark" ? "sun" : "moon");
}
$("#theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
$("#reset").innerHTML = icon("reset");
$("#reset").onclick = async () => {
  if (!confirm("Reset the demo? This re-seeds the synthetic ERP and clears all runs, approvals and traces.")) return;
  await api("/api/reset", { method: "POST" });
  stopPoll();
  Object.assign(S, { sel: null, runId: null, view: null, trace: [], queue: [] });
  await loadQueue(); loadApprovalBadge();
  toast("Demo data reset. 52 invoices are back in the queue.", "reset");
  route();
};

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (e.key === "Escape") { closeDrawer(); if (typing) document.activeElement.blur(); return; }
  if (typing) return;
  if (e.key === "/") { e.preventDefault(); if (S.page !== "exceptions") go("#/exceptions"); setTimeout(() => $("#q").focus(), 0); }
  if ((e.key === "j" || e.key === "k") && S.page === "exceptions" && S.visible && S.visible.length) {
    const i = S.visible.indexOf(S.sel);
    const next = S.visible[Math.max(0, Math.min(S.visible.length - 1, i + (e.key === "j" ? 1 : -1)))];
    if (next && next !== S.sel) go(`#/exceptions/${next}`);
  }
});

// ------------------------------------------------------------------- boot
(async function boot() {
  setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  const params = new URLSearchParams(location.search);  // legacy deep links: ?invoice=… / ?tab=…
  if (params.get("invoice")) history.replaceState(null, "", `/#/exceptions/${params.get("invoice")}`);
  else if (params.get("tab")) history.replaceState(null, "", `/#/${params.get("tab") === "arch" ? "how" : params.get("tab")}`);
  if (params.get("eval")) S.evalSel = params.get("eval");
  if (params.get("theme")) setTheme(params.get("theme"));
  try {
    const [health, config, policy] = await Promise.all([api("/api/health"), api("/api/config"), api("/api/policy")]);
    S.health = health; S.config = config;
    S.policy = Object.fromEntries(policy.map((p) => [p.section_id, p.title]));
    const live = health.provider === "anthropic";
    $("#env").classList.toggle("live", live || health.anthropic_key);
    $("#env-text").textContent = live ? "Claude · live" : health.anthropic_key ? "Demo data · Claude available" : "Demo data · offline";
    loadApprovalBadge();
  } catch (e) { toast("The API is unavailable: " + e.message, "alert"); }
  window.addEventListener("hashchange", route);
  route();
})();
