const $ = (id) => document.getElementById(id);
let timer = null;
let activeRun = null;
let modelOptions = [];

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
  $("run").disabled = true; $("cancel").disabled = false; $("empty").style.display = "none";
  setState("Benchmark running…", true);
  timer = setInterval(pollRun, 250);
}

async function pollRun() {
  const payload = await fetch(`/api/runs/${activeRun}`).then((r) => r.json());
  const percent = payload.completed / payload.total * 100;
  $("bar").style.width = `${percent}%`;
  $("tasks").textContent = `${payload.completed} / ${payload.total}`;
  $("elapsed").textContent = `${(payload.elapsed || 0).toFixed(1)}s`;
  $("eta").textContent = payload.completed ? `${Math.max(0, (payload.elapsed / payload.completed) * (payload.total - payload.completed)).toFixed(1)}s` : "calculating";
  $("current").textContent = payload.current || payload.status;
  $("rate").textContent = payload.tps ? `${Number(payload.tps).toFixed(1)} tok/s` : "— tok/s";
  if (payload.score !== null && payload.score !== undefined) $("score").textContent = Number(payload.score).toFixed(1);
  if (payload.status !== "running") { clearInterval(timer); timer = null; $("run").disabled = false; $("cancel").disabled = true; setState(payload.status === "complete" ? "Complete" : `Failed: ${payload.error || payload.status}`); loadHistory(); }
}

async function cancelRun() {
  if (!activeRun) return;
  await fetch(`/api/runs/${activeRun}/cancel`, {method: "POST"});
  clearInterval(timer); timer = null; setState("Cancelled"); $("run").disabled = false; $("cancel").disabled = true;
}

async function loadHistory() {
  const payload = await fetch("/api/history").then((r) => r.json());
  $("history").innerHTML = payload.runs.map((run) => `<tr><td><input type="checkbox" class="history-check" data-id="${run.id}"></td><td>${escapeHtml(run.model)}</td><td>${escapeHtml(run.status)}</td><td>${run.score ?? "—"}</td><td>${run.tps ? Number(run.tps).toFixed(1) : "—"}</td><td>${escapeHtml(run.created_at)}</td></tr>`).join("") || '<tr><td colspan="6">No saved runs yet</td></tr>';
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
historyActions.innerHTML = '<button class="secondary" id="deleteSelected">Delete selected</button><button class="secondary" id="deleteAll">Delete all</button>';
historyTitle.appendChild(historyActions);
historyPanel.querySelector("thead tr").innerHTML = "<th></th><th>Model</th><th>Status</th><th>Score</th><th>Avg TPS</th><th>Created</th>";
$("deleteSelected").addEventListener("click", async () => { const ids = [...document.querySelectorAll(".history-check:checked")].map((x) => x.dataset.id); await Promise.all(ids.map((id) => fetch(`/api/history/${id}`, {method: "DELETE"}))); loadHistory(); });
$("deleteAll").addEventListener("click", async () => { await fetch("/api/history", {method: "DELETE"}); loadHistory(); });

function renderModels(models) {
  $("model").innerHTML = models.map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join("") || '<option value="">No matching models</option>';
}

const searchButton = $("searchModels");
const searchInput = $("modelSearch");
searchButton.addEventListener("click", () => { searchInput.focus(); });
searchInput.addEventListener("input", () => { const query = searchInput.value.toLowerCase(); renderModels(modelOptions.filter((id) => id.toLowerCase().includes(query))); });
