/* app.js — Federal Funds Matchmaker ranked-list SPA */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────
const S = {
  accountId: "ncdot",
  opportunities: [],   // all items from /api/opportunities
  decisions: {},       // {opp_id: {decision, note}}
  selected: null,      // opp id
  filters: { confidence: "all", status: "all", days: "", eligibility: "all", search: "" },
  sort: "score",
  detailTab: "summary",
  profile: {},         // unit_id → {title, keywords[]} from /api/profile
  mainTab: "grants",   // "grants" | "project"
  pmResult: null,      // last project-match result
  compareSet: new Set(),  // up to 2 grant IDs for side-by-side
  pmLoading: false,
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
    S.mode = data.mode;
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

async function loadProfile() {
  try {
    const res = await fetch(`/api/profile?account_id=${S.accountId}`);
    const data = await res.json();
    S.profile = {};
    for (const u of (data.units ?? [])) {
      S.profile[u.id] = { title: u.title, keywords: u.keywords ?? [] };
    }
  } catch (e) {
    S.profile = {};
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
  if (S.filters.eligibility !== "all") {
    const minElig = +S.filters.eligibility;
    items = items.filter((o) => {
      const e = o.score_breakdown?.eligibility ?? o.eligibility_score;
      return e != null && e >= minElig;
    });
  }
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

  // Restore compare checkbox state
  S.compareSet.forEach((id) => {
    const cb = list.querySelector(`.compare-cb[data-id="${id}"]`);
    if (cb) cb.checked = true;
  });

  list.onclick = (e) => {
    if (e.target.closest(".compare-cb-wrap")) return;
    const li = e.target.closest("[data-id]");
    if (!li) return;
    selectOpportunity(li.dataset.id);
  };

  list.onchange = (e) => {
    const cb = e.target.closest(".compare-cb");
    if (!cb) return;
    const id = cb.dataset.id;
    if (cb.checked) {
      if (S.compareSet.size >= 10) {
        cb.checked = false;
        showToast("Select at most 10 grants to compare — uncheck one first");
        return;
      }
      S.compareSet.add(id);
    } else {
      S.compareSet.delete(id);
    }
    updateCompareBtn();
  };

  // Sample data banner
  const hasSample = items.some((o) => o.synthetic);
  const banner = $("#sample-banner");
  if (banner) banner.classList.toggle("hidden", !hasSample);
}

function confidenceBadge(c) {
  const cls = c === "High" ? "badge-high" : c === "Medium" ? "badge-medium" : "badge-low";
  const tips = {
    High: "High confidence: full synopsis text available; semantic and keyword signals are reliable.",
    Medium: "Medium confidence: partial text; score is a reasonable estimate.",
    Low: "Low confidence: minimal source text; treat score as a rough indicator only.",
  };
  const tip = tips[c] || "Confidence reflects how much source text was available to score this opportunity.";
  return `<span class="badge ${cls}" title="${esc(tip)}">${esc(c)} confidence</span>`;
}

function eligibilityChip(opp) {
  const v = opp.eligibility_verdict;
  if (!v || v === "Unclear") return `<span class="badge badge-unknown" title="Eligibility not stated in source text — check the official listing">Eligibility unknown</span>`;
  if (v === "Eligible") return `<span class="badge badge-eligible" title="${esc(opp.eligibility_reason || '')}">Eligible</span>`;
  if (v === "Not eligible") return `<span class="badge badge-ineligible" title="${esc(opp.eligibility_reason || '')}">Not eligible</span>`;
  return "";
}

function effortChip(opp) {
  const level = opp.effort?.level;
  if (!level || level === "not stated") return "";
  const cls = level === "High" ? "badge-effort-high" : level === "Medium" ? "badge-effort-medium" : "badge-effort-low";
  const hours = opp.effort?.hours_range;
  return `<span class="badge ${cls}" title="${esc(opp.effort?.basis || '')}">${esc(level)}${hours ? " (" + esc(hours) + "h)" : ""} effort</span>`;
}

function syntheticBadge(opp) {
  if (!opp.synthetic) return "";
  return `<span class="badge badge-sample">SAMPLE DATA</span>`;
}

function fmtDollars(n) {
  if (!n || isNaN(n)) return null;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(n % 1e6 === 0 ? 0 : 1)}M`;
  if (n >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return `$${Number(n).toLocaleString()}`;
}

function awardRange(opp) {
  const ceil = opp.award_ceiling ?? opp.max_award_usd;
  const floor = opp.award_floor;
  if (!ceil && !floor) return "";
  const lo = fmtDollars(floor);
  const hi = fmtDollars(ceil);
  const label = (lo && hi && lo !== hi) ? `${lo} – ${hi}` : `Up to ${hi || lo}`;
  return `<span class="opp-award" title="Award range">💰 ${label}</span>`;
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
    <label class="compare-cb-wrap" title="Add to compare" onclick="event.stopPropagation()"><input type="checkbox" class="compare-cb" data-id="${esc(opp.id)}"></label>
    <span class="opp-rank">${rank}</span>
    <div class="score-ring-wrap">${scoreRing(opp.score, 52)}</div>
    <div class="opp-body">
      <div class="opp-title">${esc(opp.title)}${syntheticBadge(opp)}</div>
      <div class="opp-funder">${esc(opp.funder || "")}</div>
      <div class="opp-meta">
        ${confidenceBadge(opp.confidence)}
        ${effortChip(opp)}
        ${eligibilityChip(opp)}
        ${awardRange(opp)}
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
  if (opp) {
    renderDetail(opp);
    if (history.replaceState) history.replaceState(null, "", "#grant/" + encodeURIComponent(id));
  }
}


// ── Score ring (circular gauge) ────────────────────────────────────────────
function scoreRing(score, size = 80) {
  const pct = Math.min(Math.max(score ?? 0, 0), 100) / 100;
  const r = 34, cx = 44, cy = 44, strokeW = 8;
  const circ = 2 * Math.PI * r;
  const dash = pct * circ;
  // Gradient id unique to this render
  const gid = "sg" + Math.random().toString(36).slice(2, 7);
  return `<svg width="${size}" height="${size}" viewBox="0 0 88 88" style="display:block" aria-hidden="true">
    <defs>
      <linearGradient id="${gid}" x1="0%" y1="100%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#e07040"/>
        <stop offset="100%" stop-color="#e8b040"/>
      </linearGradient>
    </defs>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#e8e4da" stroke-width="${strokeW}"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
      stroke="url(#${gid})" stroke-width="${strokeW}"
      stroke-linecap="round"
      stroke-dasharray="${dash.toFixed(2)} ${(circ - dash).toFixed(2)}"
      transform="rotate(-90 ${cx} ${cy})"/>
    <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central"
      style="font-size:20px;font-weight:700;fill:var(--ink);font-family:inherit">
      ${score ?? "—"}
    </text>
  </svg>`;
}

// ── Application materials (rule-based) ────────────────────────────────────
function appMaterials(opp) {
  const tags = ["SF-424 Form", "Project Narrative", "Budget & Justification"];
  if (opp.cost_share) tags.push("Cost-Share Documentation");
  const effort = opp.effort?.level;
  if (effort === "High") { tags.push("Logic Model", "Letters of Support", "Org Chart"); }
  else if (effort === "Medium") { tags.push("Letters of Support"); }
  const desc = (opp.description || opp.synopsis || "").toLowerCase();
  if (desc.includes("nepa") || desc.includes("environmental")) tags.push("Environmental Review");
  if (desc.includes("audit") || desc.includes("financial statement")) tags.push("Financial Statements");
  if (opp.max_award_usd > 500000) tags.push("Detailed Work Plan");
  tags.push("SAM.gov Registration");
  return tags;
}

function renderDetail(opp) {
  $("#detail-empty").classList.add("hidden");
  const panel = $("#detail-content");
  panel.classList.remove("hidden");

  const dec = S.decisions[opp.id] ?? {};
  const bd = opp.score_breakdown ?? opp.breakdown ?? {};
  const ai = opp.ai ?? {};
  const effort = opp.effort ?? {};

  const daysStr = opp.close_date
    ? (opp.days_to_close != null ? `${opp.days_to_close} days (${fmtDate(opp.close_date)})` : fmtDate(opp.close_date))
    : opp.status === "forecasted" ? "Forecasted — no open date yet" : "No close date";
  const daysClass = (opp.days_to_close != null && opp.days_to_close <= 14) ? "urgent"
    : opp.status === "forecasted" ? "forecasted" : "";

  const materials = appMaterials(opp);

  // Build specific relevance summary from matched strategic plan units
  let relevanceSummary;
  const matchedUnits = opp.matched_units ?? [];
  const unitMap = S.profile ?? {};
  if (matchedUnits.length && Object.keys(unitMap).length) {
    // Collect keywords from each matched unit
    const allKws = [];
    const unitTitles = [];
    for (const uid of matchedUnits) {
      const u = unitMap[uid];
      if (!u) continue;
      unitTitles.push(u.title);
      allKws.push(...u.keywords.slice(0, 4));
    }
    if (allKws.length) {
      const kwList = [...new Set(allKws)].slice(0, 8).join(", ");
      const goalStr = unitTitles.length === 1
        ? unitTitles[0]
        : unitTitles.slice(0, 2).join(" and ");
      relevanceSummary = `This grant mentions themes like ${kwList}, which align with NCDOT's strategic goal: ${goalStr}.`;
    } else {
      relevanceSummary = "Keyword signals indicate alignment with NCDOT transportation priorities.";
    }
  } else if (matchedUnits.length) {
    relevanceSummary = `Matched NCDOT goal areas: ${matchedUnits.join(", ")}. Keyword signals indicate alignment with NCDOT transportation priorities.`;
  } else {
    relevanceSummary = opp.synopsis || opp.description
      ? (opp.synopsis || opp.description).slice(0, 280) + ((opp.synopsis || opp.description).length > 280 ? "…" : "")
      : "Review the official listing to confirm relevance to NCDOT priorities.";
  }
  const eligSummary = (opp.eligibility_verdict && opp.eligibility_verdict !== "Unclear")
    ? opp.eligibility_reason
    : opp.eligibility_reason && !opp.eligibility_reason.startsWith("No eligibility")
      ? opp.eligibility_reason
      : "Eligibility could not be determined from source text. State agencies are commonly eligible for federal grants in this category — verify applicant types in the official listing.";

  panel.innerHTML = `
    <!-- Profile card header -->
    <div class="detail-header">
      <div class="detail-profile-top">
        <div class="detail-ring">${scoreRing(opp.score, 88)}</div>
        <div class="detail-profile-info">
          <div class="detail-title">${esc(opp.title)}</div>
          <div class="detail-agency-row">
            <span class="detail-agency-icon">🏛</span>
            <span class="detail-agency">${esc(opp.funder ?? opp.agency ?? "Federal Agency")}</span>
          </div>
          <div class="detail-agency-row">
            <span class="detail-agency-icon">📅</span>
            <span class="closes-inline ${daysClass}">${esc(daysStr)}</span>
          </div>
          ${(() => { const ar = awardRange(opp); return ar ? `<div class="detail-agency-row">${ar}</div>` : (opp.max_award_usd > 0 ? `<div class="detail-agency-row"><span class="detail-agency-icon">💰</span><span>Up to $${(opp.max_award_usd/1e6).toFixed(1)}M</span></div>` : ""); })()}
          ${opp.url ? `<a class="detail-link" href="${esc(opp.url)}" target="_blank" rel="noopener">View on Grants.gov ↗</a>` : ""}
        </div>
      </div>
      <div class="detail-badges-row">
        ${confidenceBadge(opp.confidence)}
        ${effort.level && effort.level !== "not stated" ? `<span class="badge badge-effort-${effort.level.toLowerCase()}">${esc(effort.level)} effort</span>` : ""}
      </div>

      <div class="detail-section">
        <div class="detail-section-label">Why it's relevant</div>
        <p class="detail-section-text">${esc(relevanceSummary)}</p>
      </div>

      <div class="detail-section">
        <div class="detail-section-label">AI Fit Assessment</div>
        <div id="detail-fit-ai" class="detail-section-text" style="color:var(--muted);font-size:12px">⏳ Assessing fit for ${esc(S.profile[Object.keys(S.profile)[0]]?.title || "your org")}…</div>
      </div>

      <div class="detail-section" id="detail-eligibility-section">
        <div class="detail-section-label">Eligibility</div>
        ${opp.eligible_applicants ? `<p class="detail-section-text" style="margin-bottom:6px"><strong>Eligible applicants:</strong> ${esc(opp.eligible_applicants)}</p>` : ""}
        <div id="detail-eligibility-ai" class="detail-section-text" style="color:var(--muted);font-size:12px">⏳ Loading AI analysis…</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-label">Materials to apply</div>
        <div class="materials-tags">
          ${materials.map(m => `<span class="material-tag">${esc(m)}</span>`).join("")}
        </div>
      </div>
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
          <div class="elig-verdict-row">
            ${eligibilityChip(opp)}
            ${opp.eligibility_reason ? `<span class="elig-reason">${esc(opp.eligibility_reason)}</span>` : ""}
          </div>
          ${opp.applicant_types?.length ? `<p class="elig-types"><strong>Listed types:</strong> ${opp.applicant_types.map(esc).join(", ")}</p>` : ""}
          ${opp.eligibility_note ? `<details class="elig-raw"><summary>Source text</summary><p>${esc(opp.eligibility_note)}</p></details>` : ""}
          ${!opp.eligibility_verdict ? `<p style="color:var(--muted)">Eligibility not stated — check the official listing.</p>` : ""}
          <p style="font-size:11px;color:var(--faint);margin-top:8px">Eligibility is determined by rule — always verify against the listing.</p>
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

  // Async: load AI eligibility analysis and populate the placeholder
  (async () => {
    const aiEl = document.getElementById("detail-eligibility-ai");
    if (!aiEl) return;
    // Raw SAM.gov text as fallback source
    const rawElig = opp.eligibility_note || opp.eligible_applicants || opp.eligibility_reason || "";
    try {
      const r = await fetch(`/api/analyze?opp_id=${encodeURIComponent(opp.id)}&account_id=${encodeURIComponent(S.accountId)}`);
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      const reqs = data.eligibility_requirements ?? [];
      // Treat "Not stated in source text" placeholder as a miss — fall back to raw SAM.gov text
      const isPlaceholder = reqs.length === 1 && reqs[0].toLowerCase().includes("not stated");
      if (reqs.length && !isPlaceholder) {
        aiEl.innerHTML = `<ul class="elig-req-list">${reqs.map(req => `<li>${esc(req)}</li>`).join("")}</ul>`;
      } else if (rawElig) {
        aiEl.innerHTML = `<p style="font-size:13px;margin:0">${esc(rawElig)}</p>`;
      } else {
        aiEl.textContent = "No eligibility restrictions stated — verify at the official listing.";
      }
    } catch (_) {
      // Network/server error — show raw SAM.gov text if available
      if (rawElig) {
        aiEl.innerHTML = `<p style="font-size:13px;margin:0">${esc(rawElig)}</p>`;
      } else {
        aiEl.textContent = "Could not load eligibility data — check the official listing.";
      }
    }
  })();

  // Async: load AI fit assessment
  (async () => {
    const fitEl = document.getElementById("detail-fit-ai");
    if (!fitEl) return;
    try {
      const r = await fetch(`/api/fit?opp_id=${encodeURIComponent(opp.id)}&account_id=${encodeURIComponent(S.accountId)}`);
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      const rec = (data.recommendation ?? "maybe").toLowerCase();
      const recMeta = {
        pursue: { label: "✓ Pursue", bg: "#2d7a4f", text: "#fff" },
        maybe:  { label: "? Review",  bg: "#c07c00", text: "#fff" },
        pass:   { label: "✗ Skip",   bg: "#b03030", text: "#fff" },
      };
      const m = recMeta[rec] || recMeta.maybe;
      fitEl.innerHTML =
        `<span class="fit-rec-badge" style="background:${m.bg};color:${m.text}">${m.label}</span>` +
        (data.why_relevant ? `<p class="fit-why">${esc(data.why_relevant)}</p>` : "") +
        (data.fit_notes    ? `<p class="fit-notes">${esc(data.fit_notes)}</p>`    : "");
    } catch (_) {
      // Fall back to the rule-based relevance summary already on screen
      fitEl.textContent = "";
    }
  })();

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
  const pct = Math.round(v);
  const isNA = label === "Semantic" && S.mode === "keyword_fallback";
  return `<div class="breakdown-row">
    <span class="breakdown-label">${esc(label)}<br><span style="font-size:10px;color:var(--faint)">${weight}%</span></span>
    <div class="breakdown-bar-wrap">${isNA ? '<span style="font-size:11px;color:var(--faint);line-height:12px">N/A — AI unavailable</span>' : `<div class="breakdown-bar" style="width:${pct}%"></div>`}</div>
    <span class="breakdown-val">${isNA ? "—" : pct}</span>
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
function loadAccountList(switchToId) {
  const dd = $("#account-dropdown");
  const list = $("#account-list");
  return fetch("/api/accounts").then((r) => r.json()).then((data) => {
    const accounts = data.accounts ?? [];
    list.innerHTML = accounts.map((a) =>
      `<button class="account-item${a.account_id === S.accountId ? " active" : ""}" data-id="${esc(a.account_id)}">${esc(a.display_name)}</button>`
    ).join("");
    list.onclick = (e) => {
      const btn2 = e.target.closest("[data-id]");
      if (!btn2) return;
      S.accountId = btn2.dataset.id;
      $("#account-name").textContent = btn2.textContent;
      dd.classList.remove("open");
      $("#account-btn").setAttribute("aria-expanded", "false");
      init();
    };
    if (switchToId) {
      const match = accounts.find((a) => a.account_id === switchToId);
      if (match) { S.accountId = match.account_id; $("#account-name").textContent = match.display_name; }
    } else {
      const cur = accounts.find((a) => a.account_id === S.accountId);
      if (cur) $("#account-name").textContent = cur.display_name;
    }
    return accounts;
  }).catch(() => []);
}

