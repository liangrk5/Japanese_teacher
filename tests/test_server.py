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


def test_lessons_generate_once_and_cache(settings, store, llm, gateway):
    gateway.reply = {
        "title": "〜てください",
        "explanation": "x",
        "examples": [{"ja": "見[み]てください。", "zh": "请看。"}],
        "speaking": [{"ja": "待[ま]ってください。", "zh": "请等一下。"}],
        "quiz": [],
    }
    c = client(settings, store, llm)
    syllabus = c.get("/api/lessons").json()
    assert len(syllabus) > 80
    assert next(p for p in syllabus if p["id"] == "wa-desu")["ready"]  # 内置课程
    lesson_id = "te-mo-ii"
    assert c.get(f"/api/lessons/{lesson_id}").json()["lesson"] is None
    first = c.post(f"/api/lessons/{lesson_id}/generate", json={}).json()
    assert first["lesson"]["content"]["title"] == "〜てください"
    calls = len(gateway.requests)
    c.post(f"/api/lessons/{lesson_id}/generate", json={})
    assert len(gateway.requests) == calls  # 已缓存
    c.post(f"/api/lessons/{lesson_id}/generate?force=true", json={})
    assert len(gateway.requests) == calls + 1
    sentences = c.get("/api/sentences").json()
    assert any(s["ja"] == "待[ま]ってください。" for s in sentences)


def test_translate_only_untranslated(settings, store, llm, gateway):
    c = client(settings, store, llm)
    card = c.get("/api/cards?level=N4&limit=1").json()["items"][0]
    gateway.reply = {str(card["id"]): "中文释义", "99999999": "x"}
    r = c.post("/api/cards/translate", json={"ids": [card["id"], 99999999]}).json()
    assert r == {str(card["id"]): "中文释义"}
    assert c.post("/api/cards/translate", json={"ids": [card["id"]]}).json() == {}


def test_import_word_list(settings, store, llm):
    c = client(settings, store, llm)
    text = "#separator:tab\nword\treading\tmeaning\n林檎\tりんご\t<b>苹果</b>\n本棚,书架\n"
    r = c.post("/api/cards/import", json={"text": text})
    assert r.json()["imported"] == 1  # 第二行是逗号分隔，按首行的制表符解析被跳过
    r = c.post("/api/cards/import", json={"text": "本棚,ほんだな,书架\n眼鏡,眼镜"})
    assert r.json()["imported"] == 2
    items = c.get("/api/cards?level=user").json()["items"]
    assert {i["word"]: i["meaning"] for i in items}["林檎"] == "苹果"
    assert c.post("/api/cards/import", json={"text": "only-one-column"}).status_code == 400


def test_doctor_reports_refused_model_per_role(settings, store):
    import json

    import httpx

    from nihongo.llm import LLMClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gpt-6-astra"}, {"id": "other-lite"}]})
        model = json.loads(request.content)["model"]
        if model == "doubao-seed-2-1-lite-260915":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "message": "model not allowed for this key",
                        "type": "auth_error",
                        "code": "model_forbidden",
                    }
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "こんにちは"}}]})

    llm = LLMClient(settings, transport=httpx.MockTransport(handler))
    d = client(settings, store, llm).post("/api/doctor").json()
    by_role = {r["role"]: r for r in d["results"]}
    assert by_role["chat"]["ok"]
    assert not by_role["fast"]["ok"] and not by_role["audio"]["ok"]
    assert "doubao-seed-2-1-lite-260915" in by_role["fast"]["error"]
    assert by_role["fast"]["code"] == "model_forbidden"
    assert d["available"] == ["gpt-6-astra", "other-lite"]

    r = client(settings, store, llm).post("/api/cards/1/example")
    assert (
        r.status_code == 502
        and "没有权限使用模型「doubao-seed-2-1-lite-260915」" in r.json()["detail"]
    )
