"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { runs: [], comparison: null, before: "", after: "", tab: "analysis", sequence: 0, tour: -1, replayed: false };
const escapeHtml = (value) => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fixed = (value, decimals = 2) => Number(value).toFixed(decimals);
const signed = (value, decimals = 2) => `${Number(value) > 0 ? "+" : ""}${fixed(value, decimals)}`;
const short = value => value.slice(0, 10);
const human = { data: "Data snapshot", source: "Model source", config: "Configuration", environment: "Environment", output: "Output" };

async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}

function errorMessage(error) {
  $("#error-banner").textContent = error.message || String(error);
  $("#error-banner").hidden = false;
}

let toastTimer;
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4500);
}

async function busy(button, action) {
  if (button.disabled) return;
  const original = button.innerHTML;
  button.disabled = true;
  button.textContent = "Working…";
  try { await action(); } catch (error) { errorMessage(error); }
  finally { button.disabled = false; if (button.textContent === "Working…") button.innerHTML = original; }
}

function seedRun(number) {
  return [...state.runs].reverse().find(run => run.label.startsWith(`${String(number).padStart(2, "0")} ·`));
}

async function loadRuns() {
  state.runs = (await api("/api/runs")).runs;
  if (!state.runs.length) throw new Error("No captured runs are available. Restart the server to initialise the demo.");
  state.before ||= seedRun(1)?.id || state.runs[state.runs.length - 1].id;
  state.after ||= seedRun(2)?.id || state.runs[0].id;
  renderLibrary();
  await compare();
}

function renderLibrary() {
  $("#run-count").textContent = state.runs.length;
  const options = state.runs.map(run => `<option value="${escapeHtml(run.id)}">${escapeHtml(run.label)} · ${short(run.id)}</option>`).join("");
  $("#before-select").innerHTML = options;
  $("#after-select").innerHTML = options;
  $("#before-select").value = state.before;
  $("#after-select").value = state.after;
  $("#run-list").innerHTML = [...state.runs].reverse().map(run => `<button class="run-item ${state.after === run.id ? "selected" : ""}" data-run="${escapeHtml(run.id)}" aria-pressed="${state.after === run.id}"><span class="run-item-top"><span class="run-item-id">${short(run.id)}</span><span class="run-dot"></span></span><span class="run-item-title">${escapeHtml(run.label)}</span><span class="run-item-meta">${run.observations} obs · ${escapeHtml(run.model === "equal-weight" ? "v1 / equal weight" : "v2 / recency")} ${run.approved ? "· approved" : ""}</span></button>`).join("");
  $ ("#run-list").querySelectorAll("[data-run]").forEach(button => button.addEventListener("click", () => choose(state.before, button.dataset.run)));
}

async function choose(before, after) {
  state.before = before;
  state.after = after;
  renderLibrary();
  await compare();
}

async function compare() {
  const sequence = ++state.sequence;
  $("#error-banner").hidden = true;
  $("#replay-result").hidden = true;
  $("#release-result").hidden = true;
  state.replayed = false;
  try {
    const result = await api(`/api/compare?before=${encodeURIComponent(state.before)}&after=${encodeURIComponent(state.after)}`);
    if (sequence !== state.sequence) return;
    state.comparison = result;
    renderAnalysis(); renderArtifacts(); renderRelease();
  } catch (error) { if (sequence === state.sequence) errorMessage(error); }
}

