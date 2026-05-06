#!/usr/bin/env python

"""Desktop wrapper around aider's Streamlit GUI.

This keeps the existing agent/coder orchestration untouched by simply hosting the
same browser GUI in a native desktop window.
"""

from __future__ import annotations

import atexit
import os
import platform
import socket
import subprocess
import time
from pathlib import Path

from aider.company.audit import AuditLogViewer


def render_desktop_audit_log(project_memory, limit: int = 10) -> str:
    """Render recent company audit events for desktop debugging panels."""
    return AuditLogViewer.from_project_memory(project_memory).render_text(limit=limit)


DEFAULT_WINDOW_SIZE = (1400, 900)
MIN_WINDOW_SIZE = (1200, 800)


def _find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _find_desktop_icon() -> str | None:
    root = Path(__file__).resolve().parent
    icons = root / "website" / "assets" / "icons"
    system = platform.system()

    candidates = []
    if system == "Windows":
        candidates.extend([icons / "favicon.ico"])
    elif system == "Darwin":
        candidates.extend([icons / "aider.icns"])

    # Cross-platform fallback.
    candidates.extend([icons / "favicon.ico", icons / "favicon-32x32.png"])

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _maybe_start_tray(on_quit):
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None

    icon_path = _find_desktop_icon()
    if not icon_path or not Path(icon_path).exists():
        return None

    try:
        image = Image.open(icon_path)
    except OSError:
        return None

    menu = pystray.Menu(pystray.MenuItem("Quit", lambda _icon, _item: on_quit()))
    tray = pystray.Icon("aider", image, "Aider Desktop", menu)
    tray.run_detached()
    return tray


def _terminate_process(proc: subprocess.Popen | None):
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def launch_desktop_gui(args, write_streamlit_credentials, debug: bool = False):
    import webview

    from aider import gui

    host = "127.0.0.1"
    port = _find_free_port(host)

    write_streamlit_credentials()

    target = str(Path(gui.__file__).resolve())
    st_args = [
        "streamlit",
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

    process = subprocess.Popen(st_args, cwd=os.getcwd())
    atexit.register(_terminate_process, process)

    if not _wait_for_server(host, port):
        _terminate_process(process)
        raise RuntimeError("Timed out waiting for the local GUI server to start")

    url = f"http://{host}:{port}"
    icon = _find_desktop_icon()
    window = webview.create_window(
        "Aider Desktop",
        url,
        width=DEFAULT_WINDOW_SIZE[0],
        height=DEFAULT_WINDOW_SIZE[1],
        min_size=MIN_WINDOW_SIZE,
        resizable=True,
        fullscreen=False,
        icon=icon,
    )

    def _on_closed():
        _terminate_process(process)

    tray = _maybe_start_tray(_on_closed)

    window.events.closed += _on_closed
    webview.start(debug=debug)

    if tray is not None:
        tray.stop()
