"""主持人引擎：一局「找出人类」的状态机。

每件发生的事都记成一条事件，``vis`` 决定谁能看到：
- ``public``：聊天室里所有人
- ``human``：只有真人玩家（轮到你了、你的座位）
- ``judge``：只有观众和结算页（AI 的内心想法、怀疑度、真实身份）
AI 的上下文只由公开事件和它自己的想法拼出来，模型之间不共享对话。
"""

from __future__ import annotations

import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from . import prompts
from .config import Settings
from .llm import LLMError, parse_json

HUMAN_TIMEOUTS = {"answer": 120.0, "discuss": 90.0, "vote": 60.0}


class Aborted(Exception):
    pass


@dataclass
class Seat:
    id: int
    name: str
    glyph: str
    color: str
    kind: str  # human | ai | impostor（观战模式里扮演真人的 AI）
    model: str = ""
    persona: str = ""
    alive: bool = True

    @property
    def is_human(self) -> bool:
        return self.kind in ("human", "impostor")

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "glyph": self.glyph, "color": self.color}

    def secret(self) -> dict:
        return {**self.public(), "kind": self.kind, "model": self.model, "persona": self.persona}


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


class HumanInbox:
    """引擎在这里等真人输入，网页通过 ``submit`` 交上来。"""

    def __init__(self):
        self.cond = threading.Condition()
        self.pending: dict | None = None
        self.value = None
        self.cancelled = False

    def ask(self, request: dict, timeout: float):
        with self.cond:
            self.pending, self.value = request, None
            deadline = time.monotonic() + timeout
            while self.value is None and not self.cancelled:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            value, self.pending = self.value, None
            if self.cancelled:
                raise Aborted()
            return value

    def submit(self, kind: str, value) -> bool:
        with self.cond:
            if not self.pending or self.pending["kind"] != kind or value is None:
                return False
            self.value = value
            self.cond.notify_all()
            return True

    def cancel(self) -> None:
        with self.cond:
            self.cancelled = True
            self.cond.notify_all()


def _leaders(tally: dict[int, int]) -> list[int]:
    top = max(tally.values())
    return [sid for sid, n in tally.items() if n == top]


