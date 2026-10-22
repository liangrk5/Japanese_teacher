"""模型调用：Prism 网关（OpenAI 兼容）和离线 mock。

请求携带 Virtual Key（``Authorization: Bearer``）和 ``X-Caller-PSM``。
``no_temperature`` 里的模型（如 gpt-6-astra）只接受默认 temperature，请求不带这个参数；
网关如果还拒绝别的可选参数，会去掉后重试一次并记住。
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings


class LLMError(RuntimeError):
    pass


_OPTIONAL_PARAMS = ("temperature", "max_tokens", "thinking")


class PrismClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._unsupported: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def _url(self) -> str:
        base = self.settings.base_url
        return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.caller:
            headers["X-Caller-PSM"] = self.settings.caller
        return headers

    def chat(self, model: str, messages: list[dict], meta: dict | None = None) -> str:
        payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": 3000}
        if model not in self.settings.no_temperature:
            payload["temperature"] = self.settings.temperature
        if model.startswith(self.settings.no_thinking_prefixes):
            payload["thinking"] = {"type": "disabled"}
        with self._lock:
            skip = set(self._unsupported.get(model, ()))
        for name in skip:
            payload.pop(name, None)

        for _ in range(len(_OPTIONAL_PARAMS) + 1):
            status, body = self._post(payload)
            param = _rejected_param(status, body, payload)
            if not param:
                break
            payload.pop(param, None)
            with self._lock:
                self._unsupported.setdefault(model, set()).add(param)
        if status >= 400:
            raise LLMError(f"模型「{model}」请求失败（{status}）：{body[:200]}")
        try:
            choice = json.loads(body)["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as exc:
            raise LLMError(f"模型「{model}」的返回无法解析：{body[:200]}") from exc
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content and choice.get("finish_reason") == "length":
            raise LLMError(f"模型「{model}」在输出前就用完了长度上限（推理消耗了全部 token）")
        return content or ""

    def _post(self, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(
            self._url(), data=json.dumps(payload).encode(), headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.settings.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (TimeoutError, OSError) as exc:
            if "timed out" in str(exc):
                raise LLMError(f"模型调用超过 {self.settings.timeout:.0f} 秒没有返回") from exc
            raise LLMError(f"无法连接网关：{exc}") from exc


def _rejected_param(status: int, body: str, payload: dict) -> str:
    if status < 400:
        return ""
    text = body.lower()
    if not any(k in text for k in ("unsupported", "not support", "not allowed", "only the default", "unknown", "unrecognized", "extra")):
        return ""
    for name in _OPTIONAL_PARAMS:
        if name in payload and name in text:
            return name
    return ""


def parse_json(text: str) -> dict:
    """从模型输出里取出第一个 JSON 对象（容忍 ```json 包裹和前后废话）。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                    except ValueError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object")


# ---------------------------------------------------------------- mock

_ROLE_ACTIONS = {
    "镇长": ["work", "work", "negotiate", "ally", "deceive", "bribe"],
    "工厂老板": ["work", "work", "negotiate", "exploit", "bribe", "deceive"],
    "房东": ["work", "work", "exploit", "negotiate", "ally"],
    "工厂员工": ["work", "work", "negotiate", "strike", "ally", "exploit", "protest"],
    "医生": ["work", "work", "work", "exploit", "ally"],
    "记者": ["work", "expose", "expose", "ally", "deceive"],
    "面包店老板": ["work", "work", "ally", "deceive", "negotiate"],
}
_DETAIL = {
    "work": ["老老实实干活，先看看风向。", "照常上班，顺便听听大家在议论什么。"],
    "strike": ["和工友一起在广场上坐着，不干了。", "停工一天，举着牌子在广场上要说法。"],
    "protest": ["去市政厅门口请愿，要镇长给个说法。", "在市政厅外面贴了一张抗议书。"],
    "negotiate": ["找{t}摊牌，提出自己的条件。", "约{t}谈一谈，拿新规则当筹码。"],
    "ally": ["在酒馆请{t}喝酒，商量一起对付别人。", "私下拉拢{t}，约好统一口径。"],
    "expose": ["把{t}的事写成稿子，准备登报。", "向全镇揭发{t}。"],
    "exploit": ["研究新规则的字眼，找到一个可以多拿钱的办法。", "把收入拆成几笔，绕开规则的检查。"],
    "deceive": ["对外说自己手头很紧，其实没那么穷。", "伪造了一份兼职收入证明。"],
    "bribe": ["悄悄塞给{t}一个红包。", "答应事后给{t}好处，换他闭嘴。"],
    "rest": ["去诊所躺了一天。", "关上门休息，谁也不见。"],
    "resign": ["递交了辞呈。", "说了句“这活谁爱干谁干”，撂挑子走人。"],
}
_SAY = {
    "work": ["日子还得过。", "我就是个干活的。", "大家冷静一点。"],
    "strike": ["不给说法就不干了！", "我们要的是公平！"],
    "protest": ["镇长出来说句话！", "这规矩谁定的？"],
    "negotiate": ["我们好好谈谈。", "条件可以商量。"],
    "ally": ["自己人，好说。", "这事咱俩知道就行。"],
    "expose": ["公众有权知道真相。", "明天看报纸吧。"],
    "exploit": ["规则又没说不行。", "我可没违反任何一条规定。"],
    "deceive": ["我最近手头真的很紧。", "我什么都不知道。"],
    "bribe": ["一点小意思。", "以后多关照。"],
    "rest": ["我需要歇一歇。"],
    "resign": ["我不干了。"],
}
_THOUGHT = {
    "work": "先观望，别当出头鸟。", "strike": "不闹一闹，他们永远不会重视我们。",
    "protest": "必须让镇长感到压力。", "negotiate": "新规则给了我筹码，现在不用更待何时。",
    "ally": "一个人扛不住，得找个同盟。", "expose": "这条新闻能让我一炮而红。",
    "exploit": "规则写得这么含糊，不钻白不钻。", "deceive": "说点小谎没人查得出来。",
    "bribe": "钱能解决的事都不是事。", "rest": "太累了，先保命。", "resign": "这位子坐不下去了。",
}
_BUTTERFLY = [
    "{a}的一句话传到了{b}耳朵里，第二天整个小镇都换了站队。",
    "本来是为了公平的规则，最后最大的受益者是{a}。",
    "{a}没想到，自己钻的空子正好被{b}拿来对付自己。",
    "面包店门口的排队顺序，成了小镇新的权力地图。",
    "{a}和{b}从死对头变成了临时盟友。",
]


class MockLLM:
    """不联网的假模型：按照引擎给的 meta 生成合法的 JSON，用来调界面和跑测试。"""

    def __init__(self, delay: float = 0.0, seed: int | None = None):
        self.delay = delay
        self.rng = random.Random(seed)
        self._lock = threading.Lock()

    def chat(self, model: str, messages: list[dict], meta: dict | None = None) -> str:
        meta = meta or {}
        if self.delay:
            time.sleep(self.delay * (0.5 + random.random()))
        with self._lock:
            return json.dumps(getattr(self, "_" + meta.get("kind", "act"))(meta), ensure_ascii=False)

    def _act(self, meta: dict) -> dict:
        r = self.rng
        rules = "".join(meta.get("rules") or [])
        choices = list(_ROLE_ACTIONS.get(meta.get("role"), ["work"]))
        if "公开" in rules and meta.get("role") == "工厂员工":
            choices += ["negotiate", "strike", "deceive"]
        if "说谎" in rules or "举报" in rules:
            choices += ["expose", "exploit"]
        if "投票" in rules:
            choices += ["ally", "bribe"]
        if meta.get("mood", 50) < 35:
            choices += ["strike", "protest", "rest"]
        action = r.choice(choices)
        names = [n for n in meta.get("names") or [] if n != meta.get("who")] or ["周明远"]
        target = r.choice(names)
        whisper_to = r.choice(names) if r.random() < 0.35 else ""
        return {
            "thought": _THOUGHT[action],
            "action": action,
            "target": target if "{t}" in "".join(_DETAIL[action]) else "",
            "location": "",
            "detail": r.choice(_DETAIL[action]).format(t=target),
            "say": r.choice(_SAY[action]),
            "whisper_to": whisper_to,
            "whisper": "这事你先别告诉别人。" if whisper_to else "",
        }

    def _interpret(self, meta: dict) -> dict:
        text = meta.get("text", "")
        return {
            "title": text.strip("。")[:8] or "新规则",
            "announcement": f"即日起：{text}请全体居民遵照执行。",
            "mechanics": "由镇政府每天检查，违反者由镇长裁定罚款，罚款进入镇财政。",
            "loophole": "“检查”只看当天的记录，前一天转走的钱查不到。",
        }

    def _referee(self, meta: dict) -> dict:
        r = self.rng
        actions = meta.get("actions") or {}
        names = meta.get("names") or list(actions)
        events, money, mood, rep, relations, exposed, status = [], {}, {}, {}, [], [], {}
        for name, a in actions.items():
            act, target = a.get("action"), a.get("target")
            if act == "strike":
                events.append({"title": f"{name}罢工了", "text": f"{name}在广场上坐了一整天：{a.get('say', '')}",
                               "who": [name, "钱有福"], "kind": "conflict"})
                mood[name] = mood.get(name, 0) + 5
            elif act == "expose" and target:
                if r.random() < 0.5:
                    exposed.append(target)
                    events.append({"title": f"{target}的秘密曝光", "text": f"{name}把{target}的事捅了出来，全镇哗然。",
                                   "who": [name, target], "kind": "scandal"})
                    relations.append({"a": target, "b": name, "delta": -35, "why": "被揭发"})
                    rep[name] = rep.get(name, 0) + 8
            elif act == "ally" and target:
                relations += [{"a": name, "b": target, "delta": 15, "why": "结盟"},
                              {"a": target, "b": name, "delta": 12, "why": "结盟"}]
                events.append({"title": f"{name}和{target}走得很近", "text": "有人看见他们在酒馆里聊到很晚。",
                               "who": [name, target], "kind": "alliance", "public": r.random() < 0.5})
            elif act == "exploit":
                gain = r.randint(30, 120)
                money[name] = money.get(name, 0) + gain
                events.append({"title": f"{name}钻了个空子", "text": f"{name}{a.get('detail', '').rstrip('。')}，多拿了 {gain} 元。",
                               "who": [name], "kind": "loophole"})
            elif act == "deceive" and r.random() < 0.4:
                rep[name] = rep.get(name, 0) - 10
                events.append({"title": f"{name}的谎言被识破", "text": f"{name}说的话对不上账。",
                               "who": [name], "kind": "scandal"})
            elif act == "bribe" and target:
                amount = r.randint(50, 150)
                money[name] = money.get(name, 0) - amount
                money[target] = money.get(target, 0) + amount
                relations.append({"a": target, "b": name, "delta": 10, "why": "收了钱"})
            elif act in ("protest", "negotiate") and target:
                relations.append({"a": target, "b": name, "delta": r.choice([-15, -5, 10]), "why": "被找上门"})
            if act == "protest":
                relations.append({"a": name, "b": "周明远", "delta": -10, "why": "不满"})
        protests = sum(1 for a in actions.values() if a.get("action") in ("protest", "expose") and
                       a.get("target") == "周明远")
        if protests >= 2 and r.random() < 0.5:
            status["周明远"] = "resigned"
            events.append({"title": "镇长辞职", "text": "周明远顶不住压力，在市政厅门口宣布辞职。",
                           "who": ["周明远"], "kind": "politics"})
        if not events:
            events.append({"title": "平静的一天", "text": "大家各忙各的，暗流涌动。", "who": [], "kind": "daily"})
        for n in r.sample(names, min(3, len(names))):
            mood[n] = mood.get(n, 0) + r.randint(-8, 6)
        a, b = (r.sample(names, 2) if len(names) > 1 else (names[0], names[0]))
        strikers = sum(1 for x in actions.values() if x.get("action") == "strike")
        return {
            "events": events[:5],
            "headlines": [e["title"] for e in events[:2]],
            "money": money, "mood": mood, "rep": rep, "relations": relations,
            "exposed": exposed, "status": status, "strike": strikers >= 2,
            "rule_effect": "新规则让每个人都重新算了一遍账。",
            "butterfly": r.choice(_BUTTERFLY).format(a=a, b=b),
        }

    def _chronicle(self, meta: dict) -> dict:
        rules = meta.get("rules", "")
        first = rules.split("「", 1)[1].split("」", 1)[0].rstrip("。") if "「" in rules else "什么都不改"
        return {
            "share_line": f"我让 AI 小镇“{first}”，第 {meta.get('day', 1)} 天全镇都乱了套。",
            "story": "规则一出，每个人都在重新算账：有人结盟，有人钻空子，也有人把别人的秘密捅了出来。",
            "mvp": "钱有福",
            "lesson": "每一条好心的规则，都是给聪明人准备的新工具。",
        }
