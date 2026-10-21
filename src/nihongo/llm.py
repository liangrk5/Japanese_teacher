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

ROLE_LABELS = {"chat": "对话 / 讲解", "audio": "发音评测", "fast": "例句 / 翻译"}


class LLMError(RuntimeError):
    def __init__(self, message: str, *, model: str = "", code: str = ""):
        super().__init__(message)
        self.model = model
        self.code = code


def _error_code(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return ""
    return str(err.get("code") or err.get("type") or "") if isinstance(err, dict) else ""


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
        model = self.settings.models[role]
        payload = {
            "model": model,
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
            code = _error_code(resp)
            label = ROLE_LABELS.get(role, role)
            if code == "model_forbidden" or "model not allowed" in resp.text:
                raise LLMError(
                    f"这个 Key 没有权限使用模型「{model}」（用于{label}）。"
                    "请在「设置 › 模型连接」里换成这个 Key 能用的模型名。",
                    model=model,
                    code="model_forbidden",
                )
            raise LLMError(
                f"模型「{model}」（{label}）请求失败，网关返回 {resp.status_code}：{resp.text[:300]}",
                model=model,
                code=code,
            )
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"无法解析模型返回：{resp.text[:300]}") from exc
        if isinstance(content, list):  # 某些适配器返回内容块列表
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""

    def list_models(self) -> list[str]:
        """尝试读取网关的 GET /v1/models；网关不支持时返回空列表。"""
        if not self.settings.configured:
            return []
        url = self._url().removesuffix("/chat/completions") + "/models"
        try:
            with httpx.Client(timeout=15, transport=self._transport) as client:
                resp = client.get(url, headers=self._headers())
            data = resp.json().get("data", []) if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError, AttributeError):
            return []
        return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})

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
