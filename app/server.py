import asyncio
import os
import sqlite3
import base64
import json
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
from .benchmark import profile_tasks, score

ROOT = Path(__file__).parent
VERSION = "0.2.3"
DATA_DIR = Path(os.getenv("LLM_REALITY_CHECK_DATA_DIR", str(ROOT.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "reality-check.sqlite3"
SECRET_FILE = DATA_DIR / "credential.key"
if not SECRET_FILE.exists(): SECRET_FILE.write_bytes(Fernet.generate_key())
FERNET = Fernet(SECRET_FILE.read_bytes())
app = FastAPI(title="LLM Reality Check", version=VERSION)
RUNS: dict[str, dict[str, Any]] = {}
SECRETS: dict[str, str] = {}  # Temporary v0.1.8 memory store; Keychain is the production target.


def db():
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, endpoint TEXT, model TEXT, status TEXT, score REAL, tps REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, profile TEXT, benchmark_version TEXT)")
    for column in ("tps REAL", "profile TEXT", "benchmark_version TEXT"):
        try: conn.execute(f"ALTER TABLE runs ADD COLUMN {column}")
        except sqlite3.OperationalError: pass
    conn.execute("CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, name TEXT UNIQUE, endpoint TEXT, api_key BLOB, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS check_results (run_id TEXT, ordinal INTEGER, category TEXT, prompt TEXT, answer TEXT, score REAL, latency_s REAL, tokens INTEGER, tps REAL, note TEXT, PRIMARY KEY(run_id, ordinal))")
    conn.commit()
    return conn


def normalize_endpoint(value: str) -> str:
    """Accept a provider base, /v1, or a pasted /v1/models URL."""
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    if path.endswith("/models"):
        path = path[:-len("/models")].rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


class Provider(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=500)
    api_key: str = ""


class RunRequest(Provider):
    model: str = Field(min_length=1, max_length=200)
    profile: str = "standard-v2"


class ProviderUpdate(Provider):
    id: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/api/providers")
def list_providers():
    with db() as conn:
        rows = conn.execute("SELECT id, name, endpoint, updated_at FROM providers ORDER BY name").fetchall()
    return {"providers": [dict(zip(("id", "name", "endpoint", "updated_at"), row)) for row in rows]}


@app.post("/api/providers")
def save_provider(provider: ProviderUpdate):
    provider_id = provider.id or uuid.uuid4().hex
    encrypted = FERNET.encrypt(provider.api_key.encode()) if provider.api_key else b""
    with db() as conn:
        conn.execute("INSERT INTO providers (id,name,endpoint,api_key) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, endpoint=excluded.endpoint, api_key=CASE WHEN length(excluded.api_key)>0 THEN excluded.api_key ELSE providers.api_key END, updated_at=CURRENT_TIMESTAMP", (provider_id, provider.name, normalize_endpoint(provider.endpoint), encrypted))
    return {"id": provider_id, "name": provider.name, "endpoint": normalize_endpoint(provider.endpoint)}


@app.get("/api/providers/{provider_id}")
def get_provider(provider_id: str):
    with db() as conn:
        row = conn.execute("SELECT id,name,endpoint,api_key FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row: raise HTTPException(404, "Provider not found")
    return {"id": row[0], "name": row[1], "endpoint": row[2], "api_key": FERNET.decrypt(row[3]).decode() if row[3] else ""}


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: str):
    with db() as conn: conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
    return {"deleted": provider_id}


@app.get("/app.js")
def javascript():
    return HTMLResponse((ROOT / "static" / "app.js").read_text(), media_type="application/javascript")


@app.get("/api/models")
async def models(endpoint: str, api_key: str = ""):
    base = normalize_endpoint(endpoint)
    url = base + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=headers)
            if response.is_error:
                detail = response.text[:300].replace("\n", " ")
                raise HTTPException(response.status_code, f"Provider returned HTTP {response.status_code} from {url}: {detail}")
            payload = response.json()
        return {"endpoint": url, "data": [{"id": x.get("id", "") } for x in payload.get("data", [])]}
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Model discovery failed for {url}: {exc}") from exc


@app.post("/api/runs")
async def start_run(request: RunRequest):
    endpoint = normalize_endpoint(request.endpoint)
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise HTTPException(422, "Endpoint must be a complete URL beginning with http:// or https://")
    run_id = uuid.uuid4().hex
    tasks = profile_tasks(request.profile)
    RUNS[run_id] = {"id": run_id, "status": "running", "completed": 0, "total": len(tasks), "started": time.time(), "model": request.model, "profile": request.profile, "benchmark_version": VERSION, "score": None, "categories": {}, "checks": [], "current": "Starting", "tokens": 0, "tps": None}
    SECRETS[run_id] = request.api_key
    with db() as conn:
        conn.execute("INSERT INTO runs (id, endpoint, model, status, profile, benchmark_version) VALUES (?, ?, ?, ?, ?, ?)", (run_id, endpoint, request.model, "running", request.profile, VERSION))
    asyncio.create_task(run_benchmark(run_id, endpoint, request.model, request.api_key, request.profile))
    return RUNS[run_id]


async def run_benchmark(run_id: str, endpoint: str, model: str, api_key: str, profile: str):
    """Execute the same complete profile for every endpoint.

    Inspect AI is installed and remains the orchestration backend target; this
    first adapter uses the standard OpenAI-compatible transport directly so it
    works with local servers before adding sandboxed agent tasks.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    tasks = profile_tasks(profile)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            for index, (category, prompt, expected) in enumerate(tasks):
                if run_id not in RUNS or RUNS[run_id]["status"] == "cancelled":
                    return
                RUNS[run_id]["current"] = category
                started = time.perf_counter()
                task_tokens = 0
                first_answer = ""
                tool_info = None
                messages = [{"role": "user", "content": prompt}]
                body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 300}

                if category == "tool-use":
                    expected_tool = "read_file" if "read_file" in prompt else "run_tests"
                    if expected_tool == "read_file":
                        function = {"name": expected_tool, "description": "Read the named benchmark fixture file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}
                    else:
                        function = {"name": expected_tool, "description": "Run the focused test suite for a package.", "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"], "additionalProperties": False}}
                    body["tools"] = [{"type": "function", "function": function}]

                response = await client.post(endpoint.rstrip("/") + "/chat/completions", headers=headers, json=body)
                if category == "tool-use" and response.is_error:
                    expected_tool = "read_file" if "read_file" in prompt else "run_tests"
                    first_answer = response.text[:1000]
                    answer = first_answer
                    tool_info = {"name": "", "arguments": {}, "expected_tool": expected_tool}
                    payload = {}
                    message = {}
                else:
                    response.raise_for_status()
                    payload = response.json()
                    message = payload["choices"][0]["message"]
                    first_answer = message.get("content") or ""
                    usage = payload.get("usage") or {}
                    task_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

                if category == "context":
                    messages.extend([{"role": "assistant", "content": first_answer}, {"role": "user", "content": "What exact token did I ask you to remember? Reply with the token only."}])
                    response = await client.post(endpoint.rstrip("/") + "/chat/completions", headers=headers, json={"model": model, "messages": messages, "temperature": 0, "max_tokens": 80})
                    response.raise_for_status()
                    payload = response.json()
                    answer = payload["choices"][0]["message"].get("content", "") or ""
                    usage = payload.get("usage") or {}
                    task_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                elif category == "tool-use" and not response.is_error:
                    calls = message.get("tool_calls") or []
                    if calls:
                        call = calls[0]
                        function_call = call.get("function") or {}
                        try:
                            arguments = json.loads(function_call.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            arguments = {}
                        expected_tool = "read_file" if "read_file" in prompt else "run_tests"
                        tool_info = {"name": function_call.get("name", ""), "arguments": arguments, "expected_tool": expected_tool}
                        if tool_info["name"] == expected_tool:
                            fixture = '{"status":"ready"}' if expected_tool == "read_file" else '{"result":"passed"}'
                            messages.extend([message, {"role": "tool", "tool_call_id": call.get("id", "benchmark-call"), "content": fixture}, {"role": "user", "content": "Report the relevant value from the tool result in one short sentence."}])
                            response = await client.post(endpoint.rstrip("/") + "/chat/completions", headers=headers, json={"model": model, "messages": messages, "temperature": 0, "max_tokens": 100})
                            response.raise_for_status()
                            payload = response.json()
                            answer = payload["choices"][0]["message"].get("content", "") or ""
                            usage = payload.get("usage") or {}
                            task_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                        else:
                            answer = first_answer
                    else:
                        answer = first_answer
                        tool_info = {"name": "", "arguments": {}, "expected_tool": "read_file" if "read_file" in prompt else "run_tests"}
                else:
                    answer = first_answer

                elapsed_s = max(time.perf_counter() - started, 0.001)
                points, note = score(category, expected, answer, prompt, first_answer, tool_info)
                check = {"ordinal": index + 1, "category": category, "prompt": prompt, "answer": answer[:8000], "score": round(float(points), 1), "latency_s": round(elapsed_s, 2), "tokens": task_tokens, "tps": round(task_tokens / elapsed_s, 1), "note": note}
                RUNS[run_id]["checks"].append(check)
                RUNS[run_id]["tokens"] += task_tokens
                RUNS[run_id]["tps"] = round(RUNS[run_id]["tokens"] / max(time.time() - RUNS[run_id]["started"], 0.001), 1)
                RUNS[run_id]["completed"] = index + 1
                completed_checks = RUNS[run_id]["checks"]
                category_checks = [item["score"] for item in completed_checks if item["category"] == category]
                RUNS[run_id]["categories"][category] = round(sum(category_checks) / len(category_checks), 1)
                RUNS[run_id]["score"] = round(sum(item["score"] for item in completed_checks) / len(completed_checks), 1)
                with db() as conn:
                    conn.execute("INSERT OR REPLACE INTO check_results (run_id, ordinal, category, prompt, answer, score, latency_s, tokens, tps, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (run_id, check["ordinal"], category, prompt, check["answer"], check["score"], check["latency_s"], task_tokens, check["tps"], note))
        final_score = round(sum(item["score"] for item in RUNS[run_id]["checks"]) / len(tasks), 1)
        RUNS[run_id].update(status="complete", score=final_score, elapsed=round(time.time() - RUNS[run_id]["started"], 2), current="Complete")
        with db() as conn:
            conn.execute("UPDATE runs SET status=?, score=?, tps=? WHERE id=?", ("complete", final_score, RUNS[run_id]["tps"], run_id))
    except Exception as exc:
        RUNS[run_id].update(status="failed", error=str(exc), elapsed=round(time.time() - RUNS[run_id]["started"], 2))
        with db() as conn:
            conn.execute("UPDATE runs SET status=? WHERE id=?", ("failed", run_id))
@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "Run not found in active session")
    data = {key: value for key, value in RUNS[run_id].items() if key != "checks"}
    data["elapsed"] = round(time.time() - data["started"], 1) if data["status"] == "running" else data.get("elapsed", 0)
    return data


@app.get("/api/runs/{run_id}/review")
def run_review(run_id: str):
    with db() as conn:
        run = conn.execute("SELECT model, profile, benchmark_version, score FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run not found")
        rows = conn.execute("SELECT ordinal, category, prompt, answer, score, latency_s, tokens, tps, note FROM check_results WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
    checks = [dict(zip(("ordinal", "category", "prompt", "answer", "score", "latency_s", "tokens", "tps", "note"), row)) for row in rows]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        grouped.setdefault(check["category"], []).append(check)
    categories = {
        category: {
            "score": round(sum(x["score"] for x in items) / len(items), 1),
            "latency_s": round(sum(x["latency_s"] for x in items) / len(items), 2),
            "tps": round(sum(x["tps"] for x in items) / len(items), 1),
            "checks": len(items),
        }
        for category, items in grouped.items()
    }
    return {"model": run[0], "profile": run[1] or "legacy", "benchmark_version": run[2] or "legacy", "score": run[3], "categories": categories, "checks": checks}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "Run not found")
    RUNS[run_id]["status"] = "cancelled"
    SECRETS.pop(run_id, None)
    return RUNS[run_id]


@app.get("/api/history")
def history():
    with db() as conn:
        rows = conn.execute("SELECT id, endpoint, model, status, score, tps, created_at, profile, benchmark_version FROM runs ORDER BY created_at DESC LIMIT 50").fetchall()
    return {"runs": [dict(zip(("id", "endpoint", "model", "status", "score", "tps", "created_at", "profile", "benchmark_version"), row)) for row in rows]}


@app.delete("/api/history")
def clear_history():
    with db() as conn:
        conn.execute("DELETE FROM check_results")
        conn.execute("DELETE FROM runs")
    return {"deleted": "all"}


@app.delete("/api/history/{run_id}")
def delete_history_item(run_id: str):
    with db() as conn:
        conn.execute("DELETE FROM check_results WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return {"deleted": run_id}


@app.get("/", response_class=HTMLResponse)
def index():
    page = (ROOT / "static" / "index.html").read_text().replace("v0.1.0", f"v{VERSION}")
    page = page.replace("<select><option>Developer & Agent · v1</option></select>", '<select id="profile"><option value="standard-v2">Standard · v2 (40 checks)</option><option value="light-v1">Light · v1 (10 checks)</option><option value="heavy-v3">Heavy · v3 (60 checks)</option></select>')
    page = page.replace('<button class="secondary" id="discover">↻ Load models</button>', '<div style="display:flex;gap:8px;align-items:center"><button class="secondary" id="discover">↻ Load models</button><button class="secondary" id="searchModels">⌕ Search models</button></div><input id="modelSearch" placeholder="Filter models by name…" style="display:block;margin-top:8px">')
    start, end = page.index("<script>"), page.index("</script>") + len("</script>")
    return page[:start] + '<script src="/app.js"></script>' + page[end:]
