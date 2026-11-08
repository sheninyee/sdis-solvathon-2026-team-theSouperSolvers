/* app.js - Souper Grants dashboard (fetches /api/data, draws cards + Plotly charts) */
const $ = (s) => document.querySelector(s);
const fmt = (n) => Math.round(n).toLocaleString();
const pct = (v, d = 0) => (v * 100).toFixed(d) + "%";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (v) => v >= 1e9 ? "$" + (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? "$" + (v / 1e6).toFixed(1) + "M" : "$" + fmt(v);
const wrap = (s, w = 29) => s.replace(new RegExp("(.{1," + w + "})(\\s+|$)", "g"), "$1<br>").replace(/<br>$/, "");

const CAT_COLORS = ["#2e7d70", "#c95b41", "#cfe4a6", "#173c36", "#e3a857", "#9c7a5b"]; // "Other" is stone
const OTHER_COLOR = "#b7c4bd";
const WHITE_TEXT_IDX = [0, 1, 3, 5];   // categories dark enough for white labels
const SIZE_COLORS = ["#2e7d70", "#c95b41", "#cfe4a6"];
const INK = "#173c36";
const GRID = "#e6e8db";

const S = { meta: null, data: null, tab: "dashboard", year: "all", type: "all",
            agencies: new Set(), profile: null, scoreCat: "all", metric: "combined" };

const catColor = (c) => c === "Other" ? OTHER_COLOR : CAT_COLORS[S.meta.categories.indexOf(c) % CAT_COLORS.length];
const catText = (c) => c === "Other" ? INK : (WHITE_TEXT_IDX.includes(S.meta.categories.indexOf(c)) ? "#fff" : INK);

const layout = (extra = {}) => Object.assign({
  paper_bgcolor: "#fbfcfe", plot_bgcolor: "#fbfcfe",
  font: { family: "Arial,sans-serif", color: INK, size: 13 },
  margin: { l: 10, r: 20, t: 10, b: 10 },
  legend: { orientation: "h", y: -0.22 },
}, extra);
const draw = (id, traces, lay) => Plotly.newPlot(id, traces, lay, { displayModeBar: false, responsive: true });

/* ---------- data helpers ---------- */
function totalsBy(rows, key) {
  const m = new Map();
  rows.forEach((r) => {
    const t = m.get(r[key]) || { name: r[key], n: 0, funding: 0, ceil_n: 0, small: 0, mid: 0, large: 0,
                                 award_sum: 0, award_n: 0, exp_sum: 0, exp_n: 0 };
    for (const k of ["n", "funding", "ceil_n", "small", "mid", "large", "award_sum", "award_n", "exp_sum", "exp_n"]) t[k] += r[k] || 0;
    m.set(r[key], t);
  });
  return [...m.values()];
}
const minN = () => Math.max(1, Math.ceil(+$("#minn").value || 20));
const periodLabel = () => S.year === "all" ? "All years" : S.year;

/* ---------- shared HTML pieces ---------- */
const card = (title, sub, body) => `<div class="compass-card"><h3>${title}</h3>${sub ? `<p class="muted">${sub}</p>` : ""}${body}</div>`;
const kpi = (label, value, detail) => `<div class="kpi"><div class="eyebrow">${label}</div><div class="kpi-value">${value}</div><div class="muted">${detail || ""}</div></div>`;
const notice = (html) => `<div class="notice">${html}</div>`;

/* ---------- chart builders ---------- */
function landscapeChart(id, rows) {
  const tot = totalsBy(rows, "agency").sort((a, b) => a.n - b.n);
  const labels = tot.map((t) => wrap(t.name));
  const traces = S.meta.categories.map((c) => {
    const share = tot.map((t) => {
      const r = rows.find((x) => x.agency === t.name && x.funding_category === c);
      return r ? r.n / t.n : 0;
    });
    return { type: "bar", orientation: "h", name: c, x: share, y: labels, marker: { color: catColor(c) },
             text: share.map((s) => s >= 0.07 ? pct(s) : ""), textposition: "inside", insidetextanchor: "middle",
             textfont: { color: catText(c), size: 13 }, hovertemplate: c + ": %{x:.0%}<extra></extra>" };
  });
  draw(id, traces, layout({ barmode: "stack", bargap: 0.18,
    xaxis: { range: [0, 1], tickvals: [0, .25, .5, .75, 1], tickformat: ".0%", title: "Share within agency", gridcolor: "#eceff3" },
    yaxis: { categoryorder: "array", categoryarray: labels, automargin: true },
    legend: { orientation: "h", y: -0.2 } }));
}

function sizeChart(id, groups, key) {
  const g = groups.filter((t) => t.ceil_n > 0).sort((a, b) => (a.large / a.ceil_n) - (b.large / b.ceil_n));
  const labels = g.map((t) => wrap(t.name, 24) + `<br>(n=${fmt(t.ceil_n)}${t.ceil_n < minN() ? "; low n" : ""})`);
  const names = S.meta.size_labels, fields = ["small", "mid", "large"];
  const traces = fields.map((f, i) => {
    const share = g.map((t) => t[f] / t.ceil_n);
    return { type: "bar", orientation: "h", name: names[i], x: share, y: labels, marker: { color: SIZE_COLORS[i] },
             text: share.map((s) => s >= 0.07 ? pct(s) : ""), textposition: "inside", insidetextanchor: "middle",
             textfont: { color: i < 2 ? "#fff" : INK, size: 13 }, hovertemplate: names[i] + ": %{x:.0%}<extra></extra>" };
  });
  draw(id, traces, layout({ barmode: "stack", bargap: 0.3,
    xaxis: { range: [0, 1], tickvals: [0, .25, .5, .75, 1], tickformat: ".0%", title: "Share of grants with a stated award ceiling", gridcolor: GRID },
    yaxis: { categoryorder: "array", categoryarray: labels, automargin: true } }));
}

function barChart(id, names, values, colors, title, hoverFmt) {
  draw(id, [{ type: "bar", orientation: "h", x: values, y: names, marker: { color: colors },
              hovertemplate: "%{y}: " + hoverFmt + "<extra></extra>" }],
    layout({ showlegend: false, bargap: 0.35,
      xaxis: { title, gridcolor: "#eceff3" }, yaxis: { automargin: true } }));
}

/* ---------- tabs ---------- */
function renderDashboard() {
  const rows = S.data.rows;
  if (!rows.length) return ($("#view").innerHTML = `<div class="empty">No grants match this selection. Try another year, type or agency.</div>`);
  const tot = totalsBy(rows, "agency"), cats = totalsBy(rows, "funding_category");
  const n = tot.reduce((s, t) => s + t.n, 0), funding = tot.reduce((s, t) => s + t.funding, 0);
  const h = Math.max(370, tot.length * 52 + 150);

  $("#view").innerHTML = `
    <div class="kpis">
      ${kpi("Eligible grants", fmt(n), periodLabel())}
      ${kpi("Agencies", tot.length, "In the current view")}
      ${kpi("Total funding", money(funding), "Estimated program funding")}
      ${kpi("Funding categories", cats.length, "With eligible records")}
    </div>
    ${card("The grant landscape", "Each bar is an agency; colors show funding categories in the eligible grant records.",
      `<label class="radio"><input type="radio" checked> 100% stacked</label>
       <div class="chart-scroll"><div id="c-landscape" style="height:${h}px"></div></div>`)}
    <div class="two-col">
      ${card("Award size by agency", "Three award-size bands fill each bar to 100%; grants with a stated ceiling only.", `<div id="c-size" style="height:${Math.max(430, tot.length * 60 + 120)}px"></div>`)}
      ${card("Funding category mix", "Share of eligible grant records in the current selection.", `<div id="c-mix" style="height:350px"></div>`)}
    </div>
    ${card("Across the years", `<span>Grants by year posted. The selected year is highlighted.</span>`, `<div id="c-years" style="height:280px"></div>`)}`;

  landscapeChart("c-landscape", rows);
  sizeChart("c-size", tot);
  const cs = cats.sort((a, b) => a.n - b.n);
  barChart("c-mix", cs.map((c) => c.name), cs.map((c) => c.n / n), cs.map((c) => catColor(c.name)), "Share of eligible grants", "%{x:.1%}");
  const y = S.data.years;
  draw("c-years", [{ type: "bar", x: y.map((d) => String(d.year)), y: y.map((d) => d.n),
    marker: { color: y.map((d) => String(d.year) === S.year ? "#b8842e" : "#3f6e64") } }],
  layout({
    showlegend: false, bargap: 0.4,
    paper_bgcolor: "#fffdf8", plot_bgcolor: "#fffdf8",
    margin: { l: 10, r: 20, t: 10, b: 10 },
    xaxis: { title: { text: "Year posted", standoff: 12 }, type: "category", automargin: true },
    yaxis: { title: { text: "Grants", standoff: 12 }, gridcolor: GRID, automargin: true },
}));
}

function renderAgency() {
  const rows = S.data.rows;
  const names = totalsBy(rows, "agency").map((t) => t.name).sort();
  if (!names.length) return ($("#view").innerHTML = `<div class="empty">No grants match this selection.</div>`);
  if (!names.includes(S.profile)) S.profile = names[0];
  const mine = rows.filter((r) => r.agency === S.profile);
  const t = totalsBy(mine, "agency")[0];
  const cats = totalsBy(mine, "funding_category");

  $("#view").innerHTML = `
    ${card("Inside an agency", "", `<label for="pick" class="eyebrow">Choose an agency</label>
      <select id="pick" class="form-control" style="margin-top:6px">${names.map((a) => `<option ${a === S.profile ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>`)}
    <div class="kpis">
      ${kpi("Eligible grants", fmt(t.n), esc(S.profile))}
      ${kpi("Total funding", money(t.funding), periodLabel())}
      ${kpi("With stated ceiling", t.n ? pct(t.ceil_n / t.n, 1) : "-", "Share of grants")}
      ${kpi("Funding categories", cats.length, "With eligible records")}
    </div>
    ${card("Award size by funding category", "Within each category, the three size bands sum to 100%.", `<div id="c-a1" style="height:${Math.max(400, cats.length * 70 + 120)}px"></div>`)}
    ${card("Where grants concentrate", "", `<div id="c-a2" style="height:350px"></div>`)}`;

  $("#pick").onchange = (e) => { S.profile = e.target.value; renderAgency(); };
  sizeChart("c-a1", cats);
  const cs = cats.sort((a, b) => a.n - b.n);
  barChart("c-a2", cs.map((c) => c.name), cs.map((c) => c.n), cs.map((c) => catColor(c.name)), "Eligible grants", "%{x:,}");
}

function renderScores() {
  const rows = S.data.rows;
  if (!rows.length) return ($("#view").innerHTML = `<div class="empty">No grants match this selection.</div>`);
  const cats = ["all", ...S.meta.categories];
  const metrics = { combined: "Combined score (award + expected awards)", award: "Award range score", expected: "Expected awards score", n: "Grant count" };
  const avg = (t, m) => m === "award" ? t.award_sum / t.award_n : m === "expected" ? t.exp_sum / t.exp_n
    : m === "n" ? t.n : (t.award_sum / t.award_n) + (t.exp_sum / t.exp_n);

  $("#view").innerHTML = `
    ${card("Compare what matters", "", `<div class="row-2">
      <div><label class="eyebrow">Funding category</label><select id="sc-cat" class="form-control" style="margin-top:6px">
        ${cats.map((c) => `<option value="${esc(c)}" ${c === S.scoreCat ? "selected" : ""}>${c === "all" ? "All categories" : esc(c)}</option>`).join("")}</select></div>
      <div><label class="eyebrow">Measure</label><select id="sc-metric" class="form-control" style="margin-top:6px">
        ${Object.entries(metrics).map(([k, v]) => `<option value="${k}" ${k === S.metric ? "selected" : ""}>${v}</option>`).join("")}</select></div></div>`)}
    ${notice("<strong>" + metrics[S.metric] + ":</strong> higher scores mean larger typical awards and/or more expected awards. Groups below the minimum sample size stay visible in gray and are labeled low n.")}
    ${card("Agency comparison", "", `<div class="chart-scroll"><div id="c-s1"></div></div>`)}
    ${card("Compare all funding categories", "Combined score by agency and category. Blank cells mean no eligible records; * marks low n.", `<div class="chart-scroll"><div id="c-s2"></div></div>`)}`;

  $("#sc-cat").onchange = (e) => { S.scoreCat = e.target.value; renderScores(); };
  $("#sc-metric").onchange = (e) => { S.metric = e.target.value; renderScores(); };

  // bar chart
  const sel = S.scoreCat === "all" ? rows : rows.filter((r) => r.funding_category === S.scoreCat);
  const g = totalsBy(sel, "agency").map((t) => ({ ...t, v: avg(t, S.metric) })).filter((t) => isFinite(t.v)).sort((a, b) => a.v - b.v);
  if (!g.length) $("#c-s1").innerHTML = `<div class="empty">No eligible records for this category.</div>`;
  else {
    const h = Math.max(350, g.length * 46 + 120);
    $("#c-s1").style.height = h + "px";
    draw("c-s1", [{ type: "bar", orientation: "h", x: g.map((t) => t.v), y: g.map((t) => wrap(t.name)),
      marker: { color: g.map((t) => t.n < minN() ? "#a2acb9" : "#3f6e64") },
      text: g.map((t) => (S.metric === "n" ? fmt(t.v) : t.v.toFixed(2)) + "  |  n=" + fmt(t.n) + (t.n < minN() ? " (low n)" : "")),
      textposition: "outside", cliponaxis: false, hoverinfo: "text" }],
      layout({ showlegend: false, bargap: 0.38, margin: { l: 10, r: 150, t: 10, b: 10 },
        xaxis: { title: metrics[S.metric], gridcolor: "#eceff3", zeroline: true }, yaxis: { automargin: true } }));
  }

  // heatmap
  const ag = totalsBy(rows, "agency").map((t) => t.name).sort().reverse();
  const z = ag.map((a) => S.meta.categories.map((c) => {
    const r = rows.find((x) => x.agency === a && x.funding_category === c);
    return r && r.award_n && r.exp_n ? avg(r, "combined") : null;
  }));
  const txt = ag.map((a) => S.meta.categories.map((c) => {
    const r = rows.find((x) => x.agency === a && x.funding_category === c);
    return r && r.award_n && r.exp_n ? avg(r, "combined").toFixed(1) + (r.n < minN() ? "*" : "") : "";
  }));
  $("#c-s2").style.height = Math.max(400, ag.length * 55 + 120) + "px";
  draw("c-s2", [{ type: "heatmap", x: S.meta.categories.map((c) => wrap(c, 16)), y: ag.map((a) => wrap(a)), z, text: txt,
    texttemplate: "%{text}", xgap: 3, ygap: 3, colorscale: [[0, "#eff3f0"], [1, "#72aa96"]], colorbar: { title: "Score" },
    hovertemplate: "%{y}<br>%{x}: %{z:.1f}<extra></extra>" }],
    layout({ margin: { l: 10, r: 10, t: 10, b: 10 }, yaxis: { automargin: true }, xaxis: { side: "bottom" } }));
}

/* ---------- page plumbing ---------- */
function render() {
  const rows = S.data.rows, n = rows.reduce((s, r) => s + r.n, 0), c = rows.reduce((s, r) => s + r.ceil_n, 0);
  $("#status").innerHTML = notice(`${periodLabel()} | ${fmt(n)} grant opportunities in view; ${fmt(c)} have a stated award ceiling.`);
  $("#badge").textContent = `Grants snapshot \u00b7 ${S.meta.total.toLocaleString()} records`;
  ({ dashboard: renderDashboard, agency: renderAgency, scores: renderScores })[S.tab]();
}

function query() {
  const p = new URLSearchParams({ year: S.year, type: S.type });
  S.agencies.forEach((a) => p.append("agency", a));
  return p;
}

async function refresh() {
  S.data = await (await fetch("/api/data?" + query())).json();
  $("#download").href = "/download?" + query();
  render();
}

function buildFilters() {
  const m = S.meta;
  $("#type").innerHTML = `<option value="all">All types</option>` + m.types.map((t) => `<option>${esc(t)}</option>`).join("");
  $("#year").innerHTML = `<option value="all">All years</option>` + m.years.map((y) => `<option>${y}</option>`).join("");
  $("#type").onchange = (e) => { S.type = e.target.value; refresh(); };
  $("#year").onchange = (e) => { S.year = e.target.value; refresh(); };
  $("#minn").onchange = render;

  const box = $("#ms-box"), panel = $("#ms-panel");
  panel.innerHTML = m.agencies.map((a) => `<label class="ms-item"><input type="checkbox" value="${esc(a)}"> ${esc(a)}</label>`).join("");
  box.onclick = () => panel.classList.toggle("open");
  panel.onchange = (e) => {
    e.target.checked ? S.agencies.add(e.target.value) : S.agencies.delete(e.target.value);
    box.textContent = S.agencies.size ? `${S.agencies.size} selected` : "All available agencies";
    refresh();
  };
  document.addEventListener("click", (e) => { if (!e.target.closest("#ms")) panel.classList.remove("open"); });

  $("#tabs").onclick = (e) => {
    if (!e.target.dataset.tab) return;
    S.tab = e.target.dataset.tab;
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === e.target));
    render();
  };
}

(async function init() {
  S.meta = await (await fetch("/api/meta")).json();
  buildFilters();
  refresh();
})();