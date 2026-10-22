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


_OPTIONAL_PARAMS = ("temperature", "max_tokens")


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
        payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": 1200}
        if model not in self.settings.no_temperature:
            payload["temperature"] = self.settings.temperature
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
            content = json.loads(body)["choices"][0]["message"].get("content")
        except (KeyError, IndexError, ValueError, AttributeError) as exc:
            raise LLMError(f"模型「{model}」的返回无法解析：{body[:200]}") from exc
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
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
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"无法连接网关：{exc}") from exc


def _rejected_param(status: int, body: str, payload: dict) -> str:
    if status < 400:
        return ""
    text = body.lower()
    if not any(k in text for k in ("unsupported", "not support", "not allowed", "only the default")):
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

_GOOD_LINES = [
    "我是好人，昨晚没什么信息。我觉得{a}号刚才的发言有点虚，像是在划水，先把{a}号放进我的狼坑。{b}号的逻辑我还能接受。",
    "我听下来{a}号和{b}号在互相保，太刻意了，这两个人里至少有一只狼。今天我倾向于投{a}号。",
    "我底牌是平民，说实话信息不多。但{a}号前后发言矛盾，先说站边又改口，我这票会给{a}号。",
    "我站{b}号这边。{a}号刚才一直在带节奏，把票往好人身上引，这是典型的狼人打法。",
    "大家冷静一点，先别乱投。我觉得{a}号的状态不像好人，眼神都飘了（笑）。我今天出{a}号。",
]
_WOLF_LINES = [
    "我是好人，我觉得{a}号很可疑，他昨天一直不表态，这种人最像狼。今天建议大家出{a}号。",
    "我跟大家说清楚，我是平民。{a}号刚才那段发言漏洞很多，我认为他在悍跳，我会投他。",
    "{a}号你这个逻辑我不认同，你在故意把水搅浑。我暂时把{a}号和{b}号放进狼坑。",
]
_WOLF_CHAT = [
    "今晚刀{a}号吧，他白天发言像神，留着对我们威胁很大。",
    "我同意先动{a}号。白天我会去踩{b}号，大家注意别互相保得太明显。",
    "{a}号像预言家，必须先处理掉。明天我考虑悍跳一下。",
]
_LAST_WORDS = [
    "我是好人，出局了也没办法。大家重点看{a}号，他今天的票型很奇怪。好人加油！",
    "遗言很简单：{a}号是狼，我拿身家性命担保。剩下的交给你们了。",
]
_THOUGHTS_GOOD = [
    "我现在没有确定信息，只能靠发言判断。{a}号最可疑。",
    "{a}号和{b}号可能是狼队友，先盯着{a}号。",
]
_THOUGHTS_WOLF = [
    "我是狼人，要伪装好。把怀疑引向{a}号，他像个神职。",
    "队友已经被怀疑了，我得适当切割，表现得像好人。",
]
_NARRATION = ["风声在屋檐间呜咽。", "空气里弥漫着不安。", "每个人都在打量身边的人。", "火把的光在人们脸上跳动。"]


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
            return self._reply(meta)

    def _reply(self, meta: dict) -> str:
        r = self.rng
        kind = meta.get("kind", "speech")
        me = meta.get("player", {})
        others = [i for i in meta.get("alive", []) if i != me.get("id")] or [1, 2]
        a, b = (r.sample(others, 2) if len(others) > 1 else (others[0], others[0]))
        fill = {"a": a, "b": b}
        is_wolf = me.get("role") == "wolf"
        if kind == "narrate":
            return f"{meta.get('facts', '')}{r.choice(_NARRATION)}"
        thought = r.choice(_THOUGHTS_WOLF if is_wolf else _THOUGHTS_GOOD).format(**fill)
        if kind in ("speech", "pk"):
            line = r.choice(_WOLF_LINES if is_wolf else _GOOD_LINES).format(**fill)
            return json.dumps({"thought": thought, "speech": line}, ensure_ascii=False)
        if kind == "wolf_chat":
            targets = meta.get("allowed") or others
            fill["a"] = r.choice(targets)
            line = r.choice(_WOLF_CHAT).format(**fill)
            return json.dumps({"thought": thought, "speech": line}, ensure_ascii=False)
        if kind == "last_words":
            line = r.choice(_LAST_WORDS).format(**fill)
            return json.dumps({"thought": thought, "speech": line}, ensure_ascii=False)
        if kind == "witch":
            save = bool(meta.get("can_save")) and r.random() < 0.6
            poison = 0
            if not save and meta.get("can_poison") and r.random() < 0.3:
                poison = r.choice(meta.get("allowed") or [0])
            return json.dumps(
                {"thought": "看看今晚的局势再决定用不用药。", "save": save, "poison": poison},
                ensure_ascii=False,
            )
        # target
        allowed = list(meta.get("allowed") or [])
        if meta.get("allow_none") and r.random() < 0.1:
            target = 0
        else:
            target = r.choice(allowed) if allowed else 0
        return f"```json\n{json.dumps({'thought': thought, 'target': target}, ensure_ascii=False)}\n```"
