"""本地游戏服务：静态页面 + SSE 事件流 + 开局/过一天/复盘接口。只用标准库。"""

from __future__ import annotations

import argparse
import errno
import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import world
from .config import load_settings
from .engine import EventLog, Town
from .llm import MockLLM, PrismClient
from .replay import build_replay

STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}


class State:
    def __init__(self, replay_dir: Path, force_mock: bool):
        self.settings = load_settings()
        self.replay_dir = replay_dir
        self.force_mock = force_mock
        self.town: Town | None = None
        self.game_id = 0
        self.mock = False
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.last_replay: str | None = None

    @property
    def mock_only(self) -> bool:
        return self.force_mock or not self.settings.configured

    @property
    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def new(self, body: dict) -> dict:
        with self.lock:
            if self.busy:
                return {"ok": False, "error": "小镇还在结算今天，等这一天过完再重开"}
            if self.town:
                self.town.abort()
            self.mock = bool(body.get("mock")) or self.mock_only
            llm = MockLLM(delay=0.5) if self.mock else PrismClient(self.settings)
            self.game_id += 1
            self.town = Town(llm, self.settings, EventLog(), scenario=str(body.get("scenario") or "calm"))
            self.town.start()
            return {"ok": True, "game_id": self.game_id, "mock": self.mock}

    def day(self, body: dict) -> dict:
        with self.lock:
            town = self.town
            if not town:
                return {"ok": False, "error": "先开一局"}
            if self.busy:
                return {"ok": False, "error": "今天还没结算完"}
            if town.result:
                return {"ok": False, "error": "这一局已经结束了"}
            if error := town.validate_op(body):
                return {"ok": False, "error": error}
            self.thread = threading.Thread(target=self._run_day, args=(town, body), daemon=True)
            self.thread.start()
            return {"ok": True}

    def _run_day(self, town: Town, op: dict) -> None:
        try:
            town.play_day(op)
        except Exception as exc:  # 不让后台线程静默死掉
            town.emit("system", text=f"模拟出错：{exc}")
            town.result = "error"
            town.log.finish()
            raise
        if town.log.done and town.result in ("won", "lost", "done"):
            self.replay_dir.mkdir(parents=True, exist_ok=True)
            path = self.replay_dir / f"rulebutterfly-{datetime.now():%Y%m%d-%H%M%S}.html"
            path.write_text(build_replay(town.log.events), encoding="utf-8")
            self.last_replay = str(path)
            record = {"time": datetime.now().isoformat(timespec="seconds"), "mock": self.mock, **town.summary()}
            with (self.replay_dir / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def abort(self) -> dict:
        if self.town:
            self.town.abort()
        return {"ok": True}


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
                town = state.town
                return self._json({
                    "settings": state.settings.public(),
                    "mock_only": state.mock_only,
                    "game_id": state.game_id if town else 0,
                    "busy": state.busy,
                    "result": town.result if town else "",
                    "last_replay": state.last_replay,
                    "scenarios": world.SCENARIOS,
                    "buildings": world.BUILDINGS,
                })
            if url.path == "/api/events":
                return self._events(int(parse_qs(url.query).get("since", ["0"])[0]))
            if url.path == "/api/replay":
                if not state.town:
                    return self._json({"error": "还没有开局"}, 409)
                body = build_replay(list(state.town.log.events)).encode()
                name = f"rulebutterfly-replay-{state.game_id}.html"
                return self._send(200, body, "text/html",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"ok": False, "error": "bad json"}, 400)
            if path == "/api/new":
                return self._json(state.new(body))
            if path == "/api/day":
                return self._json(state.day(body))
            if path == "/api/abort":
                return self._json(state.abort())
            self._send(404, b"not found", "text/plain")

        def _static(self, name: str):
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"),
                       {"Cache-Control": "no-cache"})

        def _events(self, seq: int):
            town = state.town
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if town is None:
                return
            log = town.log
            try:
                while state.town is town:
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
                self.wfile.write(b"event: replaced\ndata: {}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def bind(port: int, handler, tries: int = 20) -> ThreadingHTTPServer:
    """端口被占用时依次往后找一个空闲端口，实在不行让系统分配。"""
    for p in range(port, port + tries):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), handler)
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    return ThreadingHTTPServer(("127.0.0.1", 0), handler)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="只改一条规则：AI 小镇蝴蝶效应")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--mock", action="store_true", help="不连网关，用假模型跑")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true", help="用 pywebview 打开独立窗口")
    ap.add_argument("--replays", default=str(Path.home() / "Documents" / "rulebutterfly-replays"))
    args = ap.parse_args(argv)

    state = State(Path(args.replays), args.mock)
    server = bind(args.port, make_handler(state))
    server.daemon_threads = True
    port = server.server_address[1]
    if port != args.port:
        print(f"端口 {args.port} 已被占用，改用 {port}")
    url = f"http://127.0.0.1:{port}/"
    mode = "mock 模式" if state.mock_only else f"网关 {state.settings.base_url}"
    print(f"只改一条规则：{url}（{mode}），复盘保存在 {args.replays}")

    if args.window:
        import webview  # 可选依赖

        threading.Thread(target=server.serve_forever, daemon=True).start()
        webview.create_window("只改一条规则", url, width=1400, height=880)
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
