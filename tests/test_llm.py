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


def test_retries_without_unsupported_temperature(settings):
    import json

    import httpx

    from nihongo.llm import LLMClient

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "temperature" in body:
            inner = {
                "type": "invalid_request_error",
                "code": "-4003",
                "message": "code: unsupported_value; message: Unsupported value: 'temperature' "
                "does not support 0.6 with this model. Only the default (1) value is supported.",
                "param": "temperature",
            }
            return httpx.Response(502, json={"status_code": 400, "error": inner})
        if "max_tokens" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Unsupported parameter: 'max_tokens' is not supported "
                        "with this model. Use 'max_completion_tokens' instead."
                    }
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = LLMClient(settings, transport=httpx.MockTransport(handler))
    assert llm.chat([{"role": "user", "content": "x"}]) == "ok"
    assert "temperature" not in seen[-1] and seen[-1]["max_completion_tokens"] > 0
    # 记住了这个模型不支持的参数，下次直接一次成功
    n = len(seen)
    assert llm.chat([{"role": "user", "content": "y"}]) == "ok"
    assert len(seen) == n + 1


def test_error_message_is_readable(settings):
    import httpx

    from nihongo.llm import LLMClient

    def handler(request):
        return httpx.Response(
            502, json={"status_code": 400, "error": {"message": "code: bad_thing; message: 出错了"}}
        )

    llm = LLMClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as exc:
        llm.chat([{"role": "user", "content": "x"}])
    assert str(exc.value) == "模型「gpt-6-astra」（对话 / 讲解）请求失败（502）：出错了"
