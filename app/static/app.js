const $ = (id) => document.getElementById(id);
let timer = null;
let activeRun = null;
let modelOptions = [];
const metric = (value, digits = 1, unit = "") => value == null ? "—" : Number(value).toFixed(digits) + unit;
const benchmarkLabel = (version) => version?.startsWith("inspect-") ? version : `legacy rubric v${version}`;
$("score").parentElement.querySelector("small").textContent = "Raw benchmark score / 100";

const duration = (seconds) => {
  const whole = Math.max(0, Math.floor(seconds));
  return whole < 60 ? `${whole}s` : `${Math.floor(whole / 60)}m ${whole % 60}s`;
};
const activityPanel = document.createElement("details");
activityPanel.hidden = true;
activityPanel.open = true;
activityPanel.innerHTML = '<summary>Recent activity</summary><ol id="activityList" style="color:var(--muted);padding-left:24px"></ol>';
$("current").parentElement.after(activityPanel);
$("eta").parentElement.querySelector("small").textContent = "Current step";
$("eta").textContent = "—";

function setState(message, live = false) {
  $("state").textContent = message;
  $("dot").className = "dot" + (live ? " live" : "");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

async function discover() {
  const endpoint = $("endpoint").value.trim();
  const key = $("key").value;
  setState("Loading models…", true);
  try {
    const response = await fetch("/api/models?endpoint=" + encodeURIComponent(endpoint) + "&api_key=" + encodeURIComponent(key));
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Provider returned HTTP ${response.status}`);
    modelOptions = payload.data.map((model) => model.id);
    renderModels(modelOptions);
    setState(`${payload.data.length} model${payload.data.length === 1 ? "" : "s"} loaded`);
  } catch (error) {
    setState("Discovery failed");
    $("note").textContent = error.message;
    $("note").style.color = "var(--danger)";
  }
}

async function startRun() {
  const response = await fetch("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: $("name").value, endpoint: $("endpoint").value.trim(), api_key: $("key").value, model: $("model").value || "demo-model", profile: $("profile").value})});
  const payload = await response.json();
  if (!response.ok) { $("note").textContent = payload.detail; return; }
  activeRun = payload.id;
  activityPanel.hidden = true;
  $("activityList").replaceChildren();
  $("score").textContent = "—";
  $("reviewTitle").textContent = "Performance review";
  $("reviewSummary").textContent = "Inspect AI is running the selected samples." ;
  $("reviewBody").innerHTML = "";
  $("run").disabled = true; $("cancel").disabled = false; $("empty").style.display = "none";
  setState("Benchmark running…", true);
  timer = setInterval(pollRun, 250);
}

async function pollRun() {
  const response = await fetch(`/api/runs/${activeRun}`).catch(() => null);
  if (!response?.ok) {
    setState("Lost connection to the dashboard. Checking again…");
    return;
  }
  const payload = await response.json();
  const finished = Math.max(payload.finished ?? 0, payload.completed);
  const percent = finished / payload.total * 100;
  $("bar").style.width = `${percent}%`;
  $("tasks").textContent = `${finished} / ${payload.total}`;
  $("elapsed").textContent = duration(payload.elapsed || 0);
  $("eta").textContent = payload.status === "running" ? duration(payload.phase_elapsed ?? 0) : "—";
  $("current").textContent = [payload.current, payload.status === "running" && payload.sample].filter(Boolean).join(" · ");
  if (payload.status === "running") setState(payload.phase || "Benchmark running…", true);
  const activity = payload.activity || [];
  activityPanel.hidden = activity.length === 0;
  $("activityList").innerHTML = activity.map((event) =>
    `<li>${duration(event.elapsed)} · ${escapeHtml(event.label)}${event.sample ? ` · ${escapeHtml(event.sample)}` : ""}</li>`
  ).join("");
  $("rate").textContent = payload.tps ? `${Number(payload.tps).toFixed(1)} tok/s` : "— tok/s";
  if (payload.score !== null && payload.score !== undefined) $("score").textContent = Number(payload.score).toFixed(1);
  if (payload.status !== "running") {
    clearInterval(timer); timer = null; $("run").disabled = false; $("cancel").disabled = true;
    setState(payload.error ? `Incomplete: ${payload.error}` : payload.current);
    if (payload.completed) fetch(`/api/runs/${activeRun}/review`).then((r) => r.json()).then(renderReview);
    loadHistory();
  }
}

async function cancelRun() {
  if (!activeRun) return;
  await fetch(`/api/runs/${activeRun}/cancel`, {method: "POST"});
  await pollRun();
}

function frontierLabel(result) {
  if (!result) return "—";
  return result.ceiling_tie ? "Ceiling tie" : metric(result.index);
}

async function loadHistory() {
  const payload = await fetch("/api/history").then((r) => r.json());
  const ref = payload.frontier_reference;
  $("frontierStatus").textContent = ref
    ? `Heavy frontier reference: ${ref.model}${ref.provider ? ` (${ref.provider})` : ""} · raw ${metric(ref.score)} → index 100.`
    : "Frontier Index is uncalibrated. Run a frontier model on Heavy v3, select its run, and set it as the reference.";
  const profileLabel = (profile) => ({"light-v1":"Light · v1", "standard-v2":"Standard · v2", "heavy-v3":"Heavy · v3"}[profile] || "Not recorded");
  $("history").innerHTML = payload.runs.map((run) => `<tr><td><input type="checkbox" class="history-check" data-id="${run.id}"></td><td>${escapeHtml(run.provider_name || "Unknown")}</td><td>${escapeHtml(run.model)}</td><td><span>${escapeHtml(profileLabel(run.profile))}</span>${run.benchmark_version ? `<small style="display:block;color:var(--muted)">${escapeHtml(benchmarkLabel(run.benchmark_version))}</small>` : ""}</td><td>${escapeHtml(run.status)}</td><td>${run.score === null ? "—" : Number(run.score).toFixed(1)}</td><td>${frontierLabel(run.frontier)}</td><td>${run.tps ? Number(run.tps).toFixed(1) : "—"}</td><td>${escapeHtml(run.created_at)}</td></tr>`).join("") || '<tr><td colspan="9">No saved runs yet</td></tr>';
}

$("discover").addEventListener("click", discover);
$("run").addEventListener("click", startRun);
$("cancel").addEventListener("click", cancelRun);
const providerPanel = document.createElement("div");
providerPanel.className = "panel";
providerPanel.innerHTML = '<h2>Saved providers</h2><div id="savedProviders" style="display:grid;gap:8px;color:var(--muted)">Loading providers…</div>';
document.querySelector(".grid").before(providerPanel);

async function loadProviders() {
  const payload = await fetch("/api/providers").then((r) => r.json());
  const target = $("savedProviders");
  target.innerHTML = payload.providers.length ? payload.providers.map((p) => `<div style="display:flex;gap:8px;align-items:center"><button class="secondary provider-load" data-id="${p.id}">${escapeHtml(p.name)}</button><span style="font-family:monospace;font-size:12px">${escapeHtml(p.endpoint)}</span><button class="secondary provider-delete" data-id="${p.id}">Delete</button></div>`).join("") : "No saved providers yet.";
  document.querySelectorAll(".provider-load").forEach((button) => button.addEventListener("click", async () => { const p = await fetch(`/api/providers/${button.dataset.id}`).then((r) => r.json()); $("name").value = p.name; $("endpoint").value = p.endpoint; $("key").value = p.api_key; setState(`Loaded ${p.name}`); }));
  document.querySelectorAll(".provider-delete").forEach((button) => button.addEventListener("click", async () => { await fetch(`/api/providers/${button.dataset.id}`, {method: "DELETE"}); loadProviders(); }));
}

const saveButton = document.createElement("button");
saveButton.className = "secondary"; saveButton.textContent = "＋ Save provider"; saveButton.style.width = "100%"; saveButton.style.marginTop = "8px";
$("run").parentElement.insertBefore(saveButton, $("run"));
saveButton.addEventListener("click", async () => { const p = await fetch("/api/providers", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: $("name").value, endpoint: $("endpoint").value, api_key: $("key").value})}).then((r) => r.json()); setState(`Saved ${p.name}`); loadProviders(); });
loadProviders();
loadHistory();

const historyPanel = document.querySelector(".history");
const historyTitle = historyPanel.querySelector("h2");
const historyActions = document.createElement("span");
historyActions.style.cssText = "float:right;display:flex;gap:8px";
historyActions.innerHTML = '<button class="secondary" id="compareSelected">Compare selected</button><button class="secondary" id="deleteSelected">Delete selected</button><button class="secondary" id="deleteAll">Delete all</button>';
historyTitle.appendChild(historyActions);
historyPanel.querySelector("thead tr").innerHTML = "<th></th><th>Provider</th><th>Model</th><th>Test profile</th><th>Status</th><th>Raw score</th><th>Frontier index</th><th>Avg TPS</th><th>Created</th>";
$("deleteSelected").addEventListener("click", async () => { const ids = [...document.querySelectorAll(".history-check:checked")].map((x) => x.dataset.id); await Promise.all(ids.map((id) => fetch(`/api/history/${id}`, {method: "DELETE"}))); loadHistory(); });
$("deleteAll").addEventListener("click", async () => { await fetch("/api/history", {method: "DELETE"}); loadHistory(); });

const frontierPanel = document.createElement("div");
frontierPanel.style.cssText = "display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:14px";
frontierPanel.innerHTML = '<button class="secondary" id="setFrontier">Set selected as frontier reference</button><span id="frontierStatus" style="color:var(--muted)"></span><span id="frontierMessage" role="status"></span>';
historyPanel.appendChild(frontierPanel);
$("setFrontier").addEventListener("click", async () => {
  const selected = [...document.querySelectorAll(".history-check:checked")];
  $("frontierMessage").textContent = "";
  if (selected.length !== 1) {
    $("frontierMessage").textContent = "Select one completed Heavy v3 run.";
    return;
  }
  try {
    const response = await fetch(`/api/runs/${selected[0].dataset.id}/frontier-reference`, {method: "POST"});
    if (!response.ok) throw new Error((await response.json()).detail);
    await loadHistory();
    $("frontierMessage").textContent = "Frontier reference saved.";
  } catch (error) {
    $("frontierMessage").textContent = error.message;
  }
});

const reviewPanel = document.createElement("section");
reviewPanel.className = "panel";
reviewPanel.style.marginTop = "20px";
reviewPanel.innerHTML = '<h2 id="reviewTitle">Performance review</h2><div id="reviewSummary" style="color:var(--muted);margin-bottom:14px">Completed runs show per-check scores, category averages, latency, and throughput here.</div><div id="reviewBody"></div>';
historyPanel.insertAdjacentElement("afterend", reviewPanel);
const reviewStyle = document.createElement("style");
reviewStyle.textContent = ".review-table{width:100%;border-collapse:collapse;font:12px ui-monospace,monospace}.review-table td,.review-table th{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid var(--line)}.review-table th{color:var(--muted);font-weight:500;white-space:nowrap}.review-table details{min-width:220px;max-width:480px;white-space:pre-wrap;overflow-wrap:anywhere}.review-table summary{cursor:pointer;color:var(--cyan)}.review-table b{display:inline-block;margin-top:8px;color:var(--muted)}";
document.head.appendChild(reviewStyle);

function renderReview(report) {
  $("reviewTitle").textContent = `Performance review · ${report.model} (${report.provider})`;
  const categoryLabel = (name) => ({"tool-use":"Agentic / tool use", "structured":"Structured output", "context":"Context recall", "uncertainty":"Uncertainty handling"}[name] || name[0].toUpperCase() + name.slice(1));
  const categories = Object.entries(report.categories || {}).map(([name, item]) => `<tr><td>${escapeHtml(categoryLabel(name))}</td><td>${Number(item.score).toFixed(1)}</td><td>${metric(item.latency_s, 2, "s")}</td><td>${metric(item.tps)}</td><td>${item.checks}</td></tr>`).join("");
  const checks = (report.checks || []).map((check) => `<tr><td>${check.ordinal}</td><td>${escapeHtml(categoryLabel(check.category))}</td><td>${Number(check.score).toFixed(1)}</td><td>${metric(check.latency_s, 2, "s")}</td><td>${metric(check.tps)}</td><td><details><summary>View prompt and response</summary><b>Prompt:</b><br>${escapeHtml(check.prompt)}<br><b>Response:</b><br>${escapeHtml(check.answer)}<br><b>Scoring source:</b> ${escapeHtml(check.note)}</details></td></tr>`).join("");
  const summary = report.benchmark_version.startsWith("inspect-")
    ? "Overall gives equal weight to reasoning, coding, and agentic performance. Speed is reported separately."
    : "This run used the original app rubric.";
  const frontier = report.frontier
    ? ` Frontier Index ${frontierLabel(report.frontier)} against ${report.frontier.reference.model}.`
    : "";
  const light = report.profile === "light-v1" ? " Light is a quick check and can reach its ceiling." : "";
  $("reviewSummary").textContent = `Raw ${metric(report.score)} / 100 · ${report.profile_label} · ${benchmarkLabel(report.benchmark_version)}. ${summary}${light}${frontier}`;
  $("reviewBody").innerHTML = `<h3>Category averages</h3><div style="overflow:auto"><table class="review-table"><thead><tr><th>Category</th><th>Score / 100</th><th>Avg latency</th><th>Avg tok/s</th><th>Checks</th></tr></thead><tbody>${categories}</tbody></table></div><h3 style="margin-top:22px">Individual checks</h3><div style="overflow:auto"><table class="review-table"><thead><tr><th>#</th><th>Category</th><th>Score / 100</th><th>Latency</th><th>tok/s</th><th>Evidence</th></tr></thead><tbody>${checks}</tbody></table></div>`;
}

async function compareSelectedRuns() {
  const ids = [...document.querySelectorAll(".history-check:checked")].map((x) => x.dataset.id);
  if (ids.length !== 2) { $("reviewTitle").textContent = "Select exactly two runs to compare"; return; }
  const reports = await Promise.all(ids.map((id) => fetch(`/api/runs/${id}/review`).then((r) => r.json())));
  const [left, right] = reports;
  if (left.status !== "complete" || right.status !== "complete" || left.profile === "legacy" || right.profile === "legacy" || left.benchmark_version === "legacy" || right.benchmark_version === "legacy" || left.profile !== right.profile || left.benchmark_version !== right.benchmark_version) {
    $("reviewTitle").textContent = "Runs are not directly comparable";
    $("reviewSummary").textContent = "Choose two completed runs with the same benchmark profile and version for an apples-to-apples comparison.";
    $("reviewBody").innerHTML = "";
    return;
  }
  $("reviewTitle").textContent = `Side-by-side · ${left.provider}: ${left.model} vs ${right.provider}: ${right.model}`;
  $("reviewSummary").textContent = `${left.profile_label} · ${benchmarkLabel(left.benchmark_version)} · Same profile and scoring rules.${left.frontier && right.frontier ? ` Frontier Index ${frontierLabel(left.frontier)} vs ${frontierLabel(right.frontier)} against ${left.frontier.reference.model}.` : ""}`;
  const categoryLabel = (name) => ({"tool-use":"Agentic / tool use", "structured":"Structured output", "context":"Context recall", "uncertainty":"Uncertainty handling"}[name] || name[0].toUpperCase() + name.slice(1));
  const dimensionRows = [...new Set([...Object.keys(left.categories), ...Object.keys(right.categories)])].map((name) => `<tr><td>${escapeHtml(categoryLabel(name))}</td><td>${left.categories[name] ? Number(left.categories[name].score).toFixed(1) : "—"}</td><td>${right.categories[name] ? Number(right.categories[name].score).toFixed(1) : "—"}</td><td>${left.categories[name] ? metric(left.categories[name].tps) : "—"}</td><td>${right.categories[name] ? metric(right.categories[name].tps) : "—"}</td></tr>`).join("");
  const checkRows = left.checks.map((a, index) => { const b = right.checks[index]; return `<tr><td>${a.ordinal}</td><td>${escapeHtml(categoryLabel(a.category))}</td><td>${Number(a.score).toFixed(1)}</td><td>${b ? Number(b.score).toFixed(1) : "—"}</td><td>${metric(a.latency_s, 2, "s")}</td><td>${metric(b?.latency_s, 2, "s")}</td></tr>`; }).join("");
  $("reviewBody").innerHTML = `<div class="metrics"><div class="metric"><small>${escapeHtml(left.model)} raw</small><strong>${Number(left.score).toFixed(1)}</strong></div><div class="metric"><small>${escapeHtml(right.model)} raw</small><strong>${Number(right.score).toFixed(1)}</strong></div></div><h3>Category scores and speed</h3><div style="overflow:auto"><table class="review-table"><thead><tr><th>Category</th><th>${escapeHtml(left.model)} score</th><th>${escapeHtml(right.model)} score</th><th>${escapeHtml(left.model)} tok/s</th><th>${escapeHtml(right.model)} tok/s</th></tr></thead><tbody>${dimensionRows}</tbody></table></div><h3 style="margin-top:22px">Check-by-check scores and latency</h3><div style="overflow:auto"><table class="review-table"><thead><tr><th>#</th><th>Category</th><th>${escapeHtml(left.model)} score</th><th>${escapeHtml(right.model)} score</th><th>${escapeHtml(left.model)} latency</th><th>${escapeHtml(right.model)} latency</th></tr></thead><tbody>${checkRows}</tbody></table></div>`;
}
$("compareSelected").addEventListener("click", compareSelectedRuns);

function renderModels(models) {
  $("model").innerHTML = models.map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join("") || '<option value="">No matching models</option>';
}

const searchButton = $("searchModels");
const searchInput = $("modelSearch");
searchButton.addEventListener("click", () => { searchInput.focus(); });
searchInput.addEventListener("input", () => { const query = searchInput.value.toLowerCase(); renderModels(modelOptions.filter((id) => id.toLowerCase().includes(query))); });