function renderAnalysis() {
  const c = state.comparison;
  const b = c.before, a = c.after, index = c.common_step - 1;
  const delta = (Number(a.output.forecast[index]) - Number(b.output.forecast[index])) * 100;
  const changedInputs = c.changed_artifacts.filter(key => key !== "output");
  $("#comparison-status").innerHTML = `<span class="status-pill ${changedInputs.length ? "" : "match"}">${changedInputs.length ? `${changedInputs.length} input${changedInputs.length === 1 ? "" : "s"} changed` : "Inputs identical"}</span>`;
  $("#metrics").innerHTML = [
    ["REFERENCE FORECAST", fixed(b.output.forecast[index], 4), "%", `At observation step T+${c.common_step}`, false],
    ["CANDIDATE FORECAST", fixed(a.output.forecast[index], 4), "%", `At observation step T+${c.common_step}`, false],
    ["OUTPUT DIFFERENCE", signed(delta), "bp", `Candidate minus reference · T+${c.common_step}`, Math.abs(delta) > 0.000001],
    ["CHANGED OBSERVATIONS", c.data_changes.length, "", `${b.observations} → ${a.observations} observations captured`, false]
  ].map(([label, value, unit, note, accent]) => `<div class="metric ${accent ? "accent" : ""}"><div class="metric-label">${label}</div><div class="metric-value">${value}<span class="unit">${unit}</span></div><div class="metric-note">${note}</div></div>`).join("");
  const titles = { data: "Data snapshot differs", source: "Model source differs", config: "Configuration differs", environment: "Environment differs" };
  if (changedInputs.length === 1) {
    $("#finding-title").innerHTML = titles[changedInputs[0]];
    const copies = {
      data: `${c.data_changes.length} observation${c.data_changes.length === 1 ? " differs" : "s differ"} between these snapshots. Model source and parameters are identical. ${Math.abs(delta) < 0.000001 ? "The displayed forecast is unchanged; inspect the other output artifacts." : `The T+${c.common_step} forecast moved ${signed(delta)} basis points.`}`,
      source: "The captured source fingerprint differs. Data and parameters are unchanged. Inspect the implementations in the artifact ledger.",
      config: "The configuration fingerprint differs. Compare the captured parameter values below to see what changed.",
      environment: "The recorded execution context differs. Strict replay requires the original reviewed engine and runtime."
    };
    $("#finding-copy").textContent = copies[changedInputs[0]];
  } else if (!changedInputs.length) {
    $("#finding-title").textContent = "Input artifacts match";
    $("#finding-copy").textContent = "All captured input fingerprints match. Run labels and timestamps may differ without changing the computation.";
  } else {
    $("#finding-title").textContent = "Multiple inputs differ";
    $("#finding-copy").textContent = `${changedInputs.length} input artifacts differ. Controlled replays swap data, model and configuration in a stated order to investigate the output difference.`;
  }
  $("#fingerprints").innerHTML = ["source", "data", "config", "environment", "output"].map(key => `<div class="fingerprint ${c.changed_artifacts.includes(key) ? "changed" : ""}"><span>${human[key]}</span><code title="Reference: ${b.artifacts[key]}&#10;Candidate: ${a.artifacts[key]}">${short(a.artifacts[key])}</code><span class="fingerprint-state">${c.changed_artifacts.includes(key) ? "DIFF" : "MATCH"}</span></div>`).join("");
  $("#run-context").innerHTML = `<table><thead><tr><th>Role</th><th>Run ID</th><th>As-of observation</th><th>Model</th><th>Lookback</th><th>Horizon</th><th>Runtime</th></tr></thead><tbody>${[["REF", b], ["CAND", a]].map(([role, run]) => `<tr><td>${role}</td><td>${escapeHtml(run.id)}</td><td>${escapeHtml(run.as_of)}</td><td>${escapeHtml(run.model)}</td><td>${run.config.lookback}</td><td>${run.config.horizon}</td><td>CPython ${escapeHtml(run.environment.python)}</td></tr>`).join("")}</tbody></table>`;
  const diffRows = [
    ...c.data_changes.map(row => `<tr><td>${escapeHtml(row.date)}</td><td class="value-old">${escapeHtml(row.before)}</td><td class="value-new">${escapeHtml(row.after)}</td><td><span class="change-badge">${row.before !== null && row.after !== null ? `${signed((Number(row.after) - Number(row.before)) * 100)} bp` : row.kind}</span></td></tr>`),
    ...c.config_changes.map(row => `<tr><td>${escapeHtml(row.key)}</td><td class="value-old">${escapeHtml(row.before)}</td><td class="value-new">${escapeHtml(row.after)}</td><td><span class="change-badge">parameter</span></td></tr>`)
  ];
  $("#diff-count").textContent = `${diffRows.length} difference${diffRows.length === 1 ? "" : "s"}`;
  $("#diff-body").innerHTML = diffRows.join("") || '<tr><td colspan="4" class="empty-row">No observation or configuration differences.</td></tr>';
  $("#diff-explainer").textContent = a.provenance.injected_change ? "This scenario injects a +25 bp correction into one historical observation. It is not an actual FRED revision." : "Comparison uses the exact captured values, not a fresh download. Yield values are in percent; changes are in basis points.";
  $("#common-step").textContent = `T+${c.common_step}`;
  const max = Math.max(0.01, ...c.waterfall.slice(1).map(row => Math.abs(Number(row.change_bps))));
  $("#waterfall").innerHTML = c.waterfall.length ? c.waterfall.slice(1).map(row => `<div class="waterfall-row"><span>${row.stage === "Config" ? "Parameters" : row.stage}</span><div class="waterfall-track"><div class="waterfall-fill ${Number(row.change_bps) === 0 ? "zero" : ""}"></div></div><span class="mono">${signed(row.change_bps)} bp</span></div>`).join("") + `<div class="waterfall-summary"><span>Combined difference</span><span class="mono">${signed(delta)} bp</span></div>` : `<p class="section-copy">This combination cannot be replayed: ${escapeHtml(c.attribution_error)} Compare compatible lookback lengths to investigate the change.</p>`;
  $("#waterfall").querySelectorAll(".waterfall-fill").forEach((bar, i) => { bar.style.width = `${Math.max(1, Math.abs(Number(c.waterfall[i + 1].change_bps)) / max * 100)}%`; });
  renderChart();
}

