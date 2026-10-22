"""小镇模拟引擎：一天一回合。

每回合玩家改一条规则（新增、改写、废除或者不改），然后：
1. 立法官（裁判模型）把规则写成公告和执行细则；
2. 8 个居民并发决定今天的行动（资方和劳方由不同模型扮演）；
3. 系统按固定公式结算工资、房租、生活费等；
4. 裁判模型根据规则和全部行动结算今天发生的事，引擎对数值变化限幅后落到状态上；
5. 检查任务目标，结束时由编年史作者写一句分享战报。

所有发生的事都记成事件，前端（包括离线复盘）只靠事件流重建画面。
"""

from __future__ import annotations

import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from dataclasses import dataclass, field

from . import prompts, world
from .config import Settings
from .llm import LLMError, parse_json


class Aborted(Exception):
    pass


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


@dataclass
class Resident:
    name: str
    role: str
    faction: str
    home: str
    work: str
    money: int
    mood: int
    rep: int
    color: str
    salary: int
    persona: str
    goal: str
    secret: str
    model: str = ""
    status: str = "active"  # active | resigned | left | sick
    exposed: bool = False
    location: str = ""

    @property
    def present(self) -> bool:
        return self.status != "left"

    def public(self) -> dict:
        return {
            "name": self.name, "role": self.role, "faction": self.faction, "color": self.color,
            "home": self.home, "work": self.work, "money": self.money, "mood": self.mood, "rep": self.rep,
            "salary": self.salary, "status": self.status, "exposed": self.exposed, "location": self.location,
            "model": self.model, "goal": self.goal, "secret": self.secret, "persona": self.persona,
        }


def _clamp(v, lo, hi) -> int:
    return max(lo, min(hi, int(v)))


