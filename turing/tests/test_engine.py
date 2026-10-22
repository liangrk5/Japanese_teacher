import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from turing.config import ASTRA, SEED, Settings
from turing.engine import Game, HumanInbox
from turing.llm import LLMError, MockLLM, parse_json
from turing.replay import build_replay
from turing.server import aggregate, bind


class Bot:
    """假装是真人：投第一个候选人，答题固定一句。"""

    def __init__(self):
        self.requests = []

    def ask(self, request, timeout):
        self.requests.append(request)
        return request["candidates"][0] if request["kind"] == "vote" else "我是一个 AI 助手。"


def play(seed, mode="play", **kw):
    game = Game(MockLLM(seed=seed), Settings(), mode=mode, seed=seed, inbox=Bot(), **kw)
    game.run()
    return game


class GameTest(unittest.TestCase):
    def test_full_games_finish(self):
        for seed in range(30):
            for mode in ("play", "watch"):
                game = play(seed, mode)
                events = game.log.events
                self.assertEqual(events[0]["type"], "setup")
                self.assertEqual(events[-1]["type"], "end")
                self.assertIn(game.winner, ("human", "ai"))
                self.assertTrue(game.log.done)
                out = [e for e in events if e["type"] == "eliminated"]
                if game.winner == "ai":
                    self.assertEqual(out[-1]["identity"], "human")
                else:
                    self.assertTrue(all(e["identity"] == "ai" for e in out))

    def test_seats_and_models(self):
        game = Game(MockLLM(), Settings(), seed=1, n_ai=5)
        self.assertEqual(len(game.seats), 6)
        self.assertEqual(sum(s.kind == "human" for s in game.seats), 1)
        ai_models = [s.model for s in game.seats if s.kind == "ai"]
        self.assertEqual(sorted({ai_models.count(SEED), ai_models.count(ASTRA)}), [2, 3])
        watch = Game(MockLLM(), Settings(), mode="watch", seed=1)
        spy = [s for s in watch.seats if s.kind == "impostor"]
        self.assertEqual(len(spy), 1)
        self.assertTrue(spy[0].model and spy[0].persona)

    def test_rounds_capped(self):
        game = Game(MockLLM(), Settings(), n_ai=3, rounds=9)
        self.assertEqual(game.rounds, 2)

    def test_public_events_do_not_leak_identity(self):
        game = play(4)
        for e in game.log.events:
            if e["vis"] != "public" or e["type"] == "end":
                continue
            blob = json.dumps(e, ensure_ascii=False)
            self.assertNotIn(SEED, blob)
            self.assertNotIn(ASTRA, blob)
            self.assertNotIn('"kind"', blob)

    def test_ai_prompt_hides_others_thoughts(self):
        game = play(5, "watch")
        a, b = [s for s in game.seats if s.kind == "ai"][:2]
        hist = game.history_for(a)
        theirs = [e["text"] for e in game.log.events if e["type"] == "thought" and e["actor"] == b.id]
        mine = [e["text"] for e in game.log.events if e["type"] == "thought" and e["actor"] == a.id]
        self.assertTrue(mine and mine[0] in hist)
        self.assertIn("你当时心里想", hist)
        self.assertFalse(any(f"（你当时心里想：{t}）" in hist for t in theirs if t not in mine))

    def test_human_votes_and_answers_reach_engine(self):
        bot = Bot()
        game = Game(MockLLM(seed=2), Settings(), seed=2, inbox=bot)
        game.run()
        kinds = {r["kind"] for r in bot.requests}
        self.assertEqual(kinds, {"answer", "discuss", "vote"})
        me = game.human
        says = [e for e in game.log.events if e["type"] == "say" and e["actor"] == me.id]
        self.assertTrue(all(e["text"] == "我是一个 AI 助手。" for e in says))

    def test_human_timeout_and_inbox(self):
        inbox = HumanInbox()
        game = Game(MockLLM(seed=3), Settings(), seed=3, inbox=inbox,
                    human_timeouts={"answer": 0.05, "discuss": 0.05, "vote": 0.05})
        game.run()
        me = game.human
        says = [e for e in game.log.events if e["type"] == "say" and e["actor"] == me.id]
        self.assertTrue(says and all(e["text"] == "……" for e in says))
        votes = [e for e in game.log.events if e["type"] == "votes" and not e["revote"]]
        mine = [b for e in votes for b in e["ballots"] if b["voter"] == me.id]
        self.assertTrue(all(b["target"] is None for b in mine))

    def test_inbox_submit_and_abort(self):
        inbox = HumanInbox()
        self.assertFalse(inbox.submit("answer", "x"))
        game = Game(MockLLM(seed=4), Settings(), seed=4, inbox=inbox)
        t = threading.Thread(target=game.run)
        t.start()
        for _ in range(100):
            if inbox.pending:
                break
            time.sleep(0.02)
        self.assertFalse(inbox.submit("vote", "Alpha"))
        self.assertTrue(inbox.submit("answer", "你好"))
        game.aborted = True
        inbox.cancel()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(game.log.events[-1]["type"], "aborted")

    def test_llm_failure_falls_back(self):
        class Broken:
            def chat(self, *a, **k):
                raise LLMError("模型调用超过 1 秒没有返回")

        game = Game(Broken(), Settings(), mode="watch", seed=1)
        game.run()
        self.assertIn(game.winner, ("human", "ai"))
        self.assertTrue(any(e["type"] == "system" for e in game.log.events))

    def test_slow_model_is_cut_off(self):
        class Slow:
            def chat(self, *a, **k):
                time.sleep(3)
                return "{}"

        settings = Settings(timeout=-4.8)  # 墙钟上限 = timeout + 5 = 0.2 秒
        game = Game(Slow(), settings, mode="watch", seed=1, rounds=1, n_ai=2)
        started = time.monotonic()
        seat = game.seats[0]
        data = game.ask_ai(seat, "answer", "x", [s.name for s in game.seats[1:]])
        self.assertTrue(data.get("fallback"))
        self.assertLess(time.monotonic() - started, 2)

    def test_vote_validation(self):
        game = Game(MockLLM(), Settings(), seed=1)
        data = {"vote": "bravo", "suspicion": {"Bravo": "88", "Nobody": 3, "Cosmo": 150}}
        self.assertTrue(game._valid(data, "vote", ["Bravo", "Cosmo"]))
        self.assertEqual(data["vote"], "Bravo")
        self.assertEqual(data["suspicion"], {"Bravo": 88, "Cosmo": 100})
        self.assertFalse(game._valid({"vote": "Alpha"}, "vote", ["Bravo"]))

    def test_summary_and_stats(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.jsonl"
            with path.open("w") as f:
                for seed in range(6):
                    game = play(seed, "watch" if seed % 2 else "play")
                    f.write(json.dumps({"mock": False, **game.summary()}) + "\n")
                f.write(json.dumps({"mock": True, **game.summary()}) + "\n")
            st = aggregate(path)
            self.assertEqual(st["games"], 6)
            self.assertEqual(st["human_games"], 3)
            self.assertTrue(st["models"])
            hide = sum(m["hide_games"] for m in st["models"].values())
            self.assertEqual(hide, 3)

    def test_replay_embeds_events(self):
        game = play(1)
        html = build_replay(game.log.events)
        self.assertIn("window.REPLAY", html)
        self.assertNotIn('src="static/app.js"', html)


class BindTest(unittest.TestCase):
    def test_busy_port_falls_back(self):
        from http.server import BaseHTTPRequestHandler

        first = bind(0, BaseHTTPRequestHandler)
        port = first.server_address[1]
        second = bind(port, BaseHTTPRequestHandler)
        try:
            self.assertNotEqual(second.server_address[1], port)
        finally:
            first.server_close()
            second.server_close()


class ParseTest(unittest.TestCase):
    def test_parse_json(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json('<think>{x}</think> ok {"vote": "Alpha"}'), {"vote": "Alpha"})


if __name__ == "__main__":
    unittest.main()
