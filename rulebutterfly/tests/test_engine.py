import json
import socket
import tempfile
import time
import unittest
from pathlib import Path

from rulebutterfly import world
from rulebutterfly.config import ASTRA, SEED, Settings
from rulebutterfly.engine import Town
from rulebutterfly.llm import LLMError, MockLLM
from rulebutterfly.replay import build_replay
from rulebutterfly.server import State, bind, make_handler

OPS = [
    {"op": "add", "text": "从明天开始，所有人的工资公开。"},
    {"op": "none"},
    {"op": "replace", "index": 0, "text": "说谎会扣钱，但没人知道扣的钱去了哪里。"},
    {"op": "repeal", "index": 1},
    {"op": "add", "text": "小镇每天投票选出一个人免交房租。"},
]


def play(seed, scenario="calm", llm=None, settings=None):
    town = Town(llm or MockLLM(seed=seed), settings or Settings(), scenario=scenario, seed=seed)
    town.start()
    for i in range(20):
        if town.result:
            break
        town.play_day(OPS[i] if i < len(OPS) else {"op": "none"})
    return town


class Garbage:
    def chat(self, model, messages, meta=None):
        return "我不想输出 JSON"


class Broken:
    def chat(self, model, messages, meta=None):
        raise LLMError("网关 502")


class Slow:
    def chat(self, model, messages, meta=None):
        time.sleep(8)
        return "{}"


class Wild:
    """输出越界和乱七八糟的字段，引擎必须清洗。"""

    def chat(self, model, messages, meta=None):
        kind = (meta or {}).get("kind")
        if kind == "act":
            return json.dumps({"action": "fly", "target": "不存在的人", "location": "moon", "say": "**" * 100})
        if kind == "referee":
            return json.dumps({
                "events": [{"title": "大事", "text": "x", "who": ["镇长周明远", "路人"], "kind": "weird"}, "坏数据"],
                "money": {"钱有福": 999999, "路人": 5, "王芳": "abc"},
                "mood": {"李小强": -500}, "rep": {"陈直言": 80},
                "relations": [{"a": "王芳", "b": "王芳", "delta": 10}, {"a": "王芳", "b": "钱有福", "delta": -999}],
                "exposed": ["周明远", "周明远", "路人"], "status": {"周明远": "resigned", "吴甜甜": "king"},
                "salary": {"李小强": 99999, "钱有福": 500}, "strike": "yes",
            })
        return "{}"