let chartPoints = [];
function renderChart() {
  const c = state.comparison, svg = $("#forecast-chart");
  const history = c.before.data.slice(-45);
  const candidateHistory = c.after.data.slice(-45);
  // An observation-index chart: a different as-of date is shown explicitly.
  const comparableDates = c.before.as_of === c.after.as_of;
  const dates = [...new Set([...history.map(r => r.date), ...candidateHistory.map(r => r.date)])].sort();
  const values = [...history, ...candidateHistory].map(r => Number(r.value)).concat(c.before.output.forecast.map(Number), c.after.output.forecast.map(Number));
  const low = Math.min(...values), high = Math.max(...values), padding = Math.max(0.04, (high - low) * .18);
  const min = low - padding, max = high + padding;
  const width = 820, height = 300, left = 47, right = 30, top = 25, bottom = 35;
  const forecastCount = Math.max(c.before.config.horizon, c.after.config.horizon);
  const total = dates.length + forecastCount;
  const x = i => left + i / Math.max(1, total - 1) * (width - left - right);
  const y = v => top + (max - v) / (max - min) * (height - top - bottom);
  const seriesPath = (rows) => rows.map((r, i) => `${i ? "L" : "M"}${x(dates.indexOf(r.date)).toFixed(2)},${y(Number(r.value)).toFixed(2)}`).join(" ");
  const futurePath = run => [{ x: dates.indexOf(run.as_of), value: Number(run.output.last_yield) }, ...run.output.forecast.map((value, i) => ({ x: dates.indexOf(run.as_of) + i + 1, value: Number(value) }))].map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(2)},${y(p.value).toFixed(2)}`).join(" ");
  let markup = `<title>Treasury yield comparison: ${escapeHtml(c.before.label)} versus ${escapeHtml(c.after.label)}</title><desc>Observed yields in percent with forecasts at observation steps. Reference and candidate data snapshots may differ.</desc>`;
  markup += `<rect class="forecast-region" x="${x(dates.length - 1)}" y="${top}" width="${width - right - x(dates.length - 1)}" height="${height - top - bottom}"/>`;
  for (let i = 0; i <= 4; i++) { const value = min + (max - min) * i / 4; markup += `<line class="grid" x1="${left}" x2="${width - right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left - 9}" y="${y(value) + 3}" text-anchor="end">${fixed(value, 2)}</text>`; }
  markup += `<line class="boundary" x1="${x(dates.length - 1)}" x2="${x(dates.length - 1)}" y1="${top}" y2="${height - bottom}"/><text x="${x(dates.length - 1) + 8}" y="${top + 10}">FORECAST</text>`;
  markup += `<path class="series observed-line" d="${seriesPath(history)}"/>`;
  if (c.changed_artifacts.includes("data")) markup += `<path class="series corrected-line" d="${seriesPath(candidateHistory)}"/>`;
  markup += `<path class="series reference-line" d="${futurePath(c.before)}"/><path class="series candidate-line" d="${futurePath(c.after)}"/>`;
  const afterLast = dates.indexOf(c.after.as_of) + c.after.config.horizon;
  markup += `<circle class="forecast-point" cx="${x(afterLast)}" cy="${y(Number(c.after.output.forecast.at(-1)))}" r="3"/>`;
  for (const i of [0, Math.floor(dates.length * .35), Math.floor(dates.length * .7)]) markup += `<text x="${x(i)}" y="${height - 10}" text-anchor="middle">${escapeHtml(dates[i].slice(5))}</text>`;
  markup += `<text x="${x(total - 1)}" y="${height - 10}" text-anchor="end">T+${forecastCount}</text>`;
  svg.innerHTML = markup;
  $("#chart-range").textContent = comparableDates ? `${dates[0]} — ${c.before.as_of} · captured history` : `Reference as-of ${c.before.as_of} / candidate as-of ${c.after.as_of}`;
  chartPoints = history.map(row => ({ x: x(dates.indexOf(row.date)), y: y(Number(row.value)), label: row.date, value: `${fixed(row.value, 2)}% · reference observation` })).concat(c.after.output.forecast.map((value, i) => ({ x: x(dates.indexOf(c.after.as_of) + i + 1), y: y(Number(value)), label: `Candidate · T+${i + 1}`, value: `${fixed(value, 4)}%` })));
}

function renderArtifacts() {
  const c = state.comparison;
  $("#artifact-table").innerHTML = `<table class="artifact-table"><thead><tr><th>Artifact</th><th>Reference SHA-256</th><th>Candidate SHA-256</th><th>State</th></tr></thead><tbody>${Object.keys(human).map(key => `<tr><td><span class="artifact-name">${human[key]}</span></td><td><span class="artifact-hash">${c.before.artifacts[key]}</span></td><td><span class="artifact-hash">${c.after.artifacts[key]}</span></td><td><span class="status-pill ${c.changed_artifacts.includes(key) ? "" : "match"}">${c.changed_artifacts.includes(key) ? "Changed" : "Identical"}</span></td></tr>`).join("")}</tbody></table>`;
  $("#export-run").href = `/api/runs/${c.after.id}/export`;
  $("#source-heading").textContent = c.after.model === "equal-weight" ? "v1 · Equal-weight drift" : "v2 · Recency-weighted drift";
  $("#source-code").textContent = c.after.source;
  const details = [["Run identifier", c.after.id], ["Captured at (UTC)", c.after.created_at], ["As-of observation", c.after.as_of], ["Python", `${c.after.environment.implementation} ${c.after.environment.python}`], ["Arithmetic", c.after.environment.arithmetic], ["Engine SHA-256", c.after.environment.engine_sha256], ["Lookback / horizon", `${c.after.config.lookback} observations / ${c.after.config.horizon} steps`], ["Manifest SHA-256", c.after.manifest_sha256]];
  $("#environment-details").innerHTML = details.map(([name, value]) => `<div class="environment-row"><span>${name}</span><span>${escapeHtml(value)}</span></div>`).join("");
  $("#provenance-copy").textContent = `${c.after.provenance.source || "Imported observations"}. ${c.after.provenance.scenario}. ${c.after.provenance.retrieved_at ? `Downloaded ${c.after.provenance.retrieved_at}.` : ""}`;
}

function renderRelease() {
  const approved = state.comparison.before.approved;
  $("#approval-description").textContent = approved ? "This reference is approved. Its approval is bound to the captured manifest digest." : "Replay the reference and bind approval to its manifest.";
  $("#approve-run").textContent = approved ? "Reverify approved reference" : "Approve reference";
}

function showTab(name, focus = false) {
  state.tab = name;
  document.querySelectorAll("[data-tab]").forEach(button => { const selected = button.dataset.tab === name; button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1; if (selected && focus) button.focus(); });
  for (const panel of ["analysis", "artifacts", "release"]) $(`#panel-${panel}`).hidden = panel !== name;
}