function initAccountMenu() {
  const btn = $("#account-btn");
  const dd = $("#account-dropdown");
  btn.onclick = (e) => {
    e.stopPropagation();
    dd.classList.toggle("open");
    btn.setAttribute("aria-expanded", dd.classList.contains("open"));
  };
  document.addEventListener("click", () => { dd.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); });
  loadAccountList();
}

// ── Filters ────────────────────────────────────────────────────────────────
function initFilters() {
  $("#f-confidence").onchange = (e) => { S.filters.confidence = e.target.value; renderList(); };
  $("#f-status").onchange = (e) => { S.filters.status = e.target.value; renderList(); };
  $("#f-days").oninput = (e) => { S.filters.days = e.target.value; renderList(); };
  const fElig = document.getElementById("f-eligibility");
  if (fElig) fElig.onchange = (e) => { S.filters.eligibility = e.target.value; renderList(); };
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

  await Promise.all([loadOpportunities(), loadDecisions(), loadProfile()]);
  renderList();

  // Deep-link: open grant from URL hash
  const hashMatch = window.location.hash.match(/^#grant\/(.+)$/);
  if (hashMatch) {
    const targetId = decodeURIComponent(hashMatch[1]);
    const target = S.opportunities.find((o) => o.id === targetId);
    if (target) {
      const el = document.querySelector(`[data-id="${targetId}"]`);
      if (el) el.scrollIntoView({ block: "nearest" });
      selectOpportunity(targetId);
      return;
    }
  }
  // Auto-select rank #1
  const first = applyFilters()[0];
  if (first) selectOpportunity(first.id);
}


// ── How-scores modal ──────────────────────────────────────────────────────
function initHowModal() {
  const modal = $("#how-modal");
  const openBtn = $("#how-btn");
  const closeBtn = $("#how-modal-close");
  const footerLink = $("#footer-how-link");

  function openModal() {
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    closeBtn.focus();
  }
  function closeModal() {
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    openBtn.focus();
  }

  if (openBtn) openBtn.addEventListener("click", openModal);
  if (footerLink) footerLink.addEventListener("click", (e) => { e.preventDefault(); openModal(); });
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (modal) modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal(); });
}


