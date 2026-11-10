import asyncio
import os
import sqlite3
import signal
import sys
import json
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
from .benchmark import BENCHMARK_VERSION, PROFILES, check_outcome, profile_tasks

ROOT = Path(__file__).parent
VERSION = "0.3.2"
DATA_DIR = Path(os.getenv("LLM_REALITY_CHECK_DATA_DIR", str(ROOT.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "reality-check.sqlite3"
SECRET_FILE = DATA_DIR / "credential.key"
if not SECRET_FILE.exists(): SECRET_FILE.write_bytes(Fernet.generate_key())
FERNET = Fernet(SECRET_FILE.read_bytes())
app = FastAPI(title="LLM Reality Check", version=VERSION)
RUNS: dict[str, dict[str, Any]] = {}
WORKERS: dict[str, asyncio.subprocess.Process] = {}


def db():
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, endpoint TEXT, model TEXT, status TEXT, score REAL, tps REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, profile TEXT, benchmark_version TEXT, provider_name TEXT)")
    for column in ("tps REAL", "profile TEXT", "benchmark_version TEXT", "provider_name TEXT"):
        try: conn.execute(f"ALTER TABLE runs ADD COLUMN {column}")
        except sqlite3.OperationalError: pass
    conn.execute("CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, name TEXT UNIQUE, endpoint TEXT, api_key BLOB, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS check_results (run_id TEXT, ordinal INTEGER, category TEXT, prompt TEXT, answer TEXT, score REAL, latency_s REAL, tokens INTEGER, tps REAL, note TEXT, PRIMARY KEY(run_id, ordinal))")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS frontier_references (
            benchmark_version TEXT PRIMARY KEY, run_id TEXT NOT NULL,
            model TEXT NOT NULL, provider TEXT, score REAL NOT NULL CHECK(score > 0)
        )
    """)
    conn.execute("UPDATE runs SET provider_name=(SELECT name FROM providers WHERE providers.endpoint=runs.endpoint ORDER BY updated_at DESC LIMIT 1) WHERE provider_name IS NULL")
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
    if request.profile not in PROFILES:
        raise HTTPException(422, "Unknown benchmark profile")
    if any(run["status"] == "running" for run in RUNS.values()):
        raise HTTPException(409, "Wait for the current benchmark to finish")
    run_id = uuid.uuid4().hex
    total = sum(len(task.ids) for task in profile_tasks(request.profile))
    run = {
        "id": run_id, "status": "running", "completed": 0, "total": total,
        "started": time.monotonic(), "model": request.model, "profile": request.profile,
        "benchmark_version": BENCHMARK_VERSION, "score": None, "current": "Starting Inspect AI",
        "tps": None, "cancel_requested": False,
        "finished": 0, "outcomes": dict.fromkeys(("passed", "failed", "partial", "ungraded"), 0),
        "activity": [], "phase": "Starting Inspect AI",
        "phase_started": time.monotonic(), "sample": None,
    }
    with db() as conn:
        conn.execute(
            "INSERT INTO runs (id, endpoint, model, status, profile, benchmark_version, provider_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, endpoint, request.model, "running", request.profile, BENCHMARK_VERSION, request.name.strip()),
        )
    RUNS[run_id] = run
    asyncio.create_task(run_benchmark(run_id, endpoint, request))
    return run


def average(values):
    measured = [value for value in values if value is not None]
    return mean(measured) if measured else None


async def run_benchmark(run_id: str, endpoint: str, request: RunRequest):
    run = RUNS[run_id]
    checks = []
    measured_speeds = []
    issues = []
    worker = None
    try:
        worker = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.inspect_worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=ROOT.parent, limit=4 * 1024 * 1024,
        )
        WORKERS[run_id] = worker
        if run["cancel_requested"]:
            worker.terminate()
        else:
            worker.stdin.write(json.dumps({
                "endpoint": endpoint, "model": request.model,
                "api_key": request.api_key, "profile": request.profile,
            }).encode())
            await worker.stdin.drain()
        worker.stdin.close()
        async for line in worker.stdout:
            if not line.startswith(b"LRC ") or run["cancel_requested"]:
                continue
            event = json.loads(line[4:])
            if "current" in event:
                run["current"] = f"Inspect AI · {event['current']}"
                run["sample"] = None
            if "sample" in event:
                run["sample"] = event["sample"]
            if event.get("finished"):
                run["finished"] += 1
                run["outcomes"][event["outcome"]] += 1
                if event.get("tps") is not None:
                    measured_speeds.append(event["tps"])
                    run["tps"] = mean(measured_speeds)
            if "issue" in event:
                issues.append(event["issue"])
            if "phase" in event or "issue" in event:
                now = time.monotonic()
                run.update(phase=event.get("phase") or event["issue"], phase_started=now)
                label = run["phase"]
                if event.get("finished") and event.get("duration") is not None:
                    label += f" in {event['duration']:.1f}s"
                run["activity"] = (run["activity"] + [{
                    "elapsed": round(now - run["started"]),
                    "label": label,
                    "sample": run["sample"],
                }])[-8:]
            for check in event.get("checks", []):
                checks.append(check)
                with db() as conn:
                    conn.execute(
                        "INSERT INTO check_results (run_id, ordinal, category, prompt, answer, score, latency_s, tokens, tps, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, len(checks), check["category"], check["prompt"], check["answer"],
                         check["score"], check["latency_s"], check["tokens"], check["tps"], check["note"]),
                    )
            run["completed"] = len(checks)
        if await worker.wait() != 0 and not run["cancel_requested"]:
            issues.append("The Inspect worker exited before finishing.")
    except Exception:
        issues.append("The dashboard could not finish the Inspect run.")
    finally:
        if worker and worker.returncode is None:
            worker.terminate()
            await worker.wait()
        WORKERS.pop(run_id, None)
        if run["cancel_requested"]:
            status = "cancelled"
        elif not issues and len(checks) == run["total"]:
            status = "complete"
            category_means = [
                mean(check["score"] for check in checks if check["category"] == category)
                for category in ("reasoning", "coding", "tool-use")
            ]
            run["score"] = round(mean(category_means), 1)
        else:
            status = "incomplete"
        run.update(
            status=status, tps=average(check["tps"] for check in checks), current=status.capitalize(), error=" ".join(issues),
            elapsed=round(time.monotonic() - run["started"], 2),
        )
        with db() as conn:
            conn.execute(
                "UPDATE runs SET status=?, score=?, tps=? WHERE id=?",
                (status, run["score"], run["tps"], run_id),
            )


@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "Run not found in active session")
    data = dict(RUNS[run_id])
    data["elapsed"] = round(time.monotonic() - data["started"], 1) if data["status"] == "running" else data.get("elapsed", 0)
    data["phase_elapsed"] = round(time.monotonic() - data["phase_started"], 1)
    return data


def frontier_references(conn):
    rows = conn.execute("SELECT benchmark_version, run_id, model, provider, score FROM frontier_references")
    return {
        version: {"run_id": run_id, "model": model, "provider": provider, "score": score}
        for version, run_id, model, provider, score in rows
    }


def frontier_result(run_id, profile, status, score, reference):
    if profile != "heavy-v3" or status != "complete" or score is None or not reference:
        return None
    ceiling_tie = score == reference["score"] == 100 and run_id != reference["run_id"]
    return {
        "index": None if ceiling_tie else round(100 * score / reference["score"], 1),
        "ceiling_tie": ceiling_tie,
        "reference": reference,
    }


@app.post("/api/runs/{run_id}/frontier-reference")
def set_frontier_reference(run_id: str):
    with db() as conn:
        # Keep selection and the saved snapshot in one write transaction.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT profile, status, benchmark_version, model, provider_name, score FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run not found")
        profile, status, version, model, provider, score = row
        if profile != "heavy-v3" or status != "complete" or not version or not version.startswith("inspect-") or not score:
            raise HTTPException(422, "Select a completed Inspect Heavy v3 run with a nonzero score.")
        conn.execute("""
            INSERT INTO frontier_references (benchmark_version, run_id, model, provider, score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(benchmark_version) DO UPDATE SET
                run_id=excluded.run_id, model=excluded.model, provider=excluded.provider, score=excluded.score
        """, (version, run_id, model, provider, score))
    return {"model": model, "provider": provider, "score": score}


@app.get("/api/runs/{run_id}/review")
def run_review(run_id: str):
    with db() as conn:
        run = conn.execute("SELECT model, provider_name, profile, benchmark_version, score, status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run not found")
        reference = frontier_references(conn).get(run[3])
        rows = conn.execute("SELECT ordinal, category, prompt, answer, score, latency_s, tokens, tps, note FROM check_results WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
    checks = [dict(zip(("ordinal", "category", "prompt", "answer", "score", "latency_s", "tokens", "tps", "note"), row)) for row in rows]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        grouped.setdefault(check["category"], []).append(check)
    categories = {
        category: {
            "score": round(sum(x["score"] for x in items) / len(items), 1),
            "latency_s": average(x["latency_s"] for x in items),
            "tps": average(x["tps"] for x in items),
            "checks": len(items),
        }
        for category, items in grouped.items()
    }
    profile = run[2]
    outcomes = None
    if profile in PROFILES and (run[3] or "").startswith("inspect-"):
        outcomes = dict.fromkeys(("passed", "failed", "partial", "ungraded"), 0)
        for check in checks:
            check["outcome"] = check_outcome(check["score"])
            outcomes[check["outcome"]] += 1
        outcomes["ungraded"] += sum(len(task.ids) for task in profile_tasks(profile)) - len(checks)
    return {"model": run[0], "provider": run[1] or "Unknown", "profile": profile or "legacy", "profile_label": PROFILES.get(profile, "Not recorded"), "benchmark_version": run[3] or "legacy", "score": run[4], "status": run[5], "frontier": frontier_result(run_id, profile, run[5], run[4], reference), "categories": categories, "checks": checks, "outcomes": outcomes}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] == "running" and not run["cancel_requested"]:
        run.update(cancel_requested=True, current="Stopping Inspect AI", phase="Stopping Inspect AI", phase_started=time.monotonic())
        worker = WORKERS.get(run_id)
        if worker and worker.returncode is None:
            try:
                worker.send_signal(signal.SIGINT)
                await asyncio.wait_for(worker.wait(), timeout=15)
            except TimeoutError:
                worker.kill()
                await worker.wait()
            except ProcessLookupError:
                pass
    return run


@app.get("/api/history")
def history():
    with db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, endpoint, provider_name, model, status, score, tps, created_at, profile, benchmark_version FROM runs ORDER BY created_at DESC LIMIT 50").fetchall()
        references = frontier_references(conn)
    runs = [dict(row) for row in rows]
    for run in runs:
        run["frontier"] = frontier_result(
            run["id"], run["profile"], run["status"], run["score"], references.get(run["benchmark_version"]),
        )
    return {"runs": runs, "frontier_reference": references.get(BENCHMARK_VERSION)}


@app.delete("/api/history")
def clear_history():
    if any(run["status"] == "running" for run in RUNS.values()):
        raise HTTPException(409, "Wait for the current benchmark to finish")
    with db() as conn:
        conn.execute("DELETE FROM check_results")
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM frontier_references")
    return {"deleted": "all"}


@app.delete("/api/history/{run_id}")
def delete_history_item(run_id: str):
    if RUNS.get(run_id, {}).get("status") == "running":
        raise HTTPException(409, "Wait for this benchmark to finish")
    with db() as conn:
        conn.execute("DELETE FROM check_results WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return {"deleted": run_id}


@app.get("/", response_class=HTMLResponse)
def index():
    page = (ROOT / "static" / "index.html").read_text().replace("v0.1.0", f"v{VERSION}")
    options = "".join(
        f'<option value="{profile}">{PROFILES[profile]} ({sum(len(task.ids) for task in profile_tasks(profile))} checks)</option>'
        for profile in ("standard-v2", "light-v1", "heavy-v3")
    )
    page = page.replace("<select><option>Developer & Agent · v1</option></select>", f'<select id="profile">{options}</select>')
    page = page.replace('<button class="secondary" id="discover">↻ Load models</button>', '<div style="display:flex;gap:8px;align-items:center"><button class="secondary" id="discover">↻ Load models</button><button class="secondary" id="searchModels">⌕ Search models</button></div><input id="modelSearch" placeholder="Filter models by name…" style="display:block;margin-top:8px">')
    start, end = page.index("<script>"), page.index("</script>") + len("</script>")
    return page[:start] + '<script src="/app.js"></script>' + page[end:]
