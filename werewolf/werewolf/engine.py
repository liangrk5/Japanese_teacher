"""法官引擎：严格执行规则的状态机，按步骤点名玩家 Agent 行动。

每件发生的事都记成一条事件，``vis`` 决定谁能看到：
- ``public``：所有玩家
- ``wolves``：狼人阵营
- ``p<N>``：只有 N 号玩家
- ``judge``：只有法官和观众（内心独白、真实身份等）
玩家 Agent 的上下文只由它能看到的事件拼出来，模型之间不共享对话。
"""

from __future__ import annotations

import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .config import Settings
from .llm import LLMError, parse_json

ROLES = ["wolf"] * 4 + ["villager"] * 4 + ["seer", "witch", "hunter", "guard"]
GODS = {"seer", "witch", "hunter", "guard"}
ROLE_CN = {
    "wolf": "狼人",
    "villager": "平民",
    "seer": "预言家",
    "witch": "女巫",
    "hunter": "猎人",
    "guard": "守卫",
}
ROLE_TIPS = {
    "wolf": "你和狼队友每晚一起选择刀一名玩家。白天要伪装成好人，可以悍跳预言家、倒钩、冲锋，目标是杀光所有神职或所有平民。",
    "villager": "你没有技能，只能靠发言和投票找出狼人。可以适当站边、分析票型。",
    "seer": "每晚可以查验一名玩家是好人还是狼人。白天要想办法让好人相信你，报出查验结果，带队放逐狼人。",
    "witch": "你有一瓶解药和一瓶毒药，各只能用一次，同一晚不能同时使用，不能自救。每晚你会得知谁被狼人刀了。",
    "hunter": "被狼人刀死或被放逐出局时可以开枪带走一名玩家；被女巫毒死则不能开枪。",
    "guard": "每晚可以守护一名玩家（可以守自己），被守护的人不会被狼人刀死，但不能连续两晚守同一个人。注意同守同救会导致那名玩家死亡。",
}
NAMES = ["阿岚", "白夜", "陈墨", "大熊", "饭团", "高冷喵", "胡桃", "金鱼", "老K", "米粒", "南风", "小满"]
AVATARS = ["🦊", "🐼", "🐯", "🦁", "🐨", "🐸", "🐵", "🦉", "🐻", "🐰", "🐧", "🦄"]
PERSONAS = [
    "逻辑缜密，喜欢盘票型，说话慢条斯理",
    "冷静寡言，每句话都很短但切中要害",
    "热血冲动，容易激动，敢于站边",
    "老好人，总想调和矛盾，但关键时刻很坚定",
    "爱开玩笑的段子手，发言里常带梗",
    "高冷傲娇，对别人的逻辑很挑剔",
    "细节控，记得每个人说过的每句话",
    "谨慎多疑，谁都不太信任",
    "大大咧咧，凭直觉投票",
    "温柔礼貌，喜欢先肯定再质疑",
    "数据派，喜欢用概率说话",
    "戏精，发言很有感染力，喜欢煽动情绪",
]


@dataclass
class Player:
    id: int
    name: str
    avatar: str
    persona: str
    role: str
    model: str
    alive: bool = True

    def label(self) -> str:
        return f"{self.id}号{self.name}"

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "avatar": self.avatar,
            "persona": self.persona,
            "role": self.role,
            "role_cn": ROLE_CN[self.role],
            "model": self.model,
        }


@dataclass
class EventLog:
    """线程安全的事件列表，服务端用它给前端推流。"""

    events: list[dict] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)
    done: bool = False

    def append(self, event: dict) -> dict:
        with self.cond:
            event["seq"] = len(self.events)
            self.events.append(event)
            self.cond.notify_all()
        return event

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def since(self, seq: int, timeout: float = 15.0) -> tuple[list[dict], bool]:
        with self.cond:
            if seq >= len(self.events) and not self.done:
                self.cond.wait(timeout)
            return self.events[seq:], self.done


