"""
A real uvicorn server in a background thread, for tests where requests have
to overlap. TestClient runs requests one at a time, so it can't show races
between FastAPI's worker threads or contention for pooled DB connections
(docs/tasks/testing.md, section 7).
"""

import socket
import threading
import time

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(app):
    """Base URL of the app served by uvicorn. Same process and app object,
    so monkeypatching and dependency_overrides apply to it."""
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("live server didn't start")
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