// ── Create account modal ───────────────────────────────────────────────────
function initCreateAccountModal() {
  const modal = $("#create-acct-modal");
  const form = $("#create-acct-form");
  const errEl = $("#create-acct-error");
  const submitBtn = $("#create-acct-submit");

  function openModal() {
    form.reset();
    errEl.textContent = "";
    errEl.classList.add("hidden");
    submitBtn.disabled = false;
    submitBtn.textContent = "Create account";
    modal.classList.remove("hidden");
    setTimeout(() => $("#new-acct-name").focus(), 50);
    // Close account dropdown
    $("#account-dropdown").classList.remove("open");
    $("#account-btn").setAttribute("aria-expanded", "false");
  }

  function closeModal() { modal.classList.add("hidden"); }

  const openBtn = $("#btn-create-account");
  if (openBtn) openBtn.addEventListener("click", openModal);
  document.getElementById("create-acct-close").addEventListener("click", closeModal);
  document.getElementById("create-acct-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#new-acct-name").value.trim();
    const type = $("#new-acct-type").value;
    if (!name) return;
    submitBtn.disabled = true;
    submitBtn.textContent = "Creating…";
    errEl.classList.add("hidden");
    try {
      const res = await fetch("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: name, type }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Error ${res.status}`);
      }
      const acct = await res.json();
      closeModal();
      await loadAccountList(acct.account_id);
      init();
    } catch (err) {
      errEl.textContent = err.message || "Failed to create account. Please try again.";
      errEl.classList.remove("hidden");
      submitBtn.disabled = false;
      submitBtn.textContent = "Create account";
    }
  });
}


