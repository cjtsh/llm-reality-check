# LLM Reality Check

Practical, reproducible benchmarking for local and cloud LLMs.

Version 0.1.1 is an Apple Silicon-first local web application prototype. It runs a localhost dashboard, discovers models from OpenAI-compatible endpoints, stores run metadata in SQLite, and keeps API keys out of the repository.

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

Credentials are held in process memory in this prototype and are never written to SQLite, logs, or files. The production macOS app will use Keychain Services. Never commit `.env`, API keys, benchmark exports, or local databases.

## License

MIT. See `LICENSE` and `DISCLAIMER.md`.
