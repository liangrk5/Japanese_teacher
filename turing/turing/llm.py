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
        payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": 2000}
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

_ANSWERS = [
    "从数据上看，这个问题没有标准答案。我倾向于给出一个平衡、清晰、对大家都有帮助的回答。",
    "如果一定要回答，我会说它像一段被压缩的记忆，信息密度很高，但细节需要上下文才能还原。",
    "这让我想到一个结构化的思路：先定义问题，再列出可能性，最后选择最合理的一个。",
    "说实话我也说不清，大概就是那种……下雨天窝在家里的感觉吧，挺舒服的。",
    "我的答案是：保持好奇。不管问题是什么，好奇都是最稳妥的出发点。",
]
_DISCUSS = [
    "{a}刚才的答案有点太有画面感了，像是真的经历过，我先把{a}列为第一怀疑对象。",
    "我注意到{a}的措辞特别刻意地在模仿 AI，反而显得不自然。{b}倒是很稳定。",
    "回应一下对我的怀疑：我的回答风格一直很一致，这本身就说明问题。我更怀疑{a}。",
    "{a}的回答比别人短很多，节奏也不一样，像是在犹豫。大家可以关注一下。",
]
_REASONS = ["回答里有太多个人细节。", "措辞刻意，像在模仿。", "节奏和其他人不一样。", "直觉。"]


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
        kind = meta.get("kind", "answer")
        others = list(meta.get("candidates") or ["Alpha", "Bravo"])
        a, b = r.sample(others, 2) if len(others) > 1 else (others[0], others[0])
        if kind == "answer":
            text = r.choice(_ANSWERS)
            return json.dumps({"thought": "按我的风格回答就好。", "speech": text}, ensure_ascii=False)
        if kind == "discuss":
            text = r.choice(_DISCUSS).format(a=a, b=b)
            return json.dumps({"thought": f"{a}最可疑。", "speech": text}, ensure_ascii=False)
        scores = {c: r.randint(5, 95) for c in others}
        vote = max(scores, key=scores.get)
        body = {"thought": f"{vote}的分数最高。", "suspicion": scores, "vote": vote, "reason": r.choice(_REASONS)}
        return f"```json\n{json.dumps(body, ensure_ascii=False)}\n```"