// ── Refresh button ─────────────────────────────────────────────────────────
function initRefreshBtn() {
  const btn = $("#refresh-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("spinning");
    const label = $("#refresh-label");
    const prev = label.innerHTML;
    label.textContent = "Refreshing…";
    try {
      await init();
    } finally {
      btn.disabled = false;
      btn.classList.remove("spinning");
    }
  });
}

// ── Project Match (always-visible right panel) ────────────────────────────
function initProjectMatch() {
  const dropzone = document.getElementById("pm-dropzone");
  const fileInput = document.getElementById("pm-file-input");
  if (!dropzone || !fileInput) return;

  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag-over");
    addFiles([...e.dataTransfer.files]);
  });
  fileInput.addEventListener("change", () => { addFiles([...fileInput.files]); fileInput.value = ""; });

  document.getElementById("pm-analyze-btn").addEventListener("click", runProjectMatch);
  document.getElementById("pm-clear-btn").addEventListener("click", () => {
    S._pmFiles = [];
    renderFileList();
    document.getElementById("pm-paste").value = "";
    document.getElementById("pm-error").classList.add("hidden");
    document.getElementById("pm-results").innerHTML = `<div class="pm-results-placeholder"><span style="font-size:3rem">🎯</span><p>Upload your project documents and click <strong>Analyze</strong> to find matching grants.</p></div>`;
  });
}

