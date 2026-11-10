import asyncio
import os
import sqlite3
import base64
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
VERSION = "0.2.1"
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
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, endpoint TEXT, model TEXT, status TEXT, score REAL, tps REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    try: conn.execute("ALTER TABLE runs ADD COLUMN tps REAL")
    except sqlite3.OperationalError: pass
    conn.execute("CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, name TEXT UNIQUE, endpoint TEXT, api_key BLOB, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
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
    RUNS[run_id] = {"id": run_id, "status": "running", "completed": 0, "total": len(tasks), "started": time.time(), "model": request.model, "profile": request.profile, "score": None, "categories": {}, "current": "Starting", "tokens": 0, "tps": None}
    SECRETS[run_id] = request.api_key
    with db() as conn:
        conn.execute("INSERT INTO runs (id, endpoint, model, status) VALUES (?, ?, ?, ?)", (run_id, endpoint, request.model, "running"))
    asyncio.create_task(run_benchmark(run_id, endpoint, request.model, request.api_key, request.profile))
    return RUNS[run_id]


async def run_benchmark(run_id: str, endpoint: str, model: str, api_key: str, profile: str):
    """Execute the same complete profile for every endpoint.

    Inspect AI is installed and remains the orchestration backend target; this
    first adapter uses the standard OpenAI-compatible transport directly so it
    works with local servers before adding sandboxed agent tasks.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    results = []
    tasks = profile_tasks(profile)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            for index, (category, prompt, expected) in enumerate(tasks):
                if run_id not in RUNS or RUNS[run_id]["status"] == "cancelled":
                    return
                RUNS[run_id]["current"] = category
                response = await client.post(endpoint.rstrip("/") + "/chat/completions", headers=headers, json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 300})
                response.raise_for_status()
                payload = response.json()
                answer = payload["choices"][0]["message"].get("content", "")
                points = score(category, expected, answer)
                results.append((category, points))
                usage = payload.get("usage") or {}
                tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                RUNS[run_id]["tokens"] += int(tokens or 0)
                RUNS[run_id]["tps"] = round(RUNS[run_id]["tokens"] / max(time.time() - RUNS[run_id]["started"], 0.001), 1)
                RUNS[run_id]["completed"] = index + 1
                RUNS[run_id]["categories"][category] = round(points * 100, 1)
        final_score = sum(points for _, points in results) / len(tasks) * 100
        RUNS[run_id].update(status="complete", score=round(final_score, 1), elapsed=round(time.time() - RUNS[run_id]["started"], 2), current="Complete")
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
    data = dict(RUNS[run_id])
    data["elapsed"] = round(time.time() - data["started"], 1) if data["status"] == "running" else data.get("elapsed", 0)
    return data


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
        rows = conn.execute("SELECT id, endpoint, model, status, score, tps, created_at FROM runs ORDER BY created_at DESC LIMIT 50").fetchall()
    return {"runs": [dict(zip(("id", "endpoint", "model", "status", "score", "tps", "created_at"), row)) for row in rows]}


@app.delete("/api/history")
def clear_history():
    with db() as conn: conn.execute("DELETE FROM runs")
    return {"deleted": "all"}


@app.delete("/api/history/{run_id}")
def delete_history_item(run_id: str):
    with db() as conn: conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return {"deleted": run_id}


@app.get("/", response_class=HTMLResponse)
def index():
    page = (ROOT / "static" / "index.html").read_text().replace("v0.1.0", f"v{VERSION}")
    page = page.replace("<select><option>Developer & Agent · v1</option></select>", '<select id="profile"><option value="standard-v2">Standard · v2 (40 checks)</option><option value="light-v1">Light · v1 (10 checks)</option><option value="heavy-v3">Heavy · v3 (60 checks)</option></select>')
    page = page.replace('<button class="secondary" id="discover">↻ Load models</button>', '<div style="display:flex;gap:8px;align-items:center"><button class="secondary" id="discover">↻ Load models</button><button class="secondary" id="searchModels">⌕ Search models</button></div><input id="modelSearch" placeholder="Filter models by name…" style="display:block;margin-top:8px">')
    start, end = page.index("<script>"), page.index("</script>") + len("</script>")
    return page[:start] + '<script src="/app.js"></script>' + page[end:]
