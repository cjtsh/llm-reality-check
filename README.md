# LLM Reality Check

Practical, reproducible benchmarking for local and cloud LLMs.

Version 0.3.0 is an Apple Silicon-first local web application prototype. It runs a localhost dashboard, discovers models from OpenAI-compatible endpoints, stores run metadata in SQLite, and keeps API keys out of the repository.

## Benchmark scoring

Inspect AI runs published evaluations from Inspect Evals. The profiles pin upstream sample IDs and dependency versions. Light v1 has 10 samples, Standard v2 has 30, and Heavy v3 has 50.

- Reasoning uses GPQA Diamond.
- Coding uses HumanEval and grades generated Python in Docker.
- Agentic and tool use uses BFCL, with multi-turn samples in Standard and Heavy. Heavy also includes sandboxed AgentBench OS tasks.

The dashboard averages the upstream sample scores within each capability and gives the three capability averages equal weight in the overall 0–100 score. Latency and tokens per second are reported separately. Missing or failed evaluation groups leave the overall score incomplete. Compare completed runs on the same profile and benchmark version. Pre-Inspect history retains its original scores and version.

Start Docker with Compose before running a full profile. Heavy also requires Docker image builds and at least four CPUs available to the Docker VM. The first run downloads upstream datasets and sandbox images. Use the native Mac app for the sandboxed profiles. The dashboard-only Docker Compose setup does not mount a host Docker socket.

## Local development

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python -m app --port 8765
```

Then open <http://127.0.0.1:8765>.

The server automatically selects the next available port when the preferred port is occupied. Override it with `--port`, `LLM_REALITY_CHECK_PORT`, or `PORT`.

## Docker

```bash
docker compose up --build
```

The host port is configurable through `LLM_REALITY_CHECK_PORT`; the container port remains internal and fixed. Local model servers on macOS are reachable from the container at `http://host.docker.internal:<port>/v1`.

## Security

Saved provider credentials are encrypted in the local SQLite database with a local key. Benchmark credentials are passed to the Inspect worker in memory. Never commit `.env`, API keys, benchmark exports, or local databases.

## License

MIT. See `LICENSE` and `DISCLAIMER.md`.