OUTPUT_RULES = {
    "speech": '只输出一个 JSON 对象：{"thought": "你此刻的真实想法，只有你自己知道", "speech": "你的公开发言，口语化，80到160字，要提到具体号码"}',
    "target": '只输出一个 JSON 对象：{"thought": "你的真实想法", "target": 号码}',
    "witch": '只输出一个 JSON 对象：{"thought": "你的真实想法", "save": true 或 false, "poison": 要毒的号码，不毒填 0}',
}


class Game:
    def __init__(
        self,
        llm,
        settings: Settings,
        log: EventLog | None = None,
        seed: int | None = None,
        narrator: bool = True,
    ):
        self.llm = llm
        self.settings = settings
        self.log = log or EventLog()
        self.rng = random.Random(seed)
        self.narrator = narrator
        self.day = 0
        self.phase = "setup"
        self.winner: str | None = None
        self.players = self._deal()
        self.antidote = True
        self.poison = True
        self.last_guarded: int | None = None
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.concurrency))

    # ------------------------------------------------------------ setup

    def _deal(self) -> list[Player]:
        roles = ROLES[:]
        self.rng.shuffle(roles)
        models = self.settings.player_models or [self.settings.judge_model]
        players = [
            Player(i + 1, NAMES[i], AVATARS[i], PERSONAS[i], roles[i], models[0])
            for i in range(12)
        ]
        # 狼人和好人阵营里各自平均分配模型，比较公平
        for team in ([p for p in players if p.role == "wolf"], [p for p in players if p.role != "wolf"]):
            self.rng.shuffle(team)
            for k, p in enumerate(team):
                p.model = models[k % len(models)]
        return players

    def get(self, pid: int) -> Player:
        return self.players[pid - 1]

    def alive(self, role: str | None = None) -> list[Player]:
        return [p for p in self.players if p.alive and (role is None or p.role == role)]

    def alive_ids(self) -> list[int]:
        return [p.id for p in self.alive()]

    # ------------------------------------------------------------ events

    def emit(self, type_: str, vis: str = "public", desc: str = "", **data) -> dict:
        return self.log.append(
            {"type": type_, "vis": vis, "day": self.day, "phase": self.phase, "desc": desc, **data}
        )

    @staticmethod
    def visible(event: dict, p: Player) -> bool:
        vis = event["vis"]
        return vis == "public" or (vis == "wolves" and p.role == "wolf") or vis == f"p{p.id}"

    def history_for(self, p: Player) -> str:
        lines = []
        for e in self.log.events:
            if not e.get("desc") or not self.visible(e, p):
                continue
            line = e["desc"]
            if e.get("actor") == p.id and e.get("thought"):
                line += f"（你当时心里想：{e['thought']}）"
            lines.append(line)
        return "\n".join(lines[-160:]) or "（游戏刚刚开始）"

    # ------------------------------------------------------------ agents

    def system_prompt(self, p: Player) -> str:
        roster = "、".join(f"{x.id}号{x.name}" for x in self.players)
        extra = ""
        if p.role == "wolf":
            mates = [x.label() for x in self.players if x.role == "wolf" and x.id != p.id]
            extra = f"你的狼队友是：{'、'.join(mates)}。"
        return (
            "你正在参加一局 12 人狼人杀（预女猎守板：4 狼人、4 平民、预言家、女巫、猎人、守卫），"
            "屠边规则：狼人杀光所有神职或所有平民即获胜，好人放逐所有狼人即获胜。\n"
            f"玩家：{roster}。\n"
            f"你是 {p.label()}，性格：{p.persona}。\n"
            f"你的真实身份是【{ROLE_CN[p.role]}】。{ROLE_TIPS[p.role]}{extra}\n"
            "法官会告诉你每一步要做什么。始终站在你的阵营利益上思考，发言要符合你的性格。"
            "不要提到你是 AI 或模型。严格按要求的 JSON 格式输出，不要输出其他内容。"
        )

    def ask(self, p: Player, task: str, kind: str, allowed: list[int] | None = None,
            allow_none: bool = False, **meta) -> dict:
        fmt = "witch" if kind == "witch" else ("target" if kind == "target" else "speech")
        messages = [
            {"role": "system", "content": self.system_prompt(p)},
            {
                "role": "user",
                "content": (
                    f"现在是第 {self.day} 天{'夜晚' if self.phase == 'night' else '白天'}。"
                    f"存活玩家：{'、'.join(map(str, self.alive_ids()))} 号。\n"
                    f"你目前知道的信息：\n{self.history_for(p)}\n\n"
                    f"法官：{task}\n{OUTPUT_RULES[fmt]}"
                ),
            },
        ]
        info = {
            "kind": kind,
            "allowed": allowed,
            "allow_none": allow_none,
            "alive": self.alive_ids(),
            "player": {"id": p.id, "role": p.role},
            **meta,
        }
        # 让观战页知道正在等谁，模型慢的时候不会以为卡住了
        self.emit("thinking", "judge", actor=p.id, kind=kind, model=p.model)
        error = ""
        raw = ""
        for attempt in range(2):
            started = time.monotonic()
            try:
                raw = self.llm.chat(p.model, messages, info)
                data = parse_json(raw)
            except LLMError as exc:
                error = str(exc)
                self._trace(p, kind, started, f"失败（第 {attempt + 1} 次）：{error[:200]}")
                if "秒没有返回" in error:
                    break  # 超时不再重试，直接由法官代为行动
                continue
            except ValueError:
                error = f"输出不是 JSON：{raw[:120]!r}"
                self._trace(p, kind, started, f"失败（第 {attempt + 1} 次）：{error}")
                continue
            self._trace(p, kind, started, "ok")
            if self._valid(data, kind, allowed, allow_none, meta):
                data["thought"] = str(data.get("thought") or "")[:400]
                return data
            error = f"非法输出：{data}"
        # 连续失败：法官替这名玩家随机选一个合法动作，保证游戏继续
        self.emit("system", "judge", desc="", text=f"{p.label()} 的模型调用失败，法官代为行动：{error[:160]}")
        if kind == "witch":
            return {"thought": "（掉线）", "save": False, "poison": 0}
        if kind == "target":
            return {"thought": "（掉线）", "target": self.rng.choice(allowed) if allowed else 0}
        return {"thought": "（掉线）", "speech": "我……网络有点卡，先过吧。"}

    @staticmethod
    def _trace(p: Player, kind: str, started: float, result: str) -> None:
        elapsed = time.monotonic() - started
        print(f"[{time.strftime('%H:%M:%S')}] {p.label()} {kind} {p.model} {elapsed:.1f}s {result}",
              file=sys.stderr, flush=True)

    @staticmethod
    def _valid(data: dict, kind: str, allowed, allow_none, meta) -> bool:
        def as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        if kind == "target":
            t = as_int(data.get("target"))
            if t is None or not (t in (allowed or []) or (allow_none and t == 0)):
                return False
            data["target"] = t
            return True
        if kind == "witch":
            save = data.get("save") in (True, "true", "True", 1)
            poison = as_int(data.get("poison")) or 0
            if save and not meta.get("can_save"):
                save = False
            if poison and (not meta.get("can_poison") or poison not in (allowed or []) or save):
                poison = 0
            data["save"], data["poison"] = save, poison
            return True
        speech = str(data.get("speech") or "").strip()
        if not speech:
            return False
        data["speech"] = speech[:600]
        return True

    def ask_many(self, jobs: list[tuple]) -> list[dict]:
        futures = [self.pool.submit(self.ask, *job[:3], **job[3]) for job in jobs]
        return [f.result() for f in futures]

    def narrate(self, facts: str) -> None:
        text = facts
        if self.narrator:
            self.emit("thinking", "judge", actor=0, kind="narrate", model=self.settings.judge_model)
            try:
                text = self.llm.chat(
                    self.settings.judge_model,
                    [
                        {
                            "role": "system",
                            "content": "你是狼人杀的法官兼旁白。用一两句有画面感、紧张氛围的中文播报，"
                            "必须包含给出的全部事实，绝不能透露任何人的身份。只输出播报内容。",
                        },
                        {"role": "user", "content": facts},
                    ],
                    {"kind": "narrate", "facts": facts},
                ).strip().strip('"') or facts
            except LLMError:
                text = facts
            if len(text) > 200:
                text = facts
        self.emit("narration", desc=f"【法官】{facts}", text=text, facts=facts)

    # ------------------------------------------------------------ flow

    def run(self) -> str:
        try:
            self.emit(
                "game_start",
                "judge",
                players=[p.public() for p in self.players],
                models={"judge": self.settings.judge_model, "players": self.settings.player_models},
            )
            while True:
                self.day += 1
                self.night()
                if self.check_win():
                    break
                self.daytime()
                if self.check_win():
                    break
                if self.day >= 20:
                    self.winner = "draw"
                    break
            self.emit(
                "game_over",
                desc="",
                winner=self.winner,
                roles={p.id: p.role for p in self.players},
                text={"wolf": "狼人阵营获胜", "good": "好人阵营获胜", "draw": "平局"}[self.winner],
            )
            return self.winner
        finally:
            self.pool.shutdown(wait=False)
            self.log.finish()

    def check_win(self) -> bool:
        wolves = self.alive("wolf")
        gods = [p for p in self.alive() if p.role in GODS]
        villagers = self.alive("villager")
        if not wolves:
            self.winner = "good"
        elif not gods or not villagers:
            self.winner = "wolf"
        return self.winner is not None

    def night(self) -> None:
        self.phase = "night"
        self.emit("phase", desc=f"===== 第 {self.day} 夜，天黑请闭眼 =====", phase="night")
        self.narrate(f"第 {self.day} 夜降临，天黑请闭眼。")

        # 守卫
        guarded = None
        for g in self.alive("guard"):
            allowed = [i for i in self.alive_ids() if i != self.last_guarded]
            r = self.ask(g, "守卫请睁眼，你今晚要守护谁？可以空守（填 0）。", "target", allowed, True)
            guarded = r["target"] or None
            self.last_guarded = guarded
            who = self.get(guarded).label() if guarded else "空守"
            self.emit("action", f"p{g.id}", desc=f"第{self.day}夜，你守护了 {who}。",
                      actor=g.id, action="guard", target=guarded, thought=r["thought"])

        # 狼人：先讨论，再投票
        wolves = self.alive("wolf")
        targets = [p.id for p in self.alive() if p.role != "wolf"]
        for w in wolves:
            r = self.ask(w, "狼人请睁眼。和狼队友商量今晚刀谁，说说你的想法和明天白天的打算。",
                         "wolf_chat", targets)
            self.emit("speech", "wolves", desc=f"第{self.day}夜 狼队频道 {w.label()}：{r['speech']}",
                      actor=w.id, kind="wolf_chat", text=r["speech"], thought=r["thought"])
        votes = self.ask_many([(w, "请投票决定今晚刀谁。", "target", {"allowed": targets}) for w in wolves])
        tally = Counter()
        for w, r in zip(wolves, votes):
            tally[r["target"]] += 1
            self.emit("action", "wolves", desc=f"第{self.day}夜 {w.label()} 投票刀 {r['target']}号。",
                      actor=w.id, action="wolf_vote", target=r["target"], thought=r["thought"])
        killed = None
        if tally:
            top = max(tally.values())
            killed = self.rng.choice(sorted(t for t, c in tally.items() if c == top))
            self.emit("action", "wolves", desc=f"第{self.day}夜 狼队决定刀 {self.get(killed).label()}。",
                      action="wolf_kill", target=killed)

        # 女巫
        saved = poisoned = None
        for wi in self.alive("witch"):
            can_save = self.antidote and killed is not None and killed != wi.id
            can_poison = self.poison
            seen = f"今晚被刀的是 {self.get(killed).label()}。" if killed and self.antidote else "你今晚看不到刀口。"
            self.emit("action", f"p{wi.id}", desc=f"第{self.day}夜 法官告诉你：{seen}",
                      action="witch_info", target=killed if self.antidote else None)
            options = []
            if can_save:
                options.append("是否使用解药救他")
            if can_poison:
                options.append("是否使用毒药（填号码，不用填 0）")
            if not options:
                break
            allowed = [i for i in self.alive_ids() if i != wi.id]
            r = self.ask(wi, f"女巫请睁眼。{seen}你{'、'.join(options)}？同一晚不能两瓶药都用。",
                         "witch", allowed, can_save=can_save, can_poison=can_poison)
            if r["save"]:
                saved, self.antidote = killed, False
                self.emit("action", f"p{wi.id}", desc=f"第{self.day}夜 你用解药救了 {self.get(killed).label()}。",
                          actor=wi.id, action="witch_save", target=killed, thought=r["thought"])
            elif r["poison"]:
                poisoned, self.poison = r["poison"], False
                self.emit("action", f"p{wi.id}", desc=f"第{self.day}夜 你毒了 {self.get(poisoned).label()}。",
                          actor=wi.id, action="witch_poison", target=poisoned, thought=r["thought"])
            else:
                self.emit("action", f"p{wi.id}", desc=f"第{self.day}夜 你没有用药。",
                          actor=wi.id, action="witch_pass", thought=r["thought"])

        # 预言家
        for s in self.alive("seer"):
            allowed = [i for i in self.alive_ids() if i != s.id]
            r = self.ask(s, "预言家请睁眼，你今晚要查验谁？", "target", allowed)
            t = self.get(r["target"])
            result = "狼人" if t.role == "wolf" else "好人"
            self.emit("action", f"p{s.id}", desc=f"第{self.day}夜 你查验了 {t.label()}，结果是【{result}】。",
                      actor=s.id, action="seer_check", target=t.id, result=result, thought=r["thought"])

        # 结算
        deaths: list[tuple[int, str]] = []
        if killed is not None:
            protected = (guarded == killed) != (saved == killed)  # 同守同救则死亡
            if not protected:
                deaths.append((killed, "wolf"))
        if poisoned and poisoned not in [d for d, _ in deaths]:
            deaths.append((poisoned, "poison"))
        self.night_deaths = sorted(deaths)

    def daytime(self) -> None:
        self.phase = "day"
        self.emit("phase", desc=f"===== 第 {self.day} 天，天亮了 =====", phase="day")
        deaths = self.night_deaths
        if deaths:
            names = "、".join(self.get(d).label() for d, _ in deaths)
            self.narrate(f"天亮了。昨晚 {names} 死亡。")
        else:
            self.narrate("天亮了。昨晚是平安夜，没有人死亡。")
        for pid, cause in deaths:
            self.kill(pid, cause, announce=False)
        if self.check_win():
            return
        for pid, cause in deaths:
            if self.day == 1:
                self.last_words(self.get(pid))
            if cause == "wolf":
                self.hunter_shot(self.get(pid))
                if self.check_win():
                    return

        # 依次发言
        alive = self.alive()
        start = self.rng.randrange(len(alive))
        order = alive[start:] + alive[:start]
        self.narrate(f"请从 {order[0].label()} 开始依次发言。")
        for p in order:
            r = self.ask(p, "轮到你发言了。", "speech")
            self.emit("speech", desc=f"第{self.day}天 {p.label()} 发言：{r['speech']}",
                      actor=p.id, kind="speech", text=r["speech"], thought=r["thought"])

        exiled = self.vote([p for p in self.alive()], None)
        if exiled is None:
            return
        self.kill(exiled, "vote")
        if self.check_win():
            return
        self.last_words(self.get(exiled))
        self.hunter_shot(self.get(exiled))

    def vote(self, voters: list[Player], candidates: list[int] | None, pk: bool = False) -> int | None:
        self.narrate("平票的玩家进行 PK 发言后重新投票。" if pk else "发言结束，现在开始放逐投票。")
        jobs = []
        for v in voters:
            allowed = [i for i in (candidates or self.alive_ids()) if i != v.id]
            jobs.append((v, "请投票放逐一名玩家（填号码）。", "target", {"allowed": allowed}))
        results = self.ask_many(jobs)
        tally = Counter()
        for v, r in zip(voters, results):
            tally[r["target"]] += 1
            self.emit("vote", desc=f"第{self.day}天 {v.label()} 投给了 {r['target']}号。",
                      actor=v.id, target=r["target"], thought=r["thought"])
        top = max(tally.values())
        leaders = sorted(t for t, c in tally.items() if c == top)
        counts = {str(k): v for k, v in tally.items()}
        if len(leaders) == 1:
            self.emit("vote_result", desc=f"第{self.day}天 投票结果：{self.get(leaders[0]).label()} 被放逐。",
                      counts=counts, exiled=leaders[0], tie=[])
            return leaders[0]
        tie_names = "、".join(f"{t}号" for t in leaders)
        if pk:
            self.emit("vote_result", desc=f"第{self.day}天 再次平票（{tie_names}），今天无人出局。",
                      counts=counts, exiled=None, tie=leaders)
            return None
        self.emit("vote_result", desc=f"第{self.day}天 投票平票：{tie_names}。", counts=counts, exiled=None, tie=leaders)
        for t in leaders:
            p = self.get(t)
            r = self.ask(p, f"你和{tie_names}平票了，请做 PK 发言争取大家的信任。", "pk")
            self.emit("speech", desc=f"第{self.day}天 {p.label()} PK 发言：{r['speech']}",
                      actor=p.id, kind="pk", text=r["speech"], thought=r["thought"])
        others = [p for p in self.alive() if p.id not in leaders]
        if not others:
            return None
        return self.vote(others, leaders, pk=True)

    def kill(self, pid: int, cause: str, announce: bool = True) -> None:
        p = self.get(pid)
        p.alive = False
        how = {"vote": "被放逐出局", "shot": "被猎人带走"}.get(cause, "死亡")
        self.emit("death", desc=f"第{self.day}天 {p.label()} {how}。" if announce else "",
                  player=pid, cause=cause, role=p.role)

    def last_words(self, p: Player) -> None:
        r = self.ask(p, "你已经出局，请留下遗言。", "last_words")
        self.emit("speech", desc=f"第{self.day}天 {p.label()} 的遗言：{r['speech']}",
                  actor=p.id, kind="last_words", text=r["speech"], thought=r["thought"])

    def hunter_shot(self, p: Player) -> None:
        if p.role != "hunter":
            return
        allowed = self.alive_ids()
        r = self.ask(p, "你是猎人，可以开枪带走一名玩家（填号码），也可以不开枪（填 0）。",
                     "target", allowed, True)
        if not r["target"]:
            self.emit("action", desc=f"第{self.day}天 {p.label()} 亮出猎人身份，但没有开枪。",
                      actor=p.id, action="hunter_pass", thought=r["thought"])
            return
        self.emit("action", desc=f"第{self.day}天 {p.label()} 是猎人，开枪带走了 {self.get(r['target']).label()}！",
                  actor=p.id, action="hunter_shot", target=r["target"], thought=r["thought"])
        self.kill(r["target"], "shot")