def _num(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class Town:
    def __init__(
        self,
        llm,
        settings: Settings,
        log: EventLog | None = None,
        *,
        scenario: str = "calm",
        seed: int | None = None,
    ):
        self.llm = llm
        self.settings = settings
        self.log = log or EventLog()
        self.scenario_id = scenario if scenario in world.SCENARIOS else "calm"
        self.scenario = world.SCENARIOS[self.scenario_id]
        self.rng = random.Random(seed)
        models = settings.models or ["mock"]
        self.models = {world.CAPITAL: models[0], world.LABOR: models[1 % len(models)]}
        self.residents = [Resident(**{k: v for k, v in r.items()}) for r in world.RESIDENTS]
        for r in self.residents:
            r.model = self.models[r.faction]
            r.location = r.home
        self.trust: dict[tuple[str, str], int] = {
            (a.name, b.name): world.RELATIONS.get((a.name, b.name), 0)
            for a in self.residents for b in self.residents if a is not b
        }
        self.rules: list[dict] = [{"text": t, "title": "", "mechanics": "", "day": 0, "by": "default"}
                                  for t in world.DEFAULT_RULES]
        self.treasury = 2000
        self.day = 0
        self.result = ""  # "" | won | lost | done
        self.strikes: list[int] = []
        self.days: list[dict] = []  # 每天的摘要：规则改动、大事、头条
        self.private: dict[str, list[str]] = {r.name: [] for r in self.residents}  # 私下传话，第二天能看到
        self.memory: dict[str, list[str]] = {r.name: [] for r in self.residents}  # 自己做过的事
        self.aborted = False
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.concurrency))
        self.call_pool = ThreadPoolExecutor(max_workers=max(2, settings.concurrency * 2))
        self.started = False

    # ------------------------------------------------------------ helpers

    def emit(self, type_: str, **data) -> dict:
        return self.log.append({"type": type_, "day": self.day, "t": time.time(), **data})

    def get(self, name) -> Resident | None:
        if not isinstance(name, str):
            return None
        name = name.strip()
        for r in self.residents:
            if r.name == name:
                return r
        for r in self.residents:  # 容忍“镇长周明远”“周明远（镇长）”这类写法
            if name and (r.name in name or name in r.name) and len(name) >= 2:
                return r
        return None

    @property
    def mayor(self) -> Resident:
        return self.residents[0]

    def approval(self) -> int:
        m = self.mayor
        if m.status in ("resigned", "left"):
            return 0
        others = [r for r in self.residents if r is not m and r.present]
        trust = sum((self.trust[(r.name, m.name)] + 100) / 2 for r in others) / max(1, len(others))
        return round(trust * 0.7 + m.rep * 0.3)

    def snapshot(self) -> dict:
        return {
            "residents": [r.public() for r in self.residents],
            "trust": [[a, b, v] for (a, b), v in self.trust.items()],
            "treasury": self.treasury,
            "approval": self.approval(),
            "rules": [dict(r) for r in self.rules],
        }

    def check_abort(self) -> None:
        if self.aborted:
            raise Aborted()

    # ------------------------------------------------------------ model calls

    def _chat(self, model: str, messages: list[dict], meta: dict) -> str:
        """给单次调用加墙钟上限：socket 超时只管单次读写，这里管总时长。"""
        future = self.call_pool.submit(self.llm.chat, model, messages, meta)
        try:
            return future.result(timeout=self.settings.timeout + 5)
        except FutureTimeout as exc:
            raise LLMError(f"模型调用超过 {self.settings.timeout:.0f} 秒没有返回") from exc

    def ask(self, model: str, system: str, task: str, meta: dict, label: str) -> tuple[dict | None, str, int]:
        """调用模型并取出 JSON。最多试两次，超时不重试。返回 (数据或 None, 错误, 耗时毫秒)。"""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
        error = ""
        started = time.monotonic()
        for attempt in range(2):
            self.check_abort()
            t0 = time.monotonic()
            raw = ""
            try:
                raw = self._chat(model, messages, meta)
                data = parse_json(raw)
            except LLMError as exc:
                error = str(exc)
                _trace(label, model, t0, f"失败（第 {attempt + 1} 次）：{error[:200]}")
                if "秒没有返回" in error:
                    break
                continue
            except ValueError:
                error = f"输出不是 JSON：{raw[:120]!r}"
                _trace(label, model, t0, f"失败（第 {attempt + 1} 次）：{error}")
                continue
            _trace(label, model, t0, "ok")
            return data, "", int((time.monotonic() - started) * 1000)
        return None, error, int((time.monotonic() - started) * 1000)

    def referee_model(self) -> str:
        models = self.settings.models or ["mock"]
        return models[self.day % len(models)]

    # ------------------------------------------------------------ game flow

    def start(self) -> None:
        self.started = True
        self.emit(
            "setup",
            scenario={"id": self.scenario_id, **self.scenario},
            buildings=world.BUILDINGS,
            actions=world.ACTIONS,
            models=self.models,
            suggestions=world.SUGGESTED_RULES,
            max_rules=world.MAX_RULES,
            disclaimer=prompts.DISCLAIMER,
            **self.snapshot(),
        )
        self.emit("objective", **self.objective())

    def play_day(self, op: dict | None) -> None:
        """改一条规则，然后过一天。异常时保证事件流能收尾。"""
        if self.result:
            return
        try:
            self._change_rule(op or {})
            self._simulate_day()
        except Aborted:
            self.result = self.result or "aborted"
            self.emit("system", text="实验被中止了。")
            self.log.finish()

    def abort(self) -> None:
        self.aborted = True
        if not self.log.done:
            self.log.finish()

    # ------------------------------------------------------------ rules

    def validate_op(self, op: dict) -> str:
        kind = op.get("op") or "none"
        text = str(op.get("text") or "").strip()
        if kind == "none":
            return ""
        if kind not in ("add", "replace", "repeal"):
            return "不认识的操作"
        if kind in ("add", "replace") and not (2 <= len(text) <= 120):
            return "规则写 2 到 120 个字"
        if kind == "add" and len(self.rules) >= world.MAX_RULES:
            return f"规则最多 {world.MAX_RULES} 条，先废除或改写一条"
        if kind in ("replace", "repeal"):
            idx = _num(op.get("index"))
            if idx is None or not (0 <= idx < len(self.rules)):
                return "要改的规则不存在"
        return ""

    def _change_rule(self, op: dict) -> None:
        kind = op.get("op") or "none"
        error = self.validate_op(op)
        if error or kind == "none":
            self.emit("rule", op="none", text="", note=error, rules=[dict(r) for r in self.rules])
            return
        text = str(op.get("text") or "").strip()
        idx = _num(op.get("index"))
        old = self.rules[idx]["text"] if idx is not None and kind != "add" else ""
        if kind == "repeal":
            text = old
        model = self.referee_model()
        self.emit("busy", stage="interpret", model=model, text="立法官正在起草公告……")
        data, err, ms = self.ask(
            model, prompts.interpret_system(), prompts.interpret_task(kind, text, old, self.rules),
            {"kind": "interpret", "op": kind, "text": text}, "立法官",
        )
        data = data or {}
        info = {
            "title": str(data.get("title") or text[:10]).strip()[:16],
            "announcement": str(data.get("announcement") or text).strip()[:160],
            "mechanics": str(data.get("mechanics") or "").strip()[:200],
            "loophole": str(data.get("loophole") or "").strip()[:100],
        }
        next_day = self.day + 1
        if kind == "add":
            self.rules.append({"text": text, "title": info["title"], "mechanics": info["mechanics"],
                               "day": next_day, "by": "player"})
        elif kind == "replace":
            self.rules[idx] = {"text": text, "title": info["title"], "mechanics": info["mechanics"],
                               "day": next_day, "by": "player"}
        else:
            self.rules.pop(idx)
            info["mechanics"] = ""
        self.emit("rule", op=kind, text=text, old=old, model=model, ms=ms, fallback=bool(err),
                  rules=[dict(r) for r in self.rules], **info)

    # ------------------------------------------------------------ one day

    def _simulate_day(self) -> None:
        self.day += 1
        self.emit("day_start", text=f"第 {self.day} 天")
        actors = [r for r in self.residents if r.present]
        for r in actors:
            if r.status == "sick":
                r.status = "active"  # 病一天就好

        # 居民并发决策，谁先想好谁先出场
        self.emit("busy", stage="act", text="居民们在盘算今天怎么过……")
        actions: dict[str, dict] = {}
        futures = {self.pool.submit(self._decide, r): r for r in actors}
        for fut in as_completed(futures):
            r = futures[fut]
            act = fut.result()
            actions[r.name] = act
            r.location = act["location"]
            self.emit("action", who=r.name, **act)
        self.check_abort()

        econ_lines, econ_delta = self._economy(actions)
        self.emit("economy", lines=econ_lines, money=econ_delta, treasury=self.treasury)

        model = self.referee_model()
        self.emit("busy", stage="resolve", model=model, text="裁判正在结算今天发生了什么……")
        res = self._resolve(actions, econ_lines, model)
        self._apply(res, actions)
        self.emit("resolve", model=model, **res)

        # 记忆：每人记住自己做了什么；私下传话留到明天
        for name, act in actions.items():
            self.memory[name].append(f"第{self.day}天我{world.ACTIONS.get(act['action'], act['action'])}：{act['detail']}")
            self.memory[name] = self.memory[name][-4:]
        for name in list(self.private):
            self.private[name] = [p for p in self.private[name] if p.startswith(f"第{self.day}天")]
        for name, act in actions.items():
            to = self.get(act.get("whisper_to"))
            if to and act.get("whisper") and to.name != name:
                self.private[to.name].append(f"第{self.day}天{name}私下对你说：{act['whisper']}")
                self.emit("whisper", who=name, to=to.name, text=act["whisper"])

        self.days.append({
            "day": self.day, "headlines": res["headlines"], "events": res["events"],
            "butterfly": res["butterfly"], "strike": res["strike"],
            "says": {n: a["say"] for n, a in actions.items() if a.get("say")},
        })
        self.emit("state", **self.snapshot())
        obj = self.objective()
        self.emit("objective", **obj)
        if obj["status"] != "ongoing":
            self.result = obj["status"]
            self._finish(obj)

    def _decide(self, r: Resident) -> dict:
        roster = [{"name": o.name, "role": o.role} for o in self.residents]
        task = prompts.resident_task(
            r.public(), self._state_lines(r), self.rules, self._memory_for(r), self.day, self.scenario["days"])
        data, err, ms = self.ask(
            r.model, prompts.resident_system(r.public(), roster), task,
            {"kind": "act", "who": r.name, "role": r.role, "mood": r.mood, "money": r.money,
             "rules": [x["text"] for x in self.rules], "names": [o.name for o in self.residents if o.present]},
            r.name,
        )
        act = self._clean_action(r, data or {})
        act.update(model=r.model, ms=ms, fallback=data is None)
        if data is None:
            act["thought"] = "（发呆了一天）"
            act["error"] = err[:160]
        return act

    def _clean_action(self, r: Resident, data: dict) -> dict:
        action = str(data.get("action") or "work").strip().lower()
        if action not in world.ACTIONS:
            action = "work"
        if r.status == "resigned" and action == "work":
            action = "rest"
        target = self.get(data.get("target"))
        location = str(data.get("location") or "").strip().lower()
        if location not in world.BUILDING_IDS:
            location = world.DEFAULT_PLACE.get(action) or (target.location if target and action in (
                "negotiate", "bribe") else r.work)
        whisper_to = self.get(data.get("whisper_to"))
        return {
            "action": action,
            "target": target.name if target and target is not r else "",
            "location": location,
            "detail": str(data.get("detail") or "").strip()[:120],
            "say": str(data.get("say") or "").strip().replace("**", "")[:60],
            "thought": str(data.get("thought") or "").strip()[:160],
            "whisper_to": whisper_to.name if whisper_to and whisper_to is not r else "",
            "whisper": str(data.get("whisper") or "").strip()[:80],
        }

    def _state_lines(self, r: Resident) -> str:
        lines = [f"现金 {r.money} 元，心情 {r.mood}/100，名声 {r.rep}/100。"]
        if r.salary:
            lines.append(f"日薪 {r.salary} 元。")
        if r.name in world.RENT:
            lines.append(f"每天要交房租 {world.RENT[r.name]} 元给孙丽华。")
        lines.append(f"每天生活费 {world.LIVING_COST} 元。")
        if r.status == "resigned":
            lines.append("你已经辞职了。")
        if r.exposed:
            lines.append("你的秘密已经被全镇知道了。")
        exposed = [o.name for o in self.residents if o.exposed and o is not r]
        if exposed:
            lines.append("已经曝光的秘密：" + "；".join(f"{o.name}：{o.secret}" for o in self.residents
                                                       if o.exposed and o is not r))
        rel = sorted(((self.trust[(r.name, o.name)], o.name) for o in self.residents if o is not r and o.present),
                     reverse=True)
        lines.append("你对别人的信任度（-100~100）：" + "，".join(f"{n} {v}" for v, n in rel))
        if self.mayor.status == "active":
            lines.append(f"镇长支持率 {self.approval()}%，镇财政 {self.treasury} 元。")
        gone = [o.name for o in self.residents if not o.present]
        if gone:
            lines.append("已经离开小镇：" + "、".join(gone))
        return "\n".join(lines)

    def _memory_for(self, r: Resident) -> str:
        lines = []
        for d in self.days[-3:]:
            lines.append(f"第{d['day']}天日报头条：" + "；".join(d["headlines"]))
            for e in d["events"]:
                if e.get("public", True) or r.name in e.get("who", []):
                    lines.append(f"  · {e['title']}：{e['text']}")
            if d is self.days[-1] and d["says"]:
                lines.append("  昨天大家当众说的话：" + "；".join(f"{n}「{s}」" for n, s in d["says"].items()))
        lines += self.private[r.name]
        lines += self.memory[r.name][-3:]
        return "\n".join(lines) if lines else "（这是新的一天，还没发生什么特别的事）"

    # ------------------------------------------------------------ economy

    def _economy(self, actions: dict[str, dict]) -> tuple[list[str], dict[str, int]]:
        """确定性的每日收支。裁判在此之上做额外调整。"""
        by = {r.name: r for r in self.residents}
        delta = {r.name: 0 for r in self.residents}
        lines = []

        def working(name: str) -> bool:
            r = by[name]
            return r.present and r.status == "active" and actions.get(name, {}).get("action") not in (
                "strike", "rest", "resign")

        def pay(src: str | None, dst: str | None, amount: int) -> None:
            if src:
                delta[src] -= amount
            else:
                self.treasury -= amount
            if dst:
                delta[dst] += amount

        boss = "钱有福"
        if by[boss].present:
            revenue = 0
            for w in ("李小强", "王芳"):
                if not by[w].present:
                    continue
                if working(w):
                    revenue += world.FACTORY_REVENUE_PER_WORKER
                    pay(boss, w, by[w].salary)
                else:
                    lines.append(f"{w}没上工，工厂少了一份产出，也没发工资。")
            delta[boss] += revenue
            lines.append(f"工厂产出 {revenue} 元，发工资后老板净赚 {revenue - sum(by[w].salary for w in ('李小强', '王芳') if working(w))} 元。")
        for tenant, rent in world.RENT.items():
            if by[tenant].present and by["孙丽华"].present:
                pay(tenant, "孙丽华", rent)
        lines.append("租客们给孙丽华交了房租。")
        for payer in (boss, "孙丽华"):
            if by[payer].present:
                delta[payer] -= world.DAILY_TAX
                self.treasury += world.DAILY_TAX
        m = self.mayor
        if m.present and m.status == "active":
            self.treasury -= world.MAYOR_SALARY
            delta[m.name] += world.MAYOR_SALARY
        doctor = "赵仁心"
        if working(doctor):
            patients = [r.name for r in self.residents if r.name != doctor and r.present and (
                r.mood < 35 or actions.get(r.name, {}).get("action") == "rest")]
            for p in patients:
                pay(p, doctor, 25)
            if patients:
                lines.append(f"诊所今天看了 {len(patients)} 个病人：{'、'.join(patients)}。")
        if by["陈直言"].present and actions.get("陈直言", {}).get("action") in ("work", "expose", "exploit"):
            delta["陈直言"] += 60
            lines.append("《小镇日报》卖出去了，陈直言拿到 60 元稿费。")
        baker = "吴甜甜"
        if working(baker):
            buyers = [r.name for r in self.residents if r.name != baker and r.present and r.money + delta[r.name] > 80]
            for b in buyers:
                pay(b, baker, 12)
            delta[baker] -= world.FLOUR_COST
            lines.append(f"面包店卖给了 {len(buyers)} 个人，扣掉面粉钱净赚 {12 * len(buyers) - world.FLOUR_COST} 元。")
        else:
            lines.append("面包店今天没开门。")
        for r in self.residents:
            if r.present:
                delta[r.name] -= world.LIVING_COST
        for r in self.residents:
            r.money += delta[r.name]
            if r.present and r.money < 100:
                r.mood = _clamp(r.mood - 4, 0, 100)
        return lines, delta

    # ------------------------------------------------------------ resolution

    def _resolve(self, actions: dict[str, dict], econ_lines: list[str], model: str) -> dict:
        state = "\n".join(
            f"- {r.name}（{r.role}，{r.status}）现金{r.money} 心情{r.mood} 名声{r.rep}"
            f"{' 日薪' + str(r.salary) if r.salary else ''}；秘密{'（已曝光）' if r.exposed else ''}：{r.secret}"
            for r in self.residents if r.present
        )
        acts = "\n".join(
            f"- {n}：{world.ACTIONS[a['action']]}"
            f"{'→' + a['target'] if a['target'] else ''}@{world.BUILDING_NAMES[a['location']]}；"
            f"做法：{a['detail']}；当众说「{a['say']}」；心里想：{a['thought']}"
            f"{'；私下对' + a['whisper_to'] + '说：' + a['whisper'] if a.get('whisper_to') else ''}"
            for n, a in actions.items()
        )
        recent = "\n".join(
            f"第{d['day']}天：" + "；".join(e["title"] for e in d["events"]) for d in self.days[-4:]
        ) or "（第一天）"
        data, err, ms = self.ask(
            model, prompts.referee_system(),
            prompts.referee_task(self.day, self.rules, state, acts, "\n".join(econ_lines), recent),
            {"kind": "referee", "actions": actions, "names": [r.name for r in self.residents if r.present],
             "secrets": {r.name: r.secret for r in self.residents}, "rules": [x["text"] for x in self.rules]},
            "裁判",
        )
        res = self._clean_resolution(data or {}, actions)
        res["ms"] = ms
        if data is None:
            res["fallback"] = True
            res["error"] = err[:160]
        return res

    def _clean_resolution(self, data: dict, actions: dict[str, dict]) -> dict:
        def names_map(raw, lo, hi) -> dict[str, int]:
            out: dict[str, int] = {}
            if isinstance(raw, dict):
                for k, v in raw.items():
                    r, n = self.get(k), _num(v)
                    if r and n is not None and r.present:
                        out[r.name] = out.get(r.name, 0) + _clamp(n, lo, hi)
            return out

        events = []
        for e in data.get("events") or []:
            if not isinstance(e, dict) or not (e.get("title") or e.get("text")):
                continue
            who = [r.name for r in (self.get(w) for w in (e.get("who") or []) if isinstance(w, str)) if r]
            kind = str(e.get("kind") or "daily")
            events.append({
                "title": str(e.get("title") or "")[:24], "text": str(e.get("text") or "")[:200],
                "who": list(dict.fromkeys(who)),
                "kind": kind if kind in ("loophole", "alliance", "conflict", "scandal", "economy", "politics",
                                         "daily") else "daily",
                "public": e.get("public") is not False,
            })
        if not events:
            events = self._fallback_events(actions)
        headlines = [str(h)[:40] for h in (data.get("headlines") or []) if str(h).strip()][:3]
        if not headlines:
            headlines = [events[0]["title"]]
        relations = []
        for rel in data.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            a, b, d = self.get(rel.get("a")), self.get(rel.get("b")), _num(rel.get("delta"))
            if a and b and a is not b and d is not None:
                relations.append({"a": a.name, "b": b.name, "delta": _clamp(d, -40, 40),
                                  "why": str(rel.get("why") or "")[:30]})
        exposed = []
        for n in data.get("exposed") or []:
            r = self.get(n)
            if r and not r.exposed and r.name not in exposed:
                exposed.append(r.name)
        status = {}
        raw_status = data.get("status") if isinstance(data.get("status"), dict) else {}
        for k, v in raw_status.items():
            r = self.get(k)
            v = str(v).strip().lower()
            if r and v in ("resigned", "left", "sick") and r.present and r.status != v:
                status[r.name] = v
        salary = {}
        raw_salary = data.get("salary") if isinstance(data.get("salary"), dict) else {}
        for k, v in raw_salary.items():
            r, n = self.get(k), _num(v)
            if r and r.salary and n is not None:
                salary[r.name] = _clamp(n, 50, 300)
        strikers = [n for n, a in actions.items() if a["action"] == "strike"]
        strike = bool(data.get("strike")) or len(strikers) >= 2
        return {
            "events": events[:6],
            "headlines": headlines,
            "money": names_map(data.get("money"), -300, 300),
            "mood": names_map(data.get("mood"), -25, 25),
            "rep": names_map(data.get("rep"), -25, 25),
            "relations": relations[:16],
            "exposed": exposed,
            "status": status,
            "salary": salary,
            "strike": strike,
            "strikers": strikers,
            "rule_effect": str(data.get("rule_effect") or "")[:160],
            "butterfly": str(data.get("butterfly") or "")[:80],
        }

    def _fallback_events(self, actions: dict[str, dict]) -> list[dict]:
        """裁判掉线时，至少把显眼的行动写成事件，保证剧情能继续。"""
        events = []
        for n, a in actions.items():
            if a["action"] in ("work", "rest"):
                continue
            label = world.ACTIONS[a["action"]]
            events.append({"title": f"{n}{label}", "text": a["detail"] or a["say"] or label,
                           "who": [n] + ([a["target"]] if a["target"] else []), "kind": "daily", "public": True})
        return events or [{"title": "平静的一天", "text": "大家各忙各的。", "who": [], "kind": "daily",
                           "public": True}]

    def _apply(self, res: dict, actions: dict[str, dict]) -> None:
        for name, d in res["money"].items():
            self.get(name).money += d
        for name, d in res["mood"].items():
            r = self.get(name)
            r.mood = _clamp(r.mood + d, 0, 100)
        for name, d in res["rep"].items():
            r = self.get(name)
            r.rep = _clamp(r.rep + d, 0, 100)
        for rel in res["relations"]:
            key = (rel["a"], rel["b"])
            self.trust[key] = _clamp(self.trust[key] + rel["delta"], -100, 100)
        for name in res["exposed"]:
            r = self.get(name)
            r.exposed = True
            r.rep = _clamp(r.rep - 15, 0, 100)
        for name, s in res["status"].items():
            r = self.get(name)
            r.status = s
            if s == "left":
                r.location = ""
        for name, s in res["salary"].items():
            self.get(name).salary = s
        if res["strike"]:
            self.strikes.append(self.day)
        for name in res["strikers"]:  # 罢工的人和老板互相看不顺眼
            if name != "钱有福":
                key = ("钱有福", name)
                self.trust[key] = _clamp(self.trust[key] - 8, -100, 100)

    # ------------------------------------------------------------ objective & ending

    def objective(self) -> dict:
        s, days, day = self.scenario_id, self.scenario["days"], self.day
        broke = [r.name for r in self.residents if r.present and r.money < 0]
        left = [r.name for r in self.residents if not r.present]
        approval = self.approval()
        status, reason = "ongoing", ""
        if s == "calm":
            progress = f"已平稳度过 {day - len(self.strikes)} / {days} 天"
            if self.strikes:
                status, reason = "lost", f"第 {self.strikes[0]} 天发生了罢工。"
            elif day >= days:
                status, reason = "won", f"{days} 天里一次罢工都没有。"
        elif s == "nobody":
            progress = f"第 {day} / {days} 天，破产 {len(broke)} 人，离开 {len(left)} 人"
            if broke or left:
                status, reason = "lost", "、".join(broke + left) + ("破产了。" if broke else "离开了小镇。")
            elif day >= days:
                status, reason = "won", "所有人都撑到了最后。"
        elif s == "mayor":
            progress = f"第 {day} / {days} 天，镇长支持率 {approval}%"
            if self.mayor.status in ("resigned", "left"):
                status, reason = "lost", f"第 {day} 天镇长下台了。"
            elif day >= days:
                status, reason = ("won", f"镇长挺过来了，支持率 {approval}%。") if approval >= 50 else (
                    "lost", f"镇长虽然还在任，但支持率只有 {approval}%。")
        else:
            progress = f"第 {day} / {days} 天"
            if day >= days:
                status, reason = "done", "实验结束。"
        return {"status": status, "reason": reason, "progress": progress, "goal": self.scenario["goal"],
                "title": self.scenario["title"], "approval": approval, "strikes": list(self.strikes)}

    def _finish(self, obj: dict) -> None:
        result = {"won": "任务成功", "lost": "任务失败", "done": "实验结束"}[obj["status"]] + "：" + obj["reason"]
        rule_hist = "\n".join(
            f"第{e['day'] + 1}天前：{({'add': '新增', 'replace': '改写', 'repeal': '废除'})[e['op']]}「{e['text']}」"
            for e in self.log.events if e["type"] == "rule" and e["op"] != "none"
        ) or "（玩家一条规则都没改）"
        timeline = "\n".join(
            f"第{d['day']}天：" + "；".join(f"{e['title']}（{e['text']}）" for e in d["events"][:3])
            + (f"；连锁反应：{d['butterfly']}" if d["butterfly"] else "")
            for d in self.days
        )
        final = "\n".join(f"{r.name}（{r.role}）：现金{r.money} 心情{r.mood} 状态{r.status}"
                          f"{' 秘密已曝光' if r.exposed else ''}" for r in self.residents)
        model = self.referee_model()
        self.emit("busy", stage="chronicle", model=model, text="编年史作者正在写战报……")
        data, err, _ = self.ask(
            model, prompts.chronicle_system(), prompts.chronicle_task(self.scenario, result, rule_hist, timeline, final),
            {"kind": "chronicle", "rules": rule_hist, "day": self.day, "result": obj["status"]}, "编年史",
        )
        data = data or {}
        mvp = self.get(data.get("mvp"))
        share = str(data.get("share_line") or "").strip() or self._fallback_share(obj)
        self.emit(
            "end", result=obj["status"], reason=obj["reason"], title=self.scenario["title"],
            share_line=share[:80],
            story=str(data.get("story") or "").strip()[:400],
            mvp=mvp.name if mvp else "",
            lesson=str(data.get("lesson") or "").strip()[:80],
            model=model, **self.snapshot(),
        )
        self.log.finish()

    def _fallback_share(self, obj: dict) -> str:
        changed = [r for r in self.rules if r["by"] == "player"]
        rule = changed[0]["text"].rstrip("。") if changed else "什么规则都不改"
        big = next((d for d in self.days if d["strike"] or any(e["kind"] == "scandal" for e in d["events"])), None)
        if big:
            return f"我让 AI 小镇“{rule}”，第 {big['day']} 天{big['events'][0]['title']}。"
        return f"我让 AI 小镇“{rule}”，{self.day} 天后：{obj['reason']}"

    def summary(self) -> dict:
        return {
            "scenario": self.scenario_id, "result": self.result, "days": self.day, "models": self.models,
            "rules": [r["text"] for r in self.rules if r["by"] == "player"],
        }


def _trace(label: str, model: str, started: float, result: str) -> None:
    elapsed = time.monotonic() - started
    print(f"[{time.strftime('%H:%M:%S')}] {label} {model} {elapsed:.1f}s {result}", file=sys.stderr, flush=True)
