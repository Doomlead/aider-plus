#!/usr/bin/env python

"""Desktop wrapper around aider's Streamlit GUI.

This keeps the existing agent/coder orchestration untouched by simply hosting the
same browser GUI in a native desktop window.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path


def _find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def launch_desktop_gui(args, write_streamlit_credentials):
    import webview
    from streamlit.web import cli

    from aider import gui

    host = "127.0.0.1"
    port = _find_free_port(host)

    write_streamlit_credentials()

    target = str(Path(gui.__file__).resolve())
    st_args = [
        "run",
        target,
        "--server.headless=true",
        f"--server.address={host}",
        f"--server.port={port}",
        "--browser.serverAddress=127.0.0.1",
        "--browser.gatherUsageStats=false",
        "--runner.magicEnabled=false",
        "--server.runOnSave=false",
        "--global.developmentMode=false",
        "--server.fileWatcherType=none",
        "--client.toolbarMode=viewer",
        "--",
    ] + args

    thread = threading.Thread(target=cli.main, args=(st_args,), daemon=True)
    thread.start()

    if not _wait_for_server(host, port):
        raise RuntimeError("Timed out waiting for the local GUI server to start")

    url = f"http://{host}:{port}"
    window = webview.create_window("Aider Desktop", url, width=1280, height=900)

    def _on_closed():
        os._exit(0)

    window.events.closed += _on_closed
    webview.start()