S._pmFiles = [];

function addFiles(newFiles) {
  const allowed = /\.(pdf|doc|docx|xlsx|xls|txt|md|py|js|csv)$/i;
  const added = newFiles.filter(f => allowed.test(f.name) && !S._pmFiles.find(x => x.name === f.name));
  S._pmFiles = [...S._pmFiles, ...added].slice(0, 10);
  renderFileList();
}

function renderFileList() {
  const el = document.getElementById("pm-file-list");
  if (!el) return;
  el.innerHTML = S._pmFiles.map((f, i) =>
    `<div class="pm-file-item">
      <span class="pm-file-name">${esc(f.name)}</span>
      <span class="pm-file-size">(${(f.size / 1024).toFixed(0)} KB)</span>
      <button class="pm-file-remove" data-idx="${i}" title="Remove">✕</button>
    </div>`
  ).join("");
  el.querySelectorAll(".pm-file-remove").forEach(btn => {
    btn.onclick = () => { S._pmFiles.splice(+btn.dataset.idx, 1); renderFileList(); };
  });
}

async function runProjectMatch() {
  const pasteText = (document.getElementById("pm-paste")?.value || "").trim();
  const files = S._pmFiles || [];
  const errEl = document.getElementById("pm-error");
  const resultsEl = document.getElementById("pm-results");

  if (!pasteText && files.length === 0) {
    errEl.textContent = "Please add at least one file or paste some text.";
    errEl.classList.remove("hidden");
    return;
  }
  errEl.classList.add("hidden");

  // Loading state
  const analyzeBtn = document.getElementById("pm-analyze-btn");
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing…";
  resultsEl.innerHTML = `<div class="pm-loading"><div class="pm-spinner"></div><p>Extracting project content and matching against grants…</p></div>`;

  try {
    const formData = new FormData();
    formData.append("pasted_text", pasteText);
    formData.append("account_id", S.accountId);
    for (const f of files) formData.append("files", f);

    const res = await fetch(`/api/project-match?account_id=${encodeURIComponent(S.accountId)}`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }
    const data = await res.json();
    S.pmResult = data;
    renderProjectMatchResults(data);
  } catch (e) {
    resultsEl.innerHTML = `<div class="pm-error-inline">⚠ ${esc(e.message || "Analysis failed. Try again.")}</div>`;
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze & Match Grants";
  }
}

