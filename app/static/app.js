const $ = (id) => document.getElementById(id);
let timer = null;
let activeRun = null;
let modelOptions = [];
let providers = [];
let modelRequest = null;
let runInProgress = false;
let sessionEnded = false;
const metric = (value, digits = 1, unit = "") => value == null ? "—" : Number(value).toFixed(digits) + unit;
const benchmarkLabel = (version) => version?.startsWith("inspect-") ? version : `legacy rubric v${version}`;
$("score").parentElement.querySelector("small").textContent = "Raw benchmark score / 100";

const duration = (seconds) => {
  const whole = Math.max(0, Math.floor(seconds));
  return whole < 60 ? `${whole}s` : `${Math.floor(whole / 60)}m ${whole % 60}s`;
};
const outcomeSummary = document.createElement("p");
outcomeSummary.style.color = "var(--muted)";
outcomeSummary.hidden = true;
$("current").parentElement.after(outcomeSummary);
$("tasks").parentElement.querySelector("small").textContent = "Checks finished";

function formatOutcomes(outcomes) {
  const parts = [`${outcomes.passed} passed`, `${outcomes.failed} failed`];
  if (outcomes.partial) parts.push(`${outcomes.partial} partial`);
  if (outcomes.ungraded) parts.push(`${outcomes.ungraded} ungraded`);
  return parts.join(" · ");
}

const streamSummary = document.createElement("p");
streamSummary.style.color = "var(--muted)";
streamSummary.hidden = true;
outcomeSummary.after(streamSummary);
const streamSetting = document.createElement("label");
streamSetting.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:10px;color:var(--muted)";
streamSetting.innerHTML = '<input id="streamActivity" type="checkbox" style="width:auto"> Show live stream activity';
$("providerSelect").parentElement.after(streamSetting);
function defaultStreamSetting() {
  try {
    $("streamActivity").checked = ["localhost", "127.0.0.1", "[::1]"].includes(new URL(selectedProvider()?.endpoint ?? "").hostname);
  } catch {
    $("streamActivity").checked = false;
  }
}


const activityPanel = document.createElement("details");
activityPanel.hidden = true;
activityPanel.open = true;
activityPanel.innerHTML = '<summary>Recent activity</summary><ol id="activityList" style="color:var(--muted);padding-left:24px"></ol>';
streamSummary.after(activityPanel);
$("eta").parentElement.querySelector("small").textContent = "Current step";
$("eta").textContent = "—";

function setState(message, live = false) {
  $("state").textContent = message;
  $("dot").className = "dot" + (live ? " live" : "");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

function selectedProvider() {
  return providers.find((provider) => provider.id === $("providerSelect").value);
}

function syncRunButton() {
  $("run").disabled = runInProgress || !$("model").value || !selectedProvider();
}

function connectionMessage(message, error = false) {
  $("connectionMessage").textContent = message;
  $("connectionMessage").classList.toggle("provider-message", error);
  $("connectionMessage").hidden = !message;
}

function selectConnection() {
  modelRequest?.abort();
  modelRequest = null;
  const provider = selectedProvider();
  $("connectionEndpoint").textContent = provider?.endpoint ?? "";
  $("connectionEndpoint").title = provider?.endpoint ?? "";
  $("discover").disabled = !provider;
  $("modelSearch").value = "";
  modelOptions = [];
  $("model").replaceChildren(new Option(provider ? "Load models first" : "Choose a provider first", ""));
  defaultStreamSetting();
  connectionMessage("");
  syncRunButton();
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : `Request failed (HTTP ${response.status})`);
  }
  return payload;
}

$("quitApp")?.addEventListener("click", async () => {
  if (!window.confirm("Quit LLM Reality Check? Any running benchmark will be cancelled.")) return;
  $("quitApp").disabled = true;
  $("sessionStatus").textContent = "Quitting LLM Reality Check…";
  try {
    await requestJson("/api/app/quit", {method: "POST", headers: {"X-LLMRC-Action": "quit"}});
    sessionEnded = true;
    clearInterval(timer);
    timer = null;
    modelRequest?.abort();
    document.querySelectorAll("button, input, select").forEach((control) => { control.disabled = true; });
    setState("Local session ended");
    $("sessionStatus").textContent = "Session ended. Reopen LLM Reality Check from Applications to start again.";
  } catch (error) {
    $("sessionStatus").textContent = error.message;
    $("quitApp").disabled = false;
  }
});

