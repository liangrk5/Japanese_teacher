"""OpenAI 兼容网关（Prism）客户端。

请求携带 Virtual Key（``Authorization: Bearer``）和 ``X-Caller-PSM``，
``model`` 字段使用配置里的模型名。文字和音频都走 ``POST /v1/chat/completions``，
音频以 ``input_audio`` 内容块发送。
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from .config import Settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport

    def _url(self) -> str:
        base = self.settings.base_url
        # 允许填写 https://host 或 https://host/v1
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.caller:
            headers["X-Caller-PSM"] = self.settings.caller
        return headers

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "chat",
        temperature: float = 0.6,
        max_tokens: int = 1500,
    ) -> str:
        if not self.settings.configured:
            raise LLMError("模型尚未配置：请设置 NIHONGO_TEXT_BASE_URL 和 NIHONGO_TEXT_API_KEY")
        payload = {
            "model": self.settings.models[role],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=self.settings.timeout, transport=self._transport) as client:
                resp = client.post(self._url(), headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"无法连接模型网关：{exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"模型网关返回 {resp.status_code}：{resp.text[:300]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"无法解析模型返回：{resp.text[:300]}") from exc
        if isinstance(content, list):  # 某些适配器返回内容块列表
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""

    def chat_json(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        return parse_json(self.chat(messages, **kwargs))

    def audio_json(
        self, prompt: str, audio: bytes, *, fmt: str = "wav", system: str | None = None
    ) -> dict[str, Any]:
        content = [
            {
                "type": "input_audio",
                "input_audio": {"data": base64.b64encode(audio).decode(), "format": fmt},
            },
            {"type": "text", "text": prompt},
        ]
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return parse_json(self.chat(messages, role="audio", temperature=0.2))


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> dict[str, Any]:
    """从模型输出中取出 JSON 对象，容忍代码块和前后多余文字。"""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for cand in candidates:
        start, end = cand.find("{"), cand.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            value = json.loads(cand[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LLMError(f"模型没有返回有效 JSON：{text[:200]}")