// Eligibility+relevance score for PM (0-100, based only on those two components)
function pmScore(m) {
  const elig = m.score_breakdown?.eligibility ?? 0;
  const rel = m.relevance_score ?? 0;
  return Math.round((elig + rel) / 2);
}

function renderProjectMatchResults(data) {
  const el = document.getElementById("pm-results");
  const allMatches = data.matches ?? [];
  const keywords = (data.project_keywords ?? []).slice(0, 10);

  // Filter to above 80% combined eligibility+relevance
  const above80 = allMatches.filter(m => pmScore(m) >= 80);
  const top3 = above80.slice(0, 3);
  // Rest: sort by close_date (soonest first) then effort (fewest materials first)
  const effortOrder = { Low: 0, Medium: 1, High: 2 };
  const rest = above80.slice(3).sort((a, b) => {
    if (a.close_date && b.close_date) return a.close_date.localeCompare(b.close_date);
    if (a.close_date) return -1;
    if (b.close_date) return 1;
    return (effortOrder[a.effort?.level] ?? 1) - (effortOrder[b.effort?.level] ?? 1);
  });

  const top3HTML = top3.length ? top3.map((m, i) => {
    const score = pmScore(m);
    return `<div class="pm-top3-card">
      <div class="pm-top3-badge">Top Match #${i+1}</div>
      <div class="pm-top3-header">
        <div class="pm-top3-info">
          <div class="pm-top3-title">${esc(m.title || m.opp_id)}</div>
          <div class="pm-top3-funder">${esc(m.funder || "")}</div>
        </div>
        ${scoreRing(score, 52)}
      </div>
      <div class="pm-top3-reason">${esc(m.reason || "")}</div>
      <div class="pm-top3-meta">
        ${m.close_date ? `<span class="opp-due">Due ${esc(fmtDate(m.close_date))}</span>` : ""}
        ${m.effort?.level ? `<span class="badge badge-effort-${m.effort.level.toLowerCase()}">${esc(m.effort.level)} effort</span>` : ""}
        ${m.eligibility_verdict ? eligibilityChip(m) : ""}
        ${m.url ? `<a class="pm-match-link" href="${esc(m.url)}" target="_blank" rel="noopener">View grant ↗</a>` : ""}
        <button class="pm-view-grant btn-secondary" data-id="${esc(m.opp_id)}" style="padding:4px 10px;font-size:12px">See details</button>
      </div>
    </div>`;
  }).join("") : `<p class="profile-empty">No matches above 80% found. Try adding more project detail.</p>`;

  const restHTML = rest.length ? rest.map((m, i) => {
    const score = pmScore(m);
    return `<div class="pm-rest-card">
      <div class="pm-rest-rank">#${i + 4}</div>
      <div class="pm-rest-info">
        <div class="pm-rest-title">${esc(m.title || m.opp_id)}</div>
        <div class="pm-rest-meta">
          ${m.funder ? `<span>${esc(m.funder)}</span>` : ""}
          ${m.close_date ? `<span>Due ${esc(fmtDate(m.close_date))}</span>` : ""}
          ${m.effort?.level ? `<span>${esc(m.effort.level)} effort</span>` : ""}
        </div>
      </div>
      <div class="pm-rest-score">${score}</div>
      <button class="pm-view-grant btn-secondary" data-id="${esc(m.opp_id)}" style="padding:4px 10px;font-size:12px;white-space:nowrap">Details</button>
    </div>`;
  }).join("") : "";

  el.innerHTML = `
    <div class="pm-results-section">
      <div class="pm-result-header">
        <h3>Project Summary</h3>
        <div class="pm-keywords">${keywords.map(k => `<span class="pm-kw">${esc(k)}</span>`).join("")}</div>
      </div>
      <p class="pm-summary-text">${esc(data.project_summary || "")}</p>
    </div>
    ${top3.length ? `<div class="pm-top3-section">
      <div class="pm-top3-label">Top Matches (80%+ eligibility &amp; relevance)</div>
      ${top3HTML}
    </div>` : ""}
    ${rest.length ? `<div class="pm-rest-section">
      <div class="pm-rest-label">More Matches (ranked by deadline, then effort)</div>
      <div class="pm-rest-list">${restHTML}</div>
    </div>` : ""}
    ${above80.length === 0 ? "<p class='profile-empty' style='padding:20px 0'>No matches scored above 80%. Try uploading more detailed project documents.</p>" : ""}
    ${allMatches.length > above80.length ? `<p style="font-size:11px;color:var(--faint);margin-top:12px">${allMatches.length - above80.length} additional match${allMatches.length - above80.length !== 1 ? "es" : ""} scored below 80% and were omitted.</p>` : ""}`;

  // Wire "See details / Details" buttons
  el.querySelectorAll(".pm-view-grant").forEach(btn => {
    btn.onclick = () => { selectOpportunity(btn.dataset.id); showToast("Grant selected in left panel"); };
  });
}


