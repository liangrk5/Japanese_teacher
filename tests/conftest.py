import json

import httpx
import pytest

from nihongo.config import Settings
from nihongo.llm import LLMClient
from nihongo.store import Store


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NIHONGO_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("NIHONGO_DATA_HOME", str(tmp_path / "data"))
    for key in ("NIHONGO_TEXT_BASE_URL", "NIHONGO_TEXT_API_KEY", "NIHONGO_TEXT_CALLER"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


class FakeGateway:
    """记录请求并返回预设回复的模型网关。"""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.reply: dict | str = {"reply": "こんにちは"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    @property
    def last_body(self) -> dict:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def settings():
    return Settings(base_url="https://prism.example/v1", api_key="vk-test", caller="nihongo.tutor")


@pytest.fixture
def llm(settings, gateway):
    return LLMClient(settings, transport=httpx.MockTransport(gateway.handler))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")
