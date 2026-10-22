import json
import unittest

from werewolf.config import ASTRA, SEED, Settings
from werewolf.engine import Game
from werewolf.llm import MockLLM, PrismClient, parse_json
from werewolf.replay import build_replay


def play(seed):
    game = Game(MockLLM(seed=seed), Settings(), seed=seed)
    game.run()
    return game


class GameTest(unittest.TestCase):
    def test_full_games_finish(self):
        for seed in range(30):
            game = play(seed)
            events = game.log.events
            self.assertEqual(events[0]["type"], "game_start")
            self.assertEqual(events[-1]["type"], "game_over")
            self.assertIn(game.winner, ("wolf", "good", "draw"))
            self.assertTrue(game.log.done)

    def test_models_split_evenly_per_team(self):
        game = Game(MockLLM(), Settings(), seed=1)
        wolves = [p.model for p in game.players if p.role == "wolf"]
        good = [p.model for p in game.players if p.role != "wolf"]
        self.assertEqual(wolves.count(SEED), 2)
        self.assertEqual(good.count(ASTRA), 4)

    def test_information_isolation(self):
        game = play(3)
        villager = next(p for p in game.players if p.role == "villager")
        wolf = next(p for p in game.players if p.role == "wolf")
        seer = next(p for p in game.players if p.role == "seer")
        v_hist = game.history_for(villager)
        self.assertNotIn("狼队频道", v_hist)
        self.assertNotIn("查验了", v_hist)
        self.assertIn("狼队频道", game.history_for(wolf))
        if any(e.get("action") == "seer_check" for e in game.log.events):
            self.assertIn("查验了", game.history_for(seer))
        # 别人的内心独白不会出现在玩家上下文里
        for line in v_hist.splitlines():
            if "心里想" in line:
                self.assertIn(villager.label(), line)

    def test_invalid_output_falls_back(self):
        class Broken:
            def chat(self, *a, **k):
                return "我不想输出 JSON"

        game = Game(Broken(), Settings(), seed=2, narrator=False)
        game.run()
        self.assertEqual(game.log.events[-1]["type"], "game_over")
        self.assertTrue(any(e["type"] == "system" for e in game.log.events))

    def test_timeout_falls_back_without_retry(self):
        from werewolf.llm import LLMError

        calls = []

        class Slow:
            def chat(self, model, messages, meta=None):
                calls.append(meta.get("kind") if meta else None)
                raise LLMError("模型调用超过 60 秒没有返回")

        game = Game(Slow(), Settings(), seed=4, narrator=False)
        p = game.players[0]
        r = game.ask(p, "投票", "target", [2, 3])
        self.assertIn(r["target"], (2, 3))
        self.assertEqual(len(calls), 1)

    def test_replay_embeds_events(self):
        game = play(5)
        html = build_replay(game.log.events)
        self.assertIn("window.REPLAY =", html)
        self.assertNotIn('src="static/app.js"', html)
        start = html.index("window.REPLAY =") + len("window.REPLAY =")
        end = html.index(";</script>", start)
        self.assertEqual(len(json.loads(html[start:end])), len(game.log.events))


class LLMTest(unittest.TestCase):
    def test_parse_json(self):
        self.assertEqual(parse_json('好的\n```json\n{"target": 3, "t": "a}b"}\n```'), {"target": 3, "t": "a}b"})
        self.assertEqual(parse_json('<think>{"x":1}</think>{"y": 2}'), {"y": 2})

    def test_astra_request_has_no_temperature(self):
        sent = []

        class Fake(PrismClient):
            def _post(self, payload):
                sent.append((self._url(), self._headers(), dict(payload)))
                return 200, json.dumps({"choices": [{"message": {"content": "hi"}}]})

        s = Settings(base_url="https://prism.example/v1", api_key="vk", caller="psm.x")
        client = Fake(s)
        self.assertEqual(client.chat(ASTRA, [{"role": "user", "content": "x"}]), "hi")
        client.chat(SEED, [{"role": "user", "content": "x"}])
        url, headers, p1 = sent[0]
        self.assertEqual(url, "https://prism.example/v1/chat/completions")
        self.assertEqual(headers["X-Caller-PSM"], "psm.x")
        self.assertEqual(headers["Authorization"], "Bearer vk")
        self.assertNotIn("temperature", p1)
        self.assertNotIn("thinking", p1)
        self.assertIn("temperature", sent[1][2])
        self.assertEqual(sent[1][2]["thinking"], {"type": "disabled"})

    def test_rejected_param_is_dropped_and_retried(self):
        calls = []

        class Fake(PrismClient):
            def _post(self, payload):
                calls.append(dict(payload))
                if "max_tokens" in payload:
                    return 400, '{"error": {"message": "Unsupported parameter: max_tokens"}}'
                return 200, json.dumps({"choices": [{"message": {"content": "ok"}}]})

        client = Fake(Settings(base_url="https://x", api_key="k"))
        self.assertEqual(client.chat("m", []), "ok")
        self.assertEqual(len(calls), 2)
        client.chat("m", [])
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