async function discover() {
  const provider = selectedProvider();
  if (!provider) return;
  modelRequest?.abort();
  const request = new AbortController();
  modelRequest = request;
  $("discover").disabled = true;
  modelOptions = [];
  renderModels([]);
  connectionMessage("Loading models…");
  try {
    const payload = await requestJson(`/api/providers/${encodeURIComponent(provider.id)}/models`, {signal: request.signal});
    if (modelRequest !== request) return;
    modelOptions = payload.data.map((model) => model.id).filter(Boolean);
    $("modelSearch").value = "";
    renderModels(modelOptions);
    connectionMessage(modelOptions.length ? `${modelOptions.length} models loaded` : "This provider returned no models.");
  } catch (error) {
    if (modelRequest === request) connectionMessage(error.message, true);
  } finally {
    if (modelRequest === request) {
      modelRequest = null;
      $("discover").disabled = false;
    }
  }
}

async function startRun() {
  const provider = selectedProvider();
  const model = $("model").value;
  if (!provider || !model || runInProgress) return;
  runInProgress = true;
  syncRunButton();
  let payload;
  try {
    payload = await requestJson(`/api/providers/${encodeURIComponent(provider.id)}/runs`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model, profile: $("profile").value, stream_activity: $("streamActivity").checked}),
    });
  } catch (error) {
    runInProgress = false;
    syncRunButton();
    connectionMessage(error.message, true);
    return;
  }
  connectionMessage("");
  activeRun = payload.id;
  activityPanel.hidden = true;
  outcomeSummary.hidden = true;
  streamSummary.hidden = true;
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
  if (sessionEnded) return;
  if (!response?.ok) {
    setState("Lost connection to the dashboard. Checking again…");
    return;
  }
  const payload = await response.json();
  if (sessionEnded) return;
  const finished = Math.max(payload.finished ?? 0, payload.completed);
  const percent = finished / payload.total * 100;
  $("bar").style.width = `${percent}%`;
  $("tasks").textContent = `${finished} / ${payload.total}`;
  $("elapsed").textContent = duration(payload.elapsed || 0);
  $("eta").textContent = payload.status === "running" ? duration(payload.phase_elapsed ?? 0) : "—";
  $("current").textContent = [payload.current, payload.status === "running" && payload.sample].filter(Boolean).join(" · ");
  if (payload.status === "running") setState(payload.phase || "Benchmark running…", true);
  if (payload.outcomes) {
    outcomeSummary.textContent = formatOutcomes(payload.outcomes);
    outcomeSummary.hidden = finished === 0;
  }
  const waiting = payload.phase === "Waiting for model response";
  const streaming = payload.phase === "Receiving model output";
  streamSummary.hidden = payload.status !== "running" || (!waiting && !streaming);
  if (streaming && payload.stream_idle != null) {
    const tokens = payload.stream_tokens == null ? "" : ` · ${payload.stream_tokens} output tokens reported`;
    streamSummary.textContent = `Last stream update ${duration(payload.stream_idle)} ago${tokens}`;
  } else if (waiting) {
    streamSummary.textContent = payload.stream_enabled
      ? "Waiting for the first stream update from the model server."
      : "Buffered response. The model server reports back when the response is complete.";
  }
  const activity = payload.activity || [];
  activityPanel.hidden = activity.length === 0;
  $("activityList").innerHTML = activity.map((event) =>
    `<li>${duration(event.elapsed)} · ${escapeHtml(event.label)}${event.sample ? ` · ${escapeHtml(event.sample)}` : ""}</li>`
  ).join("");
  $("rate").textContent = payload.tps ? `${Number(payload.tps).toFixed(1)} tok/s` : "— tok/s";
  if (payload.score !== null && payload.score !== undefined) $("score").textContent = Number(payload.score).toFixed(1);
  if (payload.status !== "running") {
    clearInterval(timer); timer = null; runInProgress = false; syncRunButton(); $("cancel").disabled = true;
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
const providerPanel = document.createElement("section");
providerPanel.className = "panel providers-panel";
providerPanel.innerHTML = `
  <div class="providers-toolbar">
    <h2>Saved providers</h2>
    <div class="providers-actions">
      <input id="providerSearch" type="search" placeholder="Filter providers…" aria-label="Filter saved providers">
      <button id="addProvider" class="secondary" type="button">＋ Add provider</button>
    </div>
  </div>
  <div id="savedProviders" class="providers-grid" aria-label="Saved providers">Loading providers…</div>
  <p id="providerMessage" class="provider-message" role="status"></p>`;
document.querySelector(".grid").before(providerPanel);

const connectionStatus = document.createElement("p");
connectionStatus.id = "connectionMessage";
connectionStatus.setAttribute("role", "status");
$("modelSearch").after(connectionStatus);

const providerDialog = document.createElement("dialog");
providerDialog.className = "panel provider-dialog";
providerDialog.setAttribute("aria-labelledby", "providerDialogTitle");
providerDialog.innerHTML = `
  <form id="providerForm">
    <h2 id="providerDialogTitle"></h2>
    <div class="field"><label for="providerName">Name</label><input id="providerName" maxlength="100" required autofocus></div>
    <div class="field"><label for="providerEndpoint">OpenAI-compatible endpoint</label><input id="providerEndpoint" type="url" maxlength="500" placeholder="https://api.example.com/v1" required></div>
    <div class="field"><label for="providerKey">API key</label><input id="providerKey" type="password" autocomplete="off" aria-describedby="providerKeyHint"><small id="providerKeyHint"></small></div>
    <label id="providerClearLabel" hidden><input id="providerClearKey" type="checkbox" style="width:auto"> Remove saved API key</label>
    <p id="providerFormMessage" class="provider-message" role="alert"></p>
    <div class="provider-dialog-actions"><button id="cancelProviderEdit" type="button" class="secondary">Cancel</button><button id="saveProvider" type="submit" class="primary">Save provider</button></div>
  </form>`;
document.body.appendChild(providerDialog);
let editingProvider = null;
let providerRefresh = 0;

function renderProviders() {
  const query = $("providerSearch").value.trim().toLowerCase();
  const matches = providers.filter((p) => `${p.name} ${p.endpoint}`.toLowerCase().includes(query));
  $("savedProviders").innerHTML = matches.map((p) => `
    <article class="provider-card">
      <button class="provider-card-edit" type="button" data-edit="${escapeHtml(p.id)}" aria-label="Edit ${escapeHtml(p.name)}" title="${escapeHtml(p.endpoint)}">
        <strong>${escapeHtml(p.name)}</strong><span class="provider-endpoint">${escapeHtml(p.endpoint)}</span>
      </button>
      <button class="secondary" type="button" data-delete="${escapeHtml(p.id)}" aria-label="Delete ${escapeHtml(p.name)}">Delete</button>
    </article>`).join("") || `<p class="provider-empty">${providers.length ? "No matching providers." : "Add a provider to get started."}</p>`;
}

async function loadProviders(preferred = $("providerSelect").value, resetConnection = false) {
  const refresh = ++providerRefresh;
  try {
    const payload = await requestJson("/api/providers");
    if (refresh !== providerRefresh) return;
    const previous = $("providerSelect").value;
    providers = payload.providers;
    $("providerSelect").replaceChildren(
      new Option("Choose a saved provider", ""),
      ...providers.map((p) => new Option(p.name, p.id)),
    );
    $("providerSelect").value = providers.some((p) => p.id === preferred) ? preferred : "";
    if (resetConnection || previous !== $("providerSelect").value) selectConnection();
    renderProviders();
  } catch (error) {
    $("providerMessage").textContent = error.message;
    if (!providers.length) $("savedProviders").textContent = "Could not load saved providers.";
  }
}

function openProviderEditor(provider = null) {
  editingProvider = provider;
  $("providerForm").reset();
  $("providerDialogTitle").textContent = provider ? "Edit provider" : "Add provider";
  $("providerName").value = provider?.name ?? "";
  $("providerEndpoint").value = provider?.endpoint ?? "";
  $("providerClearLabel").hidden = !provider;
  $("providerKey").disabled = false;
  $("providerKeyHint").textContent = provider ? "Leave blank to keep the saved key." : "Optional for local servers.";
  $("providerFormMessage").textContent = "";
  providerDialog.showModal();
}

$("providerSelect").addEventListener("change", selectConnection);
$("model").addEventListener("change", syncRunButton);
$("providerSearch").addEventListener("input", renderProviders);
$("addProvider").addEventListener("click", () => openProviderEditor());
$("cancelProviderEdit").addEventListener("click", () => providerDialog.close());
providerDialog.addEventListener("cancel", (event) => {
  if ($("saveProvider").disabled) event.preventDefault();
});
$("providerClearKey").addEventListener("change", () => {
  $("providerKey").disabled = $("providerClearKey").checked;
});
$("savedProviders").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const provider = providers.find((p) => p.id === (button.dataset.edit ?? button.dataset.delete));
  if (!provider) return;
  if (button.dataset.edit) {
    openProviderEditor(provider);
    return;
  }
  if (!window.confirm(`Delete ${provider.name} from Saved providers?`)) return;
  button.disabled = true;
  $("providerMessage").textContent = "";
  try {
    await requestJson(`/api/providers/${encodeURIComponent(provider.id)}`, {method: "DELETE"});
    await loadProviders();
  } catch (error) {
    $("providerMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("providerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("saveProvider").disabled = true;
  $("cancelProviderEdit").disabled = true;
  $("providerFormMessage").textContent = "";
  const current = editingProvider;
  try {
    const saved = await requestJson("/api/providers", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        id: current?.id, name: $("providerName").value.trim(),
        endpoint: $("providerEndpoint").value.trim(), api_key: $("providerKey").disabled ? "" : $("providerKey").value,
        clear_api_key: $("providerClearKey").checked,
      }),
    });
    const selected = $("providerSelect").value;
    await loadProviders(current ? selected : selected || saved.id, current?.id === selected);
    providerDialog.close();
    $("providerForm").reset();
  } catch (error) {
    $("providerFormMessage").textContent = error.message;
  } finally {
    $("saveProvider").disabled = false;
    $("cancelProviderEdit").disabled = false;
  }
});