// ── Account Profile Modal ──────────────────────────────────────────────────
function initProfileModal() {
  const modal = document.getElementById("profile-modal");
  const closeBtn = document.getElementById("profile-modal-close");
  const openBtn = document.getElementById("btn-view-profile");
  if (!modal) return;

  function openModal() {
    // Populate decisions
    const list = document.getElementById("profile-decisions-list");
    const pursued = Object.entries(S.decisions)
      .filter(([, d]) => d.decision === "pursue" || d.decision === "maybe")
      .map(([oppId, d]) => {
        const opp = S.opportunities.find(o => o.id === oppId);
        return { oppId, decision: d.decision, opp };
      });
    if (pursued.length === 0) {
      list.innerHTML = `<p class="profile-empty">No grants marked pursue or maybe yet.</p>`;
    } else {
      list.innerHTML = pursued.map(({ oppId, decision, opp }) => {
        const decCls = decision === "pursue" ? "decision-pursue" : "decision-maybe";
        const decLabel = decision === "pursue" ? "✓ Pursue" : "? Maybe";
        return `<div class="profile-grant-row" data-id="${esc(oppId)}">
          <span class="decision-badge ${decCls}">${decLabel}</span>
          <div style="flex:1;min-width:0">
            <div class="profile-grant-title">${esc(opp?.title || oppId)}</div>
            ${opp?.funder ? `<div class="profile-grant-funder">${esc(opp.funder)}</div>` : ""}
          </div>
          ${opp?.close_date ? `<span style="font-size:11px;color:var(--muted);white-space:nowrap">Due ${esc(fmtDate(opp.close_date))}</span>` : ""}
        </div>`;
      }).join("");
      // Wire click to select grant
      list.querySelectorAll(".profile-grant-row[data-id]").forEach(row => {
        row.onclick = () => {
          closeModal();
          selectOpportunity(row.dataset.id);
          showToast("Grant selected");
        };
      });
    }

    // Wire past search tags
    document.querySelectorAll(".profile-search-tag").forEach(tag => {
      tag.onclick = () => {
        const q = tag.dataset.search;
        const fSearch = document.getElementById("f-search");
        if (fSearch) { fSearch.value = q; S.filters.search = q; renderList(); }
        closeModal();
        showToast(`Searching: ${q}`);
      };
    });

    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    // Close account dropdown
    document.getElementById("account-dropdown").classList.remove("open");
    document.getElementById("account-btn").setAttribute("aria-expanded", "false");
  }

  function closeModal() {
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  if (openBtn) openBtn.addEventListener("click", openModal);
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
  });
}