class Game:
    def __init__(
        self,
        llm,
        settings: Settings,
        log: EventLog | None = None,
        *,
        mode: str = "play",
        lang: str = "zh",
        n_ai: int = 5,
        rounds: int = 3,
        seed: int | None = None,
        inbox: HumanInbox | None = None,
        human_timeouts: dict | None = None,
    ):
        self.llm = llm
        self.settings = settings
        self.log = log or EventLog()
        self.mode = mode if mode in ("play", "watch") else "play"
        self.lang = lang if lang in prompts.QUESTIONS else "zh"
        self.rng = random.Random(seed)
        n_ai = max(2, min(n_ai, len(prompts.SEAT_NAMES) - 1))
        # 至少留下真人和一个 AI 才有意义
        self.rounds = max(1, min(rounds, n_ai - 1))
        self.inbox = inbox or HumanInbox()
        self.human_timeouts = {**HUMAN_TIMEOUTS, **(human_timeouts or {})}
        self.round = 0
        self.phase = "setup"
        self.winner: str | None = None
        self.aborted = False
        self.seats = self._deal(n_ai)
        self.questions = self.rng.sample(prompts.QUESTIONS[self.lang], self.rounds)
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.concurrency))
        self.call_pool = ThreadPoolExecutor(max_workers=max(2, settings.concurrency * 2))
        # 每轮每个 AI 对真人的怀疑度，结算页画曲线用
        self.suspicion: dict[int, list[int | None]] = {}
        self.ballots: list[dict] = []

    # ------------------------------------------------------------ setup

    def _deal(self, n_ai: int) -> list[Seat]:
        n = n_ai + 1
        models = self.settings.models or ["mock"]
        human_pos = self.rng.randrange(n)
        seats = []
        model_cycle = list(models)
        self.rng.shuffle(model_cycle)
        k = 0
        for pos in range(n):
            seat = Seat(pos + 1, prompts.SEAT_NAMES[pos], prompts.SEAT_GLYPHS[pos], prompts.SEAT_COLORS[pos], "ai")
            if pos == human_pos:
                if self.mode == "watch":
                    seat.kind = "impostor"
                    seat.model = self.rng.choice(models)
                    seat.persona = self.rng.choice(prompts.IMPOSTOR_PERSONAS)
                else:
                    seat.kind = "human"
            else:
                seat.model = model_cycle[k % len(model_cycle)]
                k += 1
            seats.append(seat)
        return seats

    def get(self, sid: int) -> Seat:
        return self.seats[sid - 1]

    def by_name(self, name) -> Seat | None:
        key = str(name or "").strip().lower()
        for s in self.seats:
            if s.name.lower() == key or str(s.id) == key:
                return s
        return None

    def alive(self) -> list[Seat]:
        return [s for s in self.seats if s.alive]

    @property
    def human(self) -> Seat:
        return next(s for s in self.seats if s.is_human)

    # ------------------------------------------------------------ events

    def emit(self, type_: str, vis: str = "public", desc: str = "", **data) -> dict:
        return self.log.append(
            {"type": type_, "vis": vis, "round": self.round, "phase": self.phase, "desc": desc,
             "t": round(time.time(), 2), **data}
        )

    def history_for(self, seat: Seat) -> str:
        lines = []
        for e in self.log.events:
            if e["vis"] == "public" and e.get("desc"):
                lines.append(e["desc"])
            elif e["type"] == "thought" and e.get("actor") == seat.id:
                lines.append(f"（你当时心里想：{e['text']}）")
        return "\n".join(lines[-120:]) or "（游戏刚刚开始）"

    def check_abort(self) -> None:
        if self.aborted:
            raise Aborted()

    # ------------------------------------------------------------ agents

    def _chat(self, model: str, messages: list[dict], meta: dict) -> str:
        """给单次调用加墙钟上限：socket 超时只管单次读写，这里管总时长。"""
        future = self.call_pool.submit(self.llm.chat, model, messages, meta)
        try:
            return future.result(timeout=self.settings.timeout + 5)
        except FutureTimeout as exc:
            raise LLMError(f"模型调用超过 {self.settings.timeout:.0f} 秒没有返回") from exc

    def ask_ai(self, seat: Seat, kind: str, task: str, candidates: list[str]) -> dict:
        roster = [s.name for s in self.seats]
        messages = [
            {"role": "system", "content": prompts.system_prompt(
                seat.name, roster, self.lang, self.rounds, seat.persona if seat.kind == "impostor" else "")},
            {"role": "user", "content": (
                f"现在是第 {self.round} 轮。仍在场：{'、'.join(s.name for s in self.alive())}。\n"
                f"到目前为止聊天室里发生的事：\n{self.history_for(seat)}\n\n{task}"
            )},
        ]
        meta = {"kind": kind, "candidates": candidates, "seat": seat.name}
        error, raw = "", ""
        for attempt in range(2):
            started = time.monotonic()
            try:
                raw = self._chat(seat.model, messages, meta)
                data = parse_json(raw)
            except LLMError as exc:
                error = str(exc)
                self._trace(seat, kind, started, f"失败（第 {attempt + 1} 次）：{error[:200]}")
                if "秒没有返回" in error:
                    break  # 超时不再重试，直接用兜底
                continue
            except ValueError:
                error = f"输出不是 JSON：{raw[:120]!r}"
                self._trace(seat, kind, started, f"失败（第 {attempt + 1} 次）：{error}")
                continue
            self._trace(seat, kind, started, "ok")
            if self._valid(data, kind, candidates):
                data["thought"] = str(data.get("thought") or "")[:400]
                data["ms"] = int((time.monotonic() - started) * 1000)
                return data
            error = f"非法输出：{str(data)[:160]}"
        self.emit("system", "judge", text=f"{seat.name}（{seat.model}）调用失败，使用兜底：{error[:160]}")
        if kind == "vote":
            return {"thought": "（掉线）", "vote": self.rng.choice(candidates), "reason": "", "suspicion": {},
                    "fallback": True}
        return {"thought": "（掉线）", "speech": self.rng.choice(prompts.FALLBACK[self.lang]), "fallback": True}

    def _valid(self, data: dict, kind: str, candidates: list[str]) -> bool:
        if kind == "vote":
            target = self.by_name(data.get("vote"))
            if not target or target.name not in candidates:
                return False
            data["vote"] = target.name
            scores = {}
            raw = data.get("suspicion")
            if isinstance(raw, dict):
                for k, v in raw.items():
                    s = self.by_name(k)
                    try:
                        scores[s.name] = max(0, min(100, int(float(v))))
                    except (AttributeError, TypeError, ValueError):
                        continue
            data["suspicion"] = scores
            data["reason"] = str(data.get("reason") or "").strip()[:120]
            return True
        speech = str(data.get("speech") or "").strip()
        if not speech:
            return False
        data["speech"] = speech.replace("**", "")[:400]
        return True

    @staticmethod
    def _trace(seat: Seat, kind: str, started: float, result: str) -> None:
        elapsed = time.monotonic() - started
        print(f"[{time.strftime('%H:%M:%S')}] {seat.name} {kind} {seat.model} {elapsed:.1f}s {result}",
              file=sys.stderr, flush=True)

    def ask_human(self, kind: str, **extra):
        timeout = self.human_timeouts[kind]
        self.emit("your_turn", "human", kind=kind, timeout=timeout, deadline=time.time() + timeout, **extra)
        value = self.inbox.ask({"kind": kind, **extra}, timeout)
        self.emit("turn_done", "human", kind=kind)
        self.check_abort()
        return value

    def act(self, seat: Seat, kind: str, task: str, targets: list[str] | None = None) -> dict:
        """统一入口：真人走网页输入，其余走模型。"""
        candidates = [n for n in (targets or [s.name for s in self.alive()]) if n != seat.name]
        if seat.kind != "human":
            return self.ask_ai(seat, kind, task, candidates)
        if kind == "vote":
            target = self.by_name(self.ask_human("vote", candidates=candidates))
            if not target or target.name not in candidates:
                return {"thought": "", "vote": None, "reason": "", "suspicion": {}, "timeout": True}
            return {"thought": "", "vote": target.name, "reason": "", "suspicion": {}}
        text = str(self.ask_human(kind) or "").strip()[:400]
        return {"thought": "", "speech": text or prompts.TIMEOUT_SPEECH[self.lang], "timeout": not text}

    def act_all(self, seats: list[Seat], kind: str, task_for, targets: list[str] | None = None) -> dict[int, dict]:
        """同时行动：AI 并发调用，真人在当前线程里等网页输入。"""
        futures = {s.id: self.pool.submit(self.act, s, kind, task_for(s), targets)
                   for s in seats if s.kind != "human"}
        results = {s.id: self.act(s, kind, task_for(s), targets) for s in seats if s.kind == "human"}
        for sid, f in futures.items():
            results[sid] = f.result()
        self.check_abort()
        return results

    # ------------------------------------------------------------ flow

    def run(self) -> str:
        try:
            self._run()
        except Aborted:
            self.winner = None
            self.emit("aborted", text="本局已中止")
        finally:
            self.log.finish()
            self.pool.shutdown(wait=False)
            self.call_pool.shutdown(wait=False)
        return self.winner or "aborted"

    def _run(self) -> None:
        self.emit("setup", seats=[s.public() for s in self.seats], mode=self.mode, lang=self.lang,
                  rounds=self.rounds, desc="")
        if self.mode == "play":
            self.emit("you", "human", seat=self.human.id)
        self.emit("secret", "judge", seats=[s.secret() for s in self.seats])

        for r in range(1, self.rounds + 1):
            self.round = r
            self.phase = "answer"
            question = self.questions[r - 1]
            self.emit("round", desc=f"—— 第 {r} 轮 —— 主持人出题：「{question}」", question=question,
                      total=self.rounds)
            self._answers(question)
            self.phase = "discuss"
            self.emit("phase", desc="", text="自由发言")
            self._discuss()
            self.phase = "vote"
            self.emit("phase", desc="", text="投票")
            out = self._vote()
            if out and out.is_human:
                self.winner = "ai"
                break
        else:
            self.winner = "human"
        self.phase = "end"
        self.emit("end", **self.summary())

    def _answers(self, question: str) -> None:
        alive = self.alive()
        results = self.act_all(alive, "answer", lambda s: prompts.answer_task(question))
        order = alive[:]
        self.rng.shuffle(order)
        for s in order:
            self._say(s, results[s.id], "作答")

    def _discuss(self) -> None:
        order = self.alive()
        self.rng.shuffle(order)
        for s in order:
            self.check_abort()
            self.emit("typing", actor=s.id)
            self._say(s, self.act(s, "discuss", prompts.discuss_task()), "")

    def _say(self, s: Seat, data: dict, label: str) -> None:
        who = f"{s.name}（{label}）" if label else s.name
        self.emit("say", desc=f"{who}：{data['speech']}", actor=s.id, text=data["speech"])
        if data.get("thought"):
            self.emit("thought", "judge", actor=s.id, text=data["thought"], ms=data.get("ms"))

    def _vote(self) -> Seat | None:
        alive = self.alive()
        tally = self._ballot(alive, [s for s in alive], prompts.vote_task, "投票结果")
        if not tally:
            self.emit("tie", desc="没有人投票，无人出局。", tied=[])
            return None
        leaders = _leaders(tally)
        if len(leaders) > 1:
            names = "、".join(self.get(i).name for i in leaders)
            self.emit("tie", desc=f"{names} 平票，其他人在他们之间重新投票。", tied=leaders, revote=True)
            tied = [self.get(i) for i in leaders]
            voters = [s for s in alive if s.id not in leaders]
            tally = self._ballot(voters, tied, prompts.revote_task, "重新投票", revote=True) if voters else {}
            leaders = _leaders(tally) if tally else leaders
            if len(leaders) > 1:
                names = "、".join(self.get(i).name for i in leaders)
                self.emit("tie", desc=f"{names} 仍然平票，本轮无人出局。", tied=leaders)
                return None
        out = self.get(leaders[0])
        out.alive = False
        identity = "人类" if out.is_human else "AI"
        self.emit("eliminated", desc=f"{out.name} 以 {tally[out.id]} 票出局，身份是 {identity}。", target=out.id,
                  votes=tally[out.id], identity="human" if out.is_human else "ai")
        return out

    def _ballot(self, voters: list[Seat], targets: list[Seat], make_task, title: str,
                revote: bool = False) -> dict[int, int]:
        def task(s: Seat) -> str:
            return make_task([x.name for x in targets if x.id != s.id])

        results = self.act_all(voters, "vote", task, [x.name for x in targets])
        human = self.human
        ballots = []
        for s in voters:
            data = results[s.id]
            target = self.by_name(data.get("vote")) if data.get("vote") else None
            ballots.append({"voter": s.id, "target": target.id if target else None, "reason": data.get("reason", "")})
            if s.kind != "human":
                self.emit("suspicion", "judge", actor=s.id, scores=data.get("suspicion", {}),
                          thought=data.get("thought", ""), revote=revote)
            if not s.is_human:
                if not revote:
                    self.suspicion.setdefault(s.id, []).append(data.get("suspicion", {}).get(human.name))
                self.ballots.append({"round": self.round, "voter": s.id, "model": s.model, "revote": revote,
                                     "hit": bool(target and target.id == human.id)})
        parts = []
        for b in ballots:
            voter = self.get(b["voter"]).name
            if b["target"] is None:
                parts.append(f"{voter} 弃票")
            else:
                reason = f"（{b['reason']}）" if b["reason"] else ""
                parts.append(f"{voter}→{self.get(b['target']).name}{reason}")
        tally = Counter(b["target"] for b in ballots if b["target"] is not None)
        self.emit("votes", desc=f"{title}：" + "；".join(parts), ballots=ballots, tally=dict(tally), revote=revote)
        return dict(tally)

    # ------------------------------------------------------------ result

    def summary(self) -> dict:
        models: dict[str, dict] = {}
        for b in self.ballots:
            m = models.setdefault(b["model"], {"votes": 0, "hits": 0})
            m["votes"] += 1
            m["hits"] += int(b["hit"])
        human = self.human
        return {
            "winner": self.winner,
            "mode": self.mode,
            "lang": self.lang,
            "human": human.id,
            "impostor_model": human.model if human.kind == "impostor" else "",
            "rounds_played": self.round,
            "rounds": self.rounds,
            "seats": [s.secret() | {"alive": s.alive} for s in self.seats],
            "suspicion": {str(k): v for k, v in self.suspicion.items()},
            "model_stats": models,
            "desc": "",
        }
