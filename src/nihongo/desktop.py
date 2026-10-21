"""macOS 桌面应用：在原生窗口（系统 WebKit）里打开本地服务。"""

from __future__ import annotations

import socket
import threading
import time
import webbrowser

APP_TITLE = "日语学习助手"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bridge:
    """暴露给页面的 JS 接口：window.pywebview.api.*"""

    def open_external(self, url: str) -> bool:
        if url.startswith(("http://127.0.0.1:", "https://")):
            webbrowser.open(url)
            return True
        return False


def run(port: int | None = None) -> int:
    import uvicorn
    import webview

    from .server import create_app

    port = port or _free_port()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{port}/",
        width=1220,
        height=840,
        min_size=(380, 560),
        background_color="#f7f3ea",
        js_api=Bridge(),
    )
    webview.start()
    server.should_exit = True
    thread.join(timeout=3)
    return 0
