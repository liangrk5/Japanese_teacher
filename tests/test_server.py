from fastapi.testclient import TestClient

from nihongo.server import create_app


def client(settings, store, llm):
    return TestClient(create_app(settings, store, llm))


def test_index_and_status(settings, store, llm):
    c = client(settings, store, llm)
    assert "日语学习助手" in c.get("/").text
    status = c.get("/api/status").json()
    assert status["settings"]["configured"] is True
    assert "api_key" not in status["settings"]


def test_review_flow(settings, store, llm):
    c = client(settings, store, llm)
    card = c.get("/api/cards/due").json()[0]
    r = c.post(f"/api/cards/{card['id']}/review", json={"grade": "good"})
    assert r.status_code == 200
    assert c.post(f"/api/cards/{card['id']}/review", json={"grade": "bad"}).status_code == 400


def test_chat_returns_structured_reply(settings, store, llm, gateway):
    gateway.reply = {"reply": "私[わたし]はAIです。", "translation": "我是AI。", "words": []}
    c = client(settings, store, llm)
    r = c.post("/api/chat", json={"history": [{"role": "user", "content": "你好"}]})
    assert r.json()["translation"] == "我是AI。"
    system = gateway.last_body["messages"][0]["content"]
    assert "漢字[かな]" in system


def test_pronounce_sends_audio(settings, store, llm, gateway):
    gateway.reply = {"heard": "こんにちは", "score": 88, "summary": "很好", "issues": []}
    c = client(settings, store, llm)
    r = c.post(
        "/api/pronounce",
        data={"target": "こんにちは", "reading": "こんにちは"},
        files={"audio": ("a.wav", b"RIFF" + b"\0" * 2000, "audio/wav")},
    )
    assert r.json()["score"] == 88
    assert gateway.last_body["messages"][-1]["content"][0]["type"] == "input_audio"


def test_gateway_error_is_reported(settings, store):
    import httpx

    from nihongo.llm import LLMClient

    bad = LLMClient(
        settings, transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no"))
    )
    r = client(settings, store, bad).post("/api/breakdown", json={"text": "你好"})
    assert r.status_code == 502 and "401" in r.json()["detail"]


def test_cross_site_writes_are_rejected(settings, store, llm):
    c = client(settings, store, llm)
    r = c.post(
        "/api/cards",
        json={"word": "a", "reading": "a", "meaning": "a"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_settings_update_applies_live(store):
    from nihongo.config import Settings
    from nihongo.llm import LLMClient

    s = Settings()
    c = TestClient(create_app(s, store, LLMClient(s)))
    r = c.post(
        "/api/settings",
        json={
            "base_url": "https://gw",
            "api_key": "k",
            "caller": "p",
            "models": {"chat": "m1", "bogus": "x"},
        },
    )
    body = r.json()["settings"]
    assert body["configured"] and body["models"]["chat"] == "m1" and "bogus" not in body["models"]
    assert c.post("/api/settings", json={"base_url": "ftp://x"}).status_code == 400
