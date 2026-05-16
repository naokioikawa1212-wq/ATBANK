"""
Desktop entry point — starts FastAPI in a background thread then opens
a native window via pywebview.
"""
import os
import socket
import sys
import threading
import time

# When run directly (not frozen), ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

import uvicorn
import webview
from main import app  # noqa: E402  (backend/main.py)


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_for_server(port):
        print("ERROR: バックエンドサーバーの起動に失敗しました", file=sys.stderr)
        sys.exit(1)

    webview.create_window(
        title="Bike Restore Shorts Generator",
        url=f"http://127.0.0.1:{port}",
        width=900,
        height=780,
        min_size=(640, 520),
        text_select=False,
    )
    webview.start()

    # Shut down server gracefully after the window closes
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