// ── Hash-change deep-link listener ────────────────────────────────────────
window.addEventListener("hashchange", () => {
  const m = window.location.hash.match(/^#grant\/(.+)$/);
  if (m) selectOpportunity(decodeURIComponent(m[1]));
});

// ── Compare button state ───────────────────────────────────────────────────
function updateCompareBtn() {
  const btn = document.getElementById("compare-btn");
  if (!btn) return;
  const n = S.compareSet.size;
  if (n === 0) {
    btn.classList.add("hidden");
    btn.disabled = true;
  } else {
    btn.classList.remove("hidden");
    btn.textContent = `⬇ Download (${n})`;
    btn.disabled = false;
    btn.title = `Download ${n} selected grant${n === 1 ? "" : "s"} as CSV`;
  }
}

// ── Compare / Download CSV ─────────────────────────────────────────────────
function downloadCompareCSV() {
  const ids = [...S.compareSet];
  const grants = ids.map((id) => S.opportunities.find((o) => o.id === id)).filter(Boolean);
  if (!grants.length) return;

  function csvCell(val) {
    if (val === null || val === undefined) return "";
    const s = String(val);
    if (s.includes(",") || s.includes('"') || s.includes("\n")) return `"${s.replace(/"/g, '""')}"`;
    return s;
  }

  function fmtAward(g) {
    const ceil = g.award_ceiling ?? g.max_award_usd;
    const floor = g.award_floor;
    if (ceil && floor && floor !== ceil) return `${fmtDollars(floor)} – ${fmtDollars(ceil)}`;
    if (ceil) return `Up to ${fmtDollars(ceil)}`;
    return "not stated";
  }

  const headers = [
    "Title", "Agency / Funder", "Opportunity ID", "Score", "Eligibility %",
    "Confidence", "Award Range", "Close Date", "Days to Close",
    "Status", "Effort Level", "Materials Needed", "Decision", "URL"
  ];

  const rows = grants.map((g) => {
    const decObj = loadDecisions()[g.id];
    return [
      g.title,
      g.funder || "not stated",
      g.id,
      g.score ?? "",
      g.score_breakdown?.eligibility ?? "",
      g.confidence || "",
      fmtAward(g),
      g.close_date ? fmtDate(g.close_date) : "not stated",
      g.days_to_close ?? "",
      g.status || "not stated",
      g.effort?.level || "not stated",
      appMaterials(g).join("; "),
      decObj?.decision || "",
      g.url || g.link || ""
    ].map(csvCell);
  });

  const csvContent = [headers.map(csvCell).join(","), ...rows.map((r) => r.join(","))].join("\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date().toISOString().slice(0, 10);
  a.download = `grants-compare-${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast(`Downloaded ${grants.length} grant${grants.length === 1 ? "" : "s"} as CSV`);
}

function initCompare() {
  const btn = document.getElementById("compare-btn");
  if (btn) btn.addEventListener("click", downloadCompareCSV);
}

// ── Drag handles ───────────────────────────────────────────────────────────
function makeDraggable(handle, onMove, onDone) {
  if (!handle) return;
  let startX = 0;
  let lastVal = null;

  function move(clientX) {
    const dx = clientX - startX;
    startX = clientX;
    lastVal = onMove(dx);
  }

  function stop() {
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    handle.classList.remove("is-dragging");
    document.removeEventListener("mousemove", onMM);
    document.removeEventListener("mouseup", stop);
    document.removeEventListener("touchmove", onTM);
    document.removeEventListener("touchend", stop);
    if (lastVal != null) onDone(lastVal);
  }

  function onMM(e) { move(e.clientX); }
  function onTM(e) { e.preventDefault(); move(e.touches[0].clientX); }

  function start(clientX) {
    startX = clientX;
    lastVal = null;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    handle.classList.add("is-dragging");
    document.addEventListener("mousemove", onMM);
    document.addEventListener("mouseup", stop);
    document.addEventListener("touchmove", onTM, { passive: false });
    document.addEventListener("touchend", stop);
  }

  handle.addEventListener("mousedown", (e) => { e.preventDefault(); start(e.clientX); });
  handle.addEventListener("touchstart", (e) => { e.preventDefault(); start(e.touches[0].clientX); }, { passive: false });
}

function initDragHandles() {
  const MIN_SIDEBAR = 180;
  const MIN_PM      = 240;

  const grantsHalf = document.querySelector(".grants-half");
  const pmHalf     = document.querySelector(".pm-half");
  const leftPanel  = document.querySelector(".left-panel");

  // Restore saved widths from previous session
  const savedLeftW   = localStorage.getItem("ffm-left-w");
  const savedGrantsW = localStorage.getItem("ffm-grants-w");
  if (savedLeftW) document.documentElement.style.setProperty("--left-w", savedLeftW);
  if (savedGrantsW && grantsHalf && pmHalf) {
    grantsHalf.style.flex = "none";
    grantsHalf.style.width = savedGrantsW;
    pmHalf.style.flex = "1";
  }

  // Handle 1: sidebar ↔ detail panel
  makeDraggable(
    document.getElementById("drag-inner"),
    (dx) => {
      if (!leftPanel) return null;
      const cur = leftPanel.getBoundingClientRect().width;
      const next = Math.max(MIN_SIDEBAR, cur + dx);
      document.documentElement.style.setProperty("--left-w", next + "px");
      return next + "px";
    },
    (val) => localStorage.setItem("ffm-left-w", val)
  );

  // Handle 2: grants half ↔ PM half
  makeDraggable(
    document.getElementById("drag-outer"),
    (dx) => {
      if (!grantsHalf || !pmHalf) return null;
      const totalW = document.querySelector(".app-layout").getBoundingClientRect().width;
      const curW   = grantsHalf.getBoundingClientRect().width;
      const nextW  = Math.max(400, Math.min(totalW - MIN_PM, curW + dx));
      grantsHalf.style.flex = "none";
      grantsHalf.style.width = nextW + "px";
      pmHalf.style.flex = "1";
      return nextW + "px";
    },
    (val) => localStorage.setItem("ffm-grants-w", val)
  );
}
initHowModal();
initCreateAccountModal();
initProfileModal();
initCompare();
initDragHandles();
initRefreshBtn();
initAccountMenu();
initFilters();
initProjectMatch();
init();
