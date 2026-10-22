"""本地游戏服务：静态页面 + SSE 事件流 + 开局/操作/复盘/战绩接口。只用标准库。"""

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
from .engine import EventLog, Game, HumanInbox
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
        self.game: Game | None = None
        self.game_id = 0
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.last_replay: str | None = None

    @property
    def mock_only(self) -> bool:
        return self.force_mock or not self.settings.configured

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    @property
    def results_file(self) -> Path:
        return self.replay_dir / "results.jsonl"

    def start(self, body: dict) -> dict:
        with self.lock:
            if self.running:
                return {"ok": False, "error": "当前对局还没结束"}
            mock = bool(body.get("mock")) or self.mock_only
            llm = MockLLM(delay=0.6) if mock else PrismClient(self.settings)
            self.log = EventLog()
            self.game_id += 1
            game = Game(
                llm, self.settings, self.log,
                mode=str(body.get("mode") or "play"),
                lang=str(body.get("lang") or "zh"),
                n_ai=int(body.get("n_ai") or 5),
                rounds=int(body.get("rounds") or 3),
                inbox=HumanInbox(),
            )
            self.game = game
            self.thread = threading.Thread(target=self._run, args=(game, mock), daemon=True)
            self.thread.start()
            return {"ok": True, "game_id": self.game_id, "mock": mock, "mode": game.mode}

    def abort(self) -> dict:
        game = self.game
        if game and self.running:
            game.aborted = True
            game.inbox.cancel()
        return {"ok": True}

    def _run(self, game: Game, mock: bool) -> None:
        try:
            game.run()
        except Exception as exc:  # 不让后台线程静默死掉
            game.emit("system", text=f"对局异常终止：{exc}")
            game.log.finish()
        if not game.winner:
            return
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        path = self.replay_dir / f"turing-{datetime.now():%Y%m%d-%H%M%S}.html"
        path.write_text(build_replay(game.log.events), encoding="utf-8")
        self.last_replay = str(path)
        record = {"time": datetime.now().isoformat(timespec="seconds"), "mock": mock, **game.summary()}
        with self.results_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def visible(self, event: dict) -> bool:
        """真人对局进行中，AI 的内心和真实身份不能推给前端，否则就作弊了。"""
        if event["vis"] != "judge" or not self.game:
            return True
        return self.game.mode == "watch" or bool(self.log and self.log.done)

    def stats(self) -> dict:
        return aggregate(self.results_file)


def aggregate(path: Path) -> dict:
    """累计战绩：真人胜率、各模型抓人命中率、观战模式里各模型的藏身成功率。"""
    out = {"games": 0, "human_games": 0, "human_wins": 0, "models": {}}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("mock"):
            continue
        out["games"] += 1
        if r.get("mode") == "play":
            out["human_games"] += 1
            out["human_wins"] += int(r.get("winner") == "human")
        for model, s in (r.get("model_stats") or {}).items():
            m = out["models"].setdefault(model, {"votes": 0, "hits": 0, "hide_games": 0, "hide_wins": 0})
            m["votes"] += s.get("votes", 0)
            m["hits"] += s.get("hits", 0)
        if r.get("impostor_model"):
            m = out["models"].setdefault(r["impostor_model"], {"votes": 0, "hits": 0, "hide_games": 0, "hide_wins": 0})
            m["hide_games"] += 1
            m["hide_wins"] += int(r.get("winner") == "human")
    return out


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
                return self._json({
                    "settings": state.settings.public(),
                    "mock_only": state.mock_only,
                    "game_id": state.game_id,
                    "running": state.running,
                    "last_replay": state.last_replay,
                    "stats": state.stats(),
                })
            if url.path == "/api/events":
                return self._events(int(parse_qs(url.query).get("since", ["0"])[0]))
            if url.path == "/api/log":
                if not state.log or not state.log.done:
                    return self._json({"error": "对局还没结束"}, 409)
                return self._json(state.log.events)
            if url.path == "/api/replay":
                if not state.log or not state.log.done:
                    return self._json({"error": "对局还没结束"}, 409)
                body = build_replay(state.log.events).encode()
                name = f"turing-replay-{state.game_id}.html"
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
            if path == "/api/start":
                return self._json(state.start(body))
            if path == "/api/abort":
                return self._json(state.abort())
            if path == "/api/act":
                game = state.game
                ok = bool(game and state.running and game.inbox.submit(str(body.get("kind")), body.get("value")))
                return self._json({"ok": ok})
            self._send(404, b"not found", "text/plain")

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
                        if state.visible(e):
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
    ap = argparse.ArgumentParser(description="反向图灵：找出人类")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--mock", action="store_true", help="不连网关，用假模型跑")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true", help="用 pywebview 打开独立窗口")
    ap.add_argument("--replays", default=str(Path.home() / "Documents" / "turing-replays"))
    args = ap.parse_args(argv)

    state = State(Path(args.replays), args.mock)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    mode = "mock 模式" if state.mock_only else f"网关 {state.settings.base_url}"
    print(f"找出人类：{url}（{mode}），复盘和战绩保存在 {args.replays}")

    if args.window:
        import webview  # 可选依赖

        threading.Thread(target=server.serve_forever, daemon=True).start()
        webview.create_window("找出人类", url, width=1280, height=840)
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