selectConnection();
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
frontierPanel.innerHTML = '<button class="secondary" id="reviewSelected">Review selected</button><button class="secondary" id="setFrontier">Set selected as frontier reference</button><span id="frontierStatus" style="color:var(--muted)"></span><span id="frontierMessage" role="status"></span>';
historyPanel.appendChild(frontierPanel);
$("reviewSelected").addEventListener("click", async () => {
  const selected = [...document.querySelectorAll(".history-check:checked")];
  if (selected.length !== 1) {
    $("frontierMessage").textContent = "Select one run to review.";
    return;
  }
  try {
    const response = await fetch(`/api/runs/${selected[0].dataset.id}/review`);
    if (!response.ok) throw new Error((await response.json()).detail);
    $("frontierMessage").textContent = "";
    renderReview(await response.json());
    reviewPanel.scrollIntoView({block: "start"});
  } catch (error) {
    $("frontierMessage").textContent = error.message;
  }
});
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
  const checks = (report.checks || []).map((check) => `<tr><td>${check.ordinal}</td><td>${escapeHtml(categoryLabel(check.category))}</td><td>${Number(check.score).toFixed(1)}${check.outcome ? `<br><small>${escapeHtml(check.outcome)}</small>` : ""}</td><td>${metric(check.latency_s, 2, "s")}</td><td>${metric(check.tps)}</td><td><details><summary>View prompt and response</summary><b>Prompt:</b><br>${escapeHtml(check.prompt)}<br><b>Response:</b><br>${escapeHtml(check.answer)}<br><b>Scoring source:</b> ${escapeHtml(check.note)}</details></td></tr>`).join("");
  const summary = report.benchmark_version.startsWith("inspect-")
    ? "Overall gives equal weight to reasoning, coding, and agentic performance. Speed is reported separately."
    : "This run used the original app rubric.";
  const frontier = report.frontier
    ? ` Frontier Index ${frontierLabel(report.frontier)} against ${report.frontier.reference.model}.`
    : "";
  const outcomes = report.outcomes ? ` ${formatOutcomes(report.outcomes)}.` : "";
  const light = report.profile === "light-v1" ? " Light is a quick check and can reach its ceiling." : "";
  $("reviewSummary").textContent = `Raw ${metric(report.score)} / 100 · ${report.profile_label} · ${benchmarkLabel(report.benchmark_version)}. ${summary}${outcomes}${light}${frontier}`;
  const savedActivity = (report.activity || []).map((event) =>
    `<li>${duration(event.elapsed)} · ${escapeHtml(event.label)}${event.sample ? ` · ${escapeHtml(event.sample)}` : ""}</li>`
  ).join("");
  const activityLog = savedActivity ? `<details><summary>Saved activity log</summary><ol>${savedActivity}</ol></details>` : "";
  $("reviewBody").innerHTML = `${activityLog}<h3>Category averages</h3><div style="overflow:auto"><table class="review-table"><thead><tr><th>Category</th><th>Score / 100</th><th>Avg latency</th><th>Avg tok/s</th><th>Checks</th></tr></thead><tbody>${categories}</tbody></table></div><h3 style="margin-top:22px">Individual checks</h3><div style="overflow:auto"><table class="review-table"><thead><tr><th>#</th><th>Category</th><th>Score / 100</th><th>Latency</th><th>tok/s</th><th>Evidence</th></tr></thead><tbody>${checks}</tbody></table></div>`;
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
  $("model").replaceChildren(...(models.length
    ? models.map((id) => new Option(id, id))
    : [new Option("No matching models", "")]));
  syncRunButton();
}

const searchButton = $("searchModels");
const searchInput = $("modelSearch");
searchButton.addEventListener("click", () => { searchInput.focus(); });
searchInput.addEventListener("input", () => { const query = searchInput.value.toLowerCase(); renderModels(modelOptions.filter((id) => id.toLowerCase().includes(query))); });
