import pytest

from nihongo.config import load_settings, save_credentials
from nihongo.llm import LLMError, parse_json


def test_request_carries_key_caller_and_model(llm, gateway):
    llm.chat([{"role": "user", "content": "hi"}])
    req = gateway.requests[-1]
    assert str(req.url) == "https://prism.example/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer vk-test"
    assert req.headers["X-Caller-PSM"] == "nihongo.tutor"
    assert gateway.last_body["model"] == "gpt-6-astra"


def test_audio_uses_input_audio_and_audio_model(llm, gateway):
    gateway.reply = {"score": 80}
    assert llm.audio_json("评测", b"RIFF....")["score"] == 80
    body = gateway.last_body
    assert body["model"] == "doubao-seed-2-1-lite-260915"
    part = body["messages"][-1]["content"][0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "wav"


def test_unconfigured_raises():
    from nihongo.config import Settings
    from nihongo.llm import LLMClient

    with pytest.raises(LLMError):
        LLMClient(Settings()).chat([{"role": "user", "content": "x"}])


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '好的：\n```json\n{"a": 1}\n```', '前缀 {"a": 1} 后缀'],
)
def test_parse_json_tolerates_wrapping(text):
    assert parse_json(text) == {"a": 1}


def test_env_overrides_credentials_file(monkeypatch):
    save_credentials(
        {"NIHONGO_TEXT_BASE_URL": "https://file", "NIHONGO_TEXT_API_KEY": "k1"},
        {"chat": "my-model"},
    )
    s = load_settings()
    assert (s.base_url, s.api_key, s.models["chat"]) == ("https://file", "k1", "my-model")

    monkeypatch.setenv("NIHONGO_TEXT_API_KEY", "k-env")
    monkeypatch.setenv("NIHONGO_MODEL_AUDIO", "audio-env")
    s = load_settings()
    assert s.api_key == "k-env"
    assert s.models["audio"] == "audio-env"


def test_saving_blank_key_keeps_previous(isolated_home):
    save_credentials({"NIHONGO_TEXT_API_KEY": "k1"})
    save_credentials({"NIHONGO_TEXT_API_KEY": "", "NIHONGO_TEXT_CALLER": "psm"})
    s = load_settings()
    assert (s.api_key, s.caller) == ("k1", "psm")
    path = isolated_home / "config" / "credentials.env"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