async function replayReference() {
  const referenceId = state.before;
  const result = await api(`/api/runs/${referenceId}/replay`, {});
  if (referenceId !== state.before) return;
  state.replayed = true;
  $("#replay-result").innerHTML = `<div class="result-heading">PASS / Exact output hash match</div><p>The captured model was re-executed with its original data and configuration.</p><div class="hash-line">expected  ${result.expected_sha256}<br>actual    ${result.actual_sha256}</div><div class="check-tags">${result.checks.map(check => `<span>PASS · ${escapeHtml(check)}</span>`).join("")}</div>`;
  $("#replay-result").hidden = false;
  $("#replay-result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

const tourSteps = [
  ["01 / 04", "Compare original and corrected snapshots", "Model source and configuration match. One historical observation contains an injected +25 bp correction; the final observed yield is unchanged."],
  ["02 / 04", "Inspect the observation diff", "The input diff identifies the corrected date. Sequential replay substitutes the candidate data while holding source and configuration fixed."],
  ["03 / 04", "Verify reference replay", "The server re-executed the captured source, data and configuration. Expected and actual output digests match. This verifies reproducibility, not forecast accuracy."],
  ["04 / 04", "Check the approved artifact set", "The original reference is approved. The candidate is blocked because data and output differ. Select identical approved artifacts to check the passing path."]
];

async function advanceTour() {
  if (state.tour === 3) { state.tour = -1; $("#tour-panel").hidden = true; return; }
  const next = state.tour + 1;
  if (next === 0) { await choose(seedRun(1).id, seedRun(2).id); showTab("analysis"); }
  if (next === 1) showTab("analysis");
  if (next === 2) { showTab("analysis"); await replayReference(); }
  if (next === 3) {
    showTab("release");
    await api(`/api/runs/${state.before}/approve`, {});
    await compare();
    renderReleaseResult(await api("/api/release", { approved_id: state.before, candidate_id: state.after }));
  }
  state.tour = next;
  const [step, title, copy] = tourSteps[next];
  $("#tour-step").textContent = step; $("#tour-title").textContent = title; $("#tour-copy").textContent = copy;
  $("#tour-panel").hidden = false; $("#tour-next").textContent = next === 3 ? "Finish investigation ✓" : "Next step →";
}

function renderReleaseResult(result) {
  $("#release-result").classList.toggle("pass", result.allowed);
  $("#release-result").innerHTML = `<h3><strong>${result.allowed ? "✓ Release check passed" : "⊘ Release blocked"}</strong></h3><p>${escapeHtml(result.message)}</p><p>${result.allowed ? "All five artifact fingerprints match the approved run." : "Create a new approval for changed research before a real production handover."}</p>`;
  $("#release-result").hidden = false;
}

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").trim().split(/\r?\n/).filter(line => line.trim());
  const header = lines.shift()?.split(",").map(value => value.trim());
  if (!header || header.length !== 2 || !["date", "observation_date", "DATE"].includes(header[0]) || !["value", "DGS10"].includes(header[1])) throw new Error("Use a two-column CSV with headers date,value or observation_date,DGS10.");
  return lines.flatMap(line => { const cells = line.split(",").map(value => value.trim()); if (cells.length !== 2) throw new Error("Each CSV row must contain exactly two unquoted columns."); return !cells[1] || cells[1] === "." ? [] : [{ date: cells[0], value: cells[1] }]; });
}

$("#before-select").addEventListener("change", event => choose(event.target.value, state.after));
$("#after-select").addEventListener("change", event => choose(state.before, event.target.value));
$("#swap-runs").addEventListener("click", () => choose(state.after, state.before));
document.querySelectorAll("[data-tab]").forEach((button, index, buttons) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
  button.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length; showTab(buttons[next].dataset.tab, true); } });
});
$("#replay-before").addEventListener("click", event => busy(event.currentTarget, replayReference));
$("#approve-run").addEventListener("click", event => busy(event.currentTarget, async () => { await api(`/api/runs/${state.before}/approve`, {}); await loadRuns(); toast("Reference verified and approval recorded."); }));
$("#check-release").addEventListener("click", event => busy(event.currentTarget, async () => { renderReleaseResult(await api("/api/release", { approved_id: state.before, candidate_id: state.after })); }));
$("#tour-button").addEventListener("click", event => busy(event.currentTarget, async () => { state.tour = -1; await advanceTour(); }));
$("#tour-next").addEventListener("click", event => busy(event.currentTarget, advanceTour));
$("#tour-close").addEventListener("click", () => { state.tour = -1; $("#tour-panel").hidden = true; });
for (const id of ["#new-run", "#mobile-new-run"]) $(id).addEventListener("click", () => { $("#form-error").hidden = true; $("#run-dialog").showModal(); });
$("#close-dialog").addEventListener("click", () => $("#run-dialog").close());
$("#run-dataset").addEventListener("change", event => { $("#upload-field").hidden = event.target.value !== "upload"; });
$("#run-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#capture-submit");
  if (button.disabled) return;
  button.disabled = true; $("#form-error").hidden = true;
  try {
    const payload = { label: $("#run-label").value, model: $("#run-model").value, dataset: $("#run-dataset").value, config: { lookback: Number($("#run-lookback").value), horizon: Number($("#run-horizon").value) }, parent: state.before };
    let endpoint = "/api/runs";
    if (payload.dataset === "upload") { const file = $("#run-file").files[0]; if (!file) throw new Error("Choose a CSV file first."); if (file.size > 1_000_000) throw new Error("Choose a CSV smaller than 1 MB."); payload.rows = parseCsv(await file.text()); endpoint = "/api/import"; }
    const run = await api(endpoint, payload);
    state.after = run.id;
    await loadRuns(); showTab("analysis"); $("#run-dialog").close(); toast("Experiment executed and captured.");
  } catch (error) { $("#form-error").textContent = error.message; $("#form-error").hidden = false; }
  finally { button.disabled = false; }
});
$("#forecast-chart").addEventListener("pointermove", event => {
  if (!chartPoints.length) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width * 820;
  const closest = chartPoints.reduce((best, point) => Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best);
  const tooltip = $("#chart-tooltip"), wrap = $("#chart-wrap").getBoundingClientRect();
  tooltip.innerHTML = `<strong>${escapeHtml(closest.label)}</strong>${escapeHtml(closest.value)}`;
  tooltip.hidden = false;
  tooltip.style.left = `${Math.max(0, Math.min(wrap.width - 190, event.clientX - wrap.left + 10))}px`;
  tooltip.style.top = `${Math.max(0, event.clientY - wrap.top - 50)}px`;
});
$("#forecast-chart").addEventListener("pointerleave", () => { $("#chart-tooltip").hidden = true; });
loadRuns().catch(errorMessage);
