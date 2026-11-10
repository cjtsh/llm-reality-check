import argparse
import os
import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .server import VERSION, app


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
parser.add_argument("--open-browser", action="store_true")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=int(os.getenv("LLM_REALITY_CHECK_PORT", os.getenv("PORT", "8765"))))
args = parser.parse_args()
port = available_port(args.port) if args.host in {"127.0.0.1", "localhost"} else args.port
url = f"http://127.0.0.1:{port}"


def open_when_ready():
    for _ in range(100):
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.2)


if args.open_browser:
    threading.Thread(target=open_when_ready, daemon=True).start()
server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port))


def request_shutdown():
    server.should_exit = True


if args.host in {"127.0.0.1", "localhost", "::1"}:
    app.state.request_shutdown = request_shutdown
print(f"LLM Reality Check v{VERSION}: {url}", flush=True)
server.run()
