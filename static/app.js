/* app.js — Federal Funds Matchmaker ranked-list SPA */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────
const S = {
  accountId: "ncdot",
  opportunities: [],   // all items from /api/opportunities
  decisions: {},       // {opp_id: {decision, note}}
  selected: null,      // opp id
  filters: { confidence: "all", status: "all", days: "", search: "" },
  sort: "score",
  detailTab: "summary",
};

// ── DOM helpers ────────────────────────────────────────────────────────────
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = (d) => d ? new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : null;

function showToast(msg, ms = 2500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.add("hidden"), ms);
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadOpportunities() {
  try {
    const res = await fetch(`/api/opportunities?account_id=${S.accountId}`);
    const data = await res.json();
    S.opportunities = data.items ?? [];
    updateRefreshStatus(data.generated_at, data.mode);
    return data;
  } catch (e) {
    console.error("Failed to load opportunities", e);
    S.opportunities = [];
    return {};
  }
}

async function loadDecisions() {
  try {
    const res = await fetch(`/api/decisions?account_id=${S.accountId}`);
    S.decisions = await res.json();
  } catch (e) {
    S.decisions = {};
  }
}

function updateRefreshStatus(generatedAt, mode) {
  const label = $("#refresh-label");
  if (!generatedAt) {
    label.textContent = "No data loaded yet";
    return;
  }
  const d = new Date(generatedAt);
  const ageH = Math.round((Date.now() - d) / 3600000);
  const stale = ageH > 36;
  label.innerHTML = `Last refreshed: ${d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` +
    (stale ? ' <span style="color:var(--coral)">⚠ Stale</span>' : "") +
    (ageH > 0 ? ` (+${ageH}h)` : "");
  // Mode banner
  const banner = $("#mode-banner");
  if (mode === "keyword_fallback") {
    banner.textContent = "⚠ AI embeddings unavailable — scores are keyword-only. Results may be less precise.";
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

// ── Filtering + sorting ────────────────────────────────────────────────────
function applyFilters() {
  let items = [...S.opportunities];
  const { confidence, status, days, search } = S.filters;

  if (confidence !== "all") items = items.filter((o) => o.confidence === confidence);
  if (status !== "all") items = items.filter((o) => o.status === status);
  if (days) items = items.filter((o) => o.days_to_close != null && o.days_to_close <= +days);
  if (search) {
    const q = search.toLowerCase();
    items = items.filter((o) => (o.title + " " + o.funder).toLowerCase().includes(q));
  }

  // Sort
  if (S.sort === "score") {
    items.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  } else if (S.sort === "close_date") {
    items.sort((a, b) => {
      if (!a.close_date) return 1;
      if (!b.close_date) return -1;
      return a.close_date.localeCompare(b.close_date);
    });
  } else if (S.sort === "undecided") {
    items.sort((a, b) => {
      const aD = !!S.decisions[a.id], bD = !!S.decisions[b.id];
      if (aD !== bD) return aD ? 1 : -1;
      return (b.score ?? 0) - (a.score ?? 0);
    });
  }

  return items;
}

// ── Render list ────────────────────────────────────────────────────────────
function renderList() {
  const items = applyFilters();
  const list = $("#opp-list");
  const countEl = $("#list-count");
  countEl.textContent = `${items.length} opportunit${items.length === 1 ? "y" : "ies"}`;

  list.innerHTML = items.length
    ? items.map((opp, i) => oppRow(opp, i + 1)).join("")
    : `<li class="opp-item" style="cursor:default;color:var(--muted)">No results match your filters.</li>`;

  // Highlight selected
  if (S.selected) {
    const el = list.querySelector(`[data-id="${S.selected}"]`);
    if (el) el.classList.add("selected");
  }

  list.onclick = (e) => {
    const li = e.target.closest("[data-id]");
    if (!li) return;
    selectOpportunity(li.dataset.id);
  };
}

function confidenceBadge(c) {
  const cls = c === "High" ? "badge-high" : c === "Medium" ? "badge-medium" : "badge-low";
  return `<span class="badge ${cls}">${esc(c)}</span>`;
}

function dueLabel(opp) {
  if (opp.status === "forecasted") return `<span class="opp-due"><span class="badge badge-forecasted">Forecasted</span></span>`;
  if (!opp.close_date) return `<span class="opp-due no-date">No close date</span>`;
  const d = opp.days_to_close;
  const urgent = d != null && d <= 14;
  return `<span class="opp-due${urgent ? " urgent" : ""}">Due ${esc(fmtDate(opp.close_date))}${d != null ? ` (${d}d)` : ""}</span>`;
}

function decisionBadge(oppId) {
  const dec = S.decisions[oppId];
  if (!dec) return "";
  const cls = `decision-${dec.decision}`;
  const labels = { pursue: "✓ Pursue", maybe: "? Maybe", pass: "✗ Pass" };
  return `<span class="decision-badge ${cls}">${labels[dec.decision] ?? dec.decision}</span>`;
}

function oppRow(opp, rank) {
  return `<li class="opp-item" data-id="${esc(opp.id)}" role="option" aria-selected="false" tabindex="0">
    <span class="opp-rank">${rank}</span>
    <div class="opp-body">
      <div class="opp-title">${esc(opp.title)}</div>
      <div class="opp-meta">
        <span class="opp-score">${opp.score ?? "—"}</span>
        ${confidenceBadge(opp.confidence)}
        <span class="opp-effort">${esc(opp.effort?.level ?? "—")} effort</span>
        ${dueLabel(opp)}
        ${decisionBadge(opp.id)}
      </div>
    </div>
  </li>`;
}

// ── Select + render detail ─────────────────────────────────────────────────
function selectOpportunity(id) {
  S.selected = id;
  document.querySelectorAll(".opp-item").forEach((el) => {
    el.classList.toggle("selected", el.dataset.id === id);
    el.setAttribute("aria-selected", el.dataset.id === id);
  });
  const opp = S.opportunities.find((o) => o.id === id);
  if (opp) renderDetail(opp);
}

function renderDetail(opp) {
  $("#detail-empty").classList.add("hidden");
  const panel = $("#detail-content");
  panel.classList.remove("hidden");

  const dec = S.decisions[opp.id] ?? {};
  const bd = opp.breakdown ?? {};
  const ai = opp.ai ?? {};
  const effort = opp.effort ?? {};

  const daysStr = opp.close_date
    ? (opp.days_to_close != null ? `${opp.days_to_close} days (${fmtDate(opp.close_date)})` : fmtDate(opp.close_date))
    : opp.status === "forecasted" ? "Forecasted — no open date yet" : "No close date";
  const daysClass = (opp.days_to_close != null && opp.days_to_close <= 14) ? "urgent"
    : opp.status === "forecasted" ? "forecasted" : "";

  panel.innerHTML = `
    <!-- Header -->
    <div class="detail-header">
      <div class="detail-title">${esc(opp.title)}</div>
      <div class="detail-sub">
        <span class="detail-agency">${esc(opp.funder ?? opp.agency ?? "")}</span>
        ${opp.number ? `<span class="detail-agency">· ${esc(opp.number)}</span>` : ""}
        ${opp.url ? `<a class="detail-link" href="${esc(opp.url)}" target="_blank" rel="noopener">View on Grants.gov ↗</a>` : ""}
      </div>
      <div class="score-row">
        <div>
          <div class="score-label">Overall score</div>
          <div class="score-big">${opp.score ?? "—"}</div>
        </div>
        ${confidenceBadge(opp.confidence)}
      </div>
      <div class="closes-row">
        Closes in: <span class="${daysClass}">${esc(daysStr)}</span>
      </div>
      ${effort.level ? `<div class="effort-row">
        <span class="effort-label">Estimated effort</span><br>
        <strong>${esc(effort.level)}</strong>${effort.hours_range ? ` (${esc(effort.hours_range)} hrs)` : ""}
        ${effort.basis ? `— <em style="color:var(--muted)">${esc(effort.basis)}</em>` : ""}
        <span style="color:var(--faint);font-size:11px"> (estimate)</span>
      </div>` : ""}
    </div>

    <!-- Decision -->
    <div class="detail-card">
      <h3>Record your decision</h3>
      <div class="decision-row">
        <button class="btn-decide btn-pursue${dec.decision === 'pursue' ? ' chosen' : ''}" data-dec="pursue">✓ Pursue</button>
        <button class="btn-decide btn-maybe${dec.decision === 'maybe' ? ' chosen' : ''}" data-dec="maybe">? Maybe</button>
        <button class="btn-decide btn-pass${dec.decision === 'pass' ? ' chosen' : ''}" data-dec="pass">✗ Pass</button>
        <input class="decision-note" id="decision-note" type="text" placeholder="Optional note…" value="${esc(dec.note ?? "")}">
        <button class="btn-save-note" id="btn-save-note">Save</button>
      </div>
    </div>

    <!-- Why it matches + summary tabs -->
    <div class="detail-card">
      <div class="ai-disclaimer">
        <span>⚠ AI-generated · verify against</span>
        ${opp.url ? `<a href="${esc(opp.url)}" target="_blank" rel="noopener">the official listing</a>` : "the official listing"}
      </div>
      <div class="detail-tabs" id="detail-tabs">
        <button class="detail-tab${S.detailTab === 'summary' ? ' active' : ''}" data-pane="summary">Summary</button>
        <button class="detail-tab${S.detailTab === 'eligibility' ? ' active' : ''}" data-pane="eligibility">Eligibility</button>
        <button class="detail-tab${S.detailTab === 'risk' ? ' active' : ''}" data-pane="risk">Risk / Gap</button>
      </div>
      <div class="detail-tab-content">
        <div class="detail-tab-pane${S.detailTab === 'summary' ? ' active' : ''}" id="pane-summary">
          ${ai.why_it_fits ? `<p><strong>Why it fits:</strong> ${esc(ai.why_it_fits)}</p><br>` : ""}
          <p>${esc(ai.summary || (opp.description ? opp.description.slice(0, 300) + "…" : "No summary available."))}</p>
          ${ai.award_info ? `<p style="margin-top:8px"><strong>Award:</strong> ${esc(ai.award_info)}</p>` : ""}
          ${ai.deadline ? `<p style="margin-top:4px"><strong>Deadline note:</strong> ${esc(ai.deadline)}</p>` : ""}
          ${opp.matched_unit ? `<p style="margin-top:8px;font-size:12px;color:var(--muted)">Matched goal: <strong>${esc(opp.matched_unit.id)}</strong> — ${esc(opp.matched_unit.title)}</p>` : ""}
          ${ai.evidence_quote ? `<div class="evidence-quote">"${esc(ai.evidence_quote)}"</div>` : ""}
        </div>
        <div class="detail-tab-pane${S.detailTab === 'eligibility' ? ' active' : ''}" id="pane-eligibility">
          <p>${esc(ai.eligibility_notes || (opp.eligibility?.join(", ") || "Eligibility not stated."))}</p>
        </div>
        <div class="detail-tab-pane${S.detailTab === 'risk' ? ' active' : ''}" id="pane-risk">
          <p>${esc(ai.risk_or_gap || "No risk analysis available.")}</p>
        </div>
      </div>
    </div>

    <!-- Score breakdown -->
    <div class="detail-card">
      <h3>Score breakdown</h3>
      ${breakdownBar("Semantic", bd.semantic, 45)}
      ${breakdownBar("Keyword", bd.keyword, 25)}
      ${breakdownBar("Eligibility", bd.eligibility, 15)}
      ${breakdownBar("Timing", bd.timing, 10)}
      ${breakdownBar("Funder affinity", bd.funder_affinity, 5)}
      ${opp.matched_terms?.length ? `<p style="margin-top:10px;font-size:12px;color:var(--muted)">Matched terms: ${opp.matched_terms.map(esc).join(", ")}</p>` : ""}
    </div>

    <!-- Source text -->
    ${opp.description ? `<div class="detail-card">
      <details>
        <summary>Source text excerpt (from Grants.gov)</summary>
        <p class="source-text">${esc(opp.description.slice(0, 1000))}${opp.description.length > 1000 ? "…" : ""}</p>
      </details>
    </div>` : ""}
  `;

  // Tab switching
  $("#detail-tabs").onclick = (e) => {
    const btn = e.target.closest("[data-pane]");
    if (!btn) return;
    S.detailTab = btn.dataset.pane;
    panel.querySelectorAll(".detail-tab").forEach((b) => b.classList.toggle("active", b === btn));
    panel.querySelectorAll(".detail-tab-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + S.detailTab));
  };

  // Decision buttons
  panel.querySelectorAll(".btn-decide").forEach((btn) => {
    btn.onclick = () => saveDecision(opp.id, btn.dataset.dec, $("#decision-note").value);
  });
  $("#btn-save-note").onclick = () => {
    const dec = S.decisions[opp.id]?.decision;
    if (dec) saveDecision(opp.id, dec, $("#decision-note").value);
    else showToast("Choose Pursue, Maybe, or Pass first.");
  };
}

function breakdownBar(label, value, weight) {
  const v = value ?? 0;
  const pct = Math.round(v * 100);
  return `<div class="breakdown-row">
    <span class="breakdown-label">${esc(label)}<br><span style="font-size:10px;color:var(--faint)">${weight}%</span></span>
    <div class="breakdown-bar-wrap"><div class="breakdown-bar" style="width:${pct}%"></div></div>
    <span class="breakdown-val">${pct}</span>
  </div>`;
}

// ── Save decision ──────────────────────────────────────────────────────────
async function saveDecision(oppId, decision, note) {
  try {
    const res = await fetch(`/api/decisions/${oppId}?account_id=${S.accountId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note, user_id: "demo" }),
    });
    if (!res.ok) throw new Error(await res.text());
    S.decisions[oppId] = { decision, note };
    showToast(`Saved: ${decision}`);
    renderList();
    // Re-render detail to update button states
    const opp = S.opportunities.find((o) => o.id === oppId);
    if (opp) renderDetail(opp);
    // Auto-advance to next undecided
    autoAdvance(oppId);
  } catch (e) {
    showToast("Could not save decision. Try again.");
    console.error(e);
  }
}

function autoAdvance(justDecidedId) {
  const items = applyFilters();
  const idx = items.findIndex((o) => o.id === justDecidedId);
  const next = items.slice(idx + 1).find((o) => !S.decisions[o.id]);
  if (next) selectOpportunity(next.id);
}

// ── Account menu ───────────────────────────────────────────────────────────
function initAccountMenu() {
  const btn = $("#account-btn");
  const dd = $("#account-dropdown");
  btn.onclick = (e) => {
    e.stopPropagation();
    dd.classList.toggle("open");
    btn.setAttribute("aria-expanded", dd.classList.contains("open"));
  };
  document.addEventListener("click", () => { dd.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); });

  fetch("/api/accounts").then((r) => r.json()).then((data) => {
    const accounts = data.accounts ?? [];
    const list = $("#account-list");
    list.innerHTML = accounts.map((a) =>
      `<button class="account-item${a.account_id === S.accountId ? " active" : ""}" data-id="${esc(a.account_id)}">${esc(a.display_name)}</button>`
    ).join("");
    list.onclick = (e) => {
      const btn2 = e.target.closest("[data-id]");
      if (!btn2) return;
      S.accountId = btn2.dataset.id;
      $("#account-name").textContent = btn2.textContent;
      dd.classList.remove("open");
      init();
    };
    // Set account name
    const cur = accounts.find((a) => a.account_id === S.accountId);
    if (cur) $("#account-name").textContent = cur.display_name;
  }).catch(() => {});
}

// ── Filters ────────────────────────────────────────────────────────────────
function initFilters() {
  $("#f-confidence").onchange = (e) => { S.filters.confidence = e.target.value; renderList(); };
  $("#f-status").onchange = (e) => { S.filters.status = e.target.value; renderList(); };
  $("#f-days").oninput = (e) => { S.filters.days = e.target.value; renderList(); };
  $("#f-search").oninput = (e) => { S.filters.search = e.target.value; renderList(); };
  $("#sort-by").onchange = (e) => { S.sort = e.target.value; renderList(); };
  $("#export-link").href = `/api/export?account_id=${S.accountId}`;
}

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  S.selected = null;
  S.detailTab = "summary";
  $("#detail-empty").classList.remove("hidden");
  $("#detail-content").classList.add("hidden");
  $("#opp-list").innerHTML = `<li class="opp-item" style="cursor:default;color:var(--muted)">Loading…</li>`;

  await Promise.all([loadOpportunities(), loadDecisions()]);
  renderList();

  // Auto-select rank #1
  const first = applyFilters()[0];
  if (first) selectOpportunity(first.id);
}

initAccountMenu();
initFilters();
init();
