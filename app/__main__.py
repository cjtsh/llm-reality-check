import argparse
import os
import socket

import uvicorn


def available_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No available port found near {preferred}")


parser = argparse.ArgumentParser(description="LLM Reality Check local server")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=int(os.getenv("LLM_REALITY_CHECK_PORT", os.getenv("PORT", "8765"))))
args = parser.parse_args()
port = available_port(args.port) if args.host in {"127.0.0.1", "localhost"} else args.port
print(f"LLM Reality Check v0.2.4: http://127.0.0.1:{port}", flush=True)
uvicorn.run("app.server:app", host=args.host, port=port)