class TownTest(unittest.TestCase):
    def test_full_games_finish(self):
        for seed in range(12):
            for scenario in world.SCENARIOS:
                town = play(seed, scenario)
                events = town.log.events
                self.assertEqual(events[0]["type"], "setup")
                self.assertEqual(events[-1]["type"], "end")
                self.assertIn(town.result, ("won", "lost", "done"))
                self.assertTrue(town.log.done)
                self.assertTrue(events[-1]["share_line"])
                self.assertLessEqual(town.day, world.SCENARIOS[scenario]["days"])
                self.assertEqual([e["seq"] for e in events], list(range(len(events))))
                json.dumps(events, ensure_ascii=False)

    def test_events_are_snapshots(self):
        """事件里的规则和罢工列表不能被后面的回合改掉，否则复盘会穿越。"""
        town = play(3)
        rule_events = [e for e in town.log.events if e["type"] == "rule"]
        self.assertEqual(rule_events[0]["rules"][-1]["text"], OPS[0]["text"])
        self.assertEqual(rule_events[0]["rules"][0]["text"], world.DEFAULT_RULES[0])
        first = next(e for e in town.log.events if e["type"] == "objective")
        self.assertEqual(first["strikes"], [])

    def test_factions_use_different_models(self):
        town = Town(MockLLM(seed=1), Settings())
        models = {r.faction: r.model for r in town.residents}
        self.assertEqual(models[world.CAPITAL], SEED)
        self.assertEqual(models[world.LABOR], ASTRA)
        town.day = 1
        self.assertEqual(town.referee_model(), ASTRA)
        town.day = 2
        self.assertEqual(town.referee_model(), SEED)

    def test_rule_ops(self):
        town = Town(MockLLM(seed=1), Settings())
        town.start()
        self.assertEqual(town.validate_op({"op": "replace", "index": 9, "text": "abc"}), "要改的规则不存在")
        self.assertTrue(town.validate_op({"op": "add", "text": "x"}))
        self.assertTrue(town.validate_op({"op": "zap"}))
        self.assertEqual(town.validate_op({"op": "none"}), "")
        town.play_day({"op": "replace", "index": 1, "text": "房租全免。"})
        self.assertEqual(town.rules[1]["text"], "房租全免。")
        self.assertEqual(town.rules[1]["by"], "player")
        self.assertEqual(town.rules[1]["day"], 1)
        town.play_day({"op": "repeal", "index": 0})
        self.assertEqual(len(town.rules), 2)
        for _ in range(world.MAX_RULES):
            town.rules.append({"text": "占位", "by": "player", "day": 0, "title": "", "mechanics": ""})
        self.assertIn("最多", town.validate_op({"op": "add", "text": "再来一条"}))

    def test_models_failing_never_block(self):
        for llm in (Garbage(), Broken()):
            town = play(1, "calm", llm=llm)
            self.assertIn(town.result, ("won", "lost"))
            acts = [e for e in town.log.events if e["type"] == "action"]
            self.assertTrue(all(a["fallback"] for a in acts))
            self.assertTrue(all(a["action"] == "work" for a in acts))
            resolves = [e for e in town.log.events if e["type"] == "resolve"]
            self.assertTrue(all(r["fallback"] for r in resolves))
            self.assertTrue(town.log.events[-1]["share_line"])

    def test_timeout_falls_back(self):
        settings = Settings(timeout=0.1)
        town = Town(Slow(), settings, scenario="calm")
        town.start()
        started = time.monotonic()
        town.play_day({"op": "add", "text": "所有人的工资公开。"})
        self.assertLess(time.monotonic() - started, 25)
        self.assertEqual(town.day, 1)
        self.assertTrue(all(e["fallback"] for e in town.log.events if e["type"] == "action"))

    def test_wild_output_is_clamped(self):
        town = Town(Wild(), Settings(), scenario="sandbox")
        town.start()
        boss_before = town.get("钱有福").money
        town.play_day({"op": "none"})
        acts = [e for e in town.log.events if e["type"] == "action"]
        self.assertTrue(all(a["action"] == "work" and a["target"] == "" for a in acts))
        self.assertTrue(all(a["location"] in world.BUILDING_IDS for a in acts))
        self.assertTrue(all("**" not in a["say"] and len(a["say"]) <= 60 for a in acts))
        res = next(e for e in town.log.events if e["type"] == "resolve")
        self.assertEqual(res["money"], {"钱有福": 300})
        self.assertEqual(res["mood"], {"李小强": -25})
        self.assertEqual(res["rep"], {"陈直言": 25})
        self.assertEqual(res["events"][0]["who"], ["周明远"])
        self.assertEqual(res["events"][0]["kind"], "daily")
        self.assertEqual(res["relations"], [{"a": "王芳", "b": "钱有福", "delta": -40, "why": ""}])
        self.assertEqual(res["exposed"], ["周明远"])
        self.assertEqual(res["status"], {"周明远": "resigned"})
        self.assertEqual(res["salary"], {"李小强": 300})
        self.assertTrue(res["strike"])
        self.assertTrue(town.get("周明远").exposed)
        self.assertEqual(town.mayor.status, "resigned")
        self.assertEqual(town.approval(), 0)
        self.assertGreater(town.get("钱有福").money, boss_before)

    def test_objectives(self):
        town = Town(MockLLM(seed=1), Settings(), scenario="calm")
        town.day, town.strikes = 3, [2]
        self.assertEqual(town.objective()["status"], "lost")
        town = Town(MockLLM(seed=1), Settings(), scenario="nobody")
        town.day = 2
        town.get("吴甜甜").money = -5
        self.assertEqual(town.objective()["status"], "lost")
        town = Town(MockLLM(seed=1), Settings(), scenario="mayor")
        town.day = 7
        self.assertEqual(town.objective()["status"], "won" if town.approval() >= 50 else "lost")
        town.mayor.status = "resigned"
        self.assertEqual(town.objective()["status"], "lost")

    def test_economy_strike_stops_wages(self):
        town = Town(MockLLM(seed=1), Settings())
        acts = {r.name: {"action": "work"} for r in town.residents}
        acts["李小强"] = {"action": "strike"}
        _, delta = town._economy(acts)
        # 李小强 没有工资，只付房租和生活费
        self.assertEqual(delta["李小强"], -world.RENT["李小强"] - world.LIVING_COST - 12)
        self.assertEqual(delta["王芳"], 130 - world.RENT["王芳"] - world.LIVING_COST - 12)

    def test_private_events_and_whispers(self):
        town = play(5, "sandbox")
        whispers = [e for e in town.log.events if e["type"] == "whisper"]
        self.assertTrue(whispers)
        mem = town._memory_for(town.get(whispers[-1]["to"]))
        self.assertIsInstance(mem, str)

    def test_replay_is_self_contained(self):
        town = play(2)
        html = build_replay(town.log.events)
        self.assertIn("window.REPLAY", html)
        self.assertIn('id="three-src"', html)
        self.assertNotIn('src="static/', html)
        self.assertNotIn('href="static/', html)
        self.assertEqual(html.count("</script>"), html.count("<script"))


class ServerTest(unittest.TestCase):
    def test_bind_falls_back_when_port_taken(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        try:
            server = bind(port, make_handler(State(Path(tempfile.mkdtemp()), True)))
            self.assertNotEqual(server.server_address[1], port)
            server.server_close()
        finally:
            blocker.close()

    def test_state_runs_a_game_and_saves_replay(self):
        tmp = Path(tempfile.mkdtemp())
        state = State(tmp, force_mock=True)
        self.assertTrue(state.new({"scenario": "mayor"})["ok"])
        for _ in range(10):
            if state.town.result:
                break
            res = state.day({"op": "none"})
            self.assertTrue(res["ok"], res)
            self.assertFalse(state.day({"op": "none"})["ok"])  # 正在结算时不能再过一天
            state.thread.join(30)
        self.assertTrue(state.town.result)
        self.assertTrue(list(tmp.glob("rulebutterfly-*.html")))
        self.assertTrue((tmp / "results.jsonl").is_file())
        self.assertFalse(state.day({"op": "none"})["ok"])


if __name__ == "__main__":
    unittest.main()
