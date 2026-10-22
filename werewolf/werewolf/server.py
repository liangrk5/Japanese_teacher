"""本地观战服务：静态页面 + SSE 事件流 + 开局/复盘接口。只用标准库。"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import load_settings
from .engine import EventLog, Game
from .llm import MockLLM, PrismClient
from .replay import build_replay

STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}


class State:
    def __init__(self, replay_dir: Path, force_mock: bool):
        self.settings = load_settings()
        self.replay_dir = replay_dir
        self.force_mock = force_mock
        self.log: EventLog | None = None
        self.game_id = 0
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.last_replay: str | None = None

    @property
    def mock_only(self) -> bool:
        return self.force_mock or not self.settings.configured

    def start(self, mock: bool) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return {"ok": False, "error": "当前对局还没结束"}
            mock = mock or self.mock_only
            llm = MockLLM(delay=0.3) if mock else PrismClient(self.settings)
            self.log = EventLog()
            self.game_id += 1
            game = Game(llm, self.settings, self.log)
            self.thread = threading.Thread(target=self._run, args=(game,), daemon=True)
            self.thread.start()
            return {"ok": True, "game_id": self.game_id, "mock": mock}

    def _run(self, game: Game) -> None:
        try:
            game.run()
        except Exception as exc:  # 不让后台线程静默死掉
            game.emit("system", "judge", text=f"对局异常终止：{exc}")
            game.log.finish()
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        path = self.replay_dir / f"werewolf-{datetime.now():%Y%m%d-%H%M%S}.html"
        path.write_text(build_replay(game.log.events), encoding="utf-8")
        self.last_replay = str(path)


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 安静一点
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._static("index.html")
            if url.path.startswith("/static/"):
                return self._static(url.path.removeprefix("/static/"))
            if url.path == "/api/status":
                running = bool(state.thread and state.thread.is_alive())
                return self._json({
                    "settings": state.settings.public(),
                    "mock_only": state.mock_only,
                    "game_id": state.game_id,
                    "running": running,
                    "last_replay": state.last_replay,
                })
            if url.path == "/api/events":
                return self._events(int(parse_qs(url.query).get("since", ["0"])[0]))
            if url.path == "/api/replay":
                if not state.log:
                    return self._json({"error": "还没有对局"}, 404)
                body = build_replay(state.log.events).encode()
                name = f"werewolf-replay-{state.game_id}.html"
                return self._send(200, body, "text/html",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/api/start":
                return self._send(404, b"not found", "text/plain")
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(state.start(bool(body.get("mock"))))

        def _static(self, name: str):
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"),
                       {"Cache-Control": "no-cache"})

        def _events(self, seq: int):
            log = state.log
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if log is None:
                return
            try:
                while True:
                    events, done = log.since(seq)
                    for e in events:
                        self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                    seq += len(events)
                    if done and not events:
                        self.wfile.write(b"event: end\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    if not events:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="AI 狼人杀观战")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mock", action="store_true", help="不连网关，用假模型跑")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true", help="用 pywebview 打开独立窗口")
    ap.add_argument("--replays", default=str(Path.home() / "Documents" / "werewolf-replays"))
    args = ap.parse_args(argv)

    state = State(Path(args.replays), args.mock)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    mode = "mock 模式" if state.mock_only else f"网关 {state.settings.base_url}"
    print(f"AI 狼人杀观战：{url}（{mode}），复盘保存在 {args.replays}")

    if args.window:
        import webview  # 可选依赖

        threading.Thread(target=server.serve_forever, daemon=True).start()
        webview.create_window("AI 狼人杀", url, width=1360, height=860)
        webview.start()
        return
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
