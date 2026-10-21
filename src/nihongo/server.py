"""本地 Web 服务：只监听 127.0.0.1。"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import tutor
from .config import Settings, data_home, load_settings, save_credentials
from .kana import KANA_TABLE
from .llm import LLMClient, LLMError
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"
MAX_AUDIO_BYTES = 8 * 1024 * 1024


def load_grammar() -> list[dict]:
    text = resources.files("nihongo.data").joinpath("grammar_n5.json").read_text("utf-8")
    return json.loads(text)


class ChatIn(BaseModel):
    history: list[dict[str, str]] = Field(default_factory=list)
    level: str = "zero"
    scenario: str = "free"


class ReviewIn(BaseModel):
    grade: str


class CardIn(BaseModel):
    word: str = Field(min_length=1, max_length=40)
    reading: str = Field(min_length=1, max_length=60)
    meaning: str = Field(min_length=1, max_length=120)


class KanaIn(BaseModel):
    kana: str = Field(min_length=1, max_length=4)
    correct: bool


class AskIn(BaseModel):
    question: str = ""
    level: str = "zero"


class SettingsIn(BaseModel):
    base_url: str = Field("", max_length=300)
    api_key: str = Field("", max_length=500)
    caller: str = Field("", max_length=200)
    models: dict[str, str] = Field(default_factory=dict)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    level: str = "zero"


def create_app(
    settings: Settings | None = None, store: Store | None = None, llm: LLMClient | None = None
) -> FastAPI:
    settings = settings or load_settings()
    store = store or Store(data_home() / "nihongo.db")
    llm = llm or LLMClient(settings)
    grammar = load_grammar()
    grammar_by_id = {g["id"]: g for g in grammar}

    app = FastAPI(title="日语学习助手", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # 拒绝其它网站对本地服务发起的写请求
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(LLMError)
    async def llm_error(_: Request, exc: LLMError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {"settings": settings.public(), "progress": store.overview()}

    @app.post("/api/settings")
    def update_settings(body: SettingsIn) -> dict[str, Any]:
        if body.base_url and not body.base_url.startswith(("http://", "https://")):
            raise HTTPException(400, "网关地址需要以 http:// 或 https:// 开头")
        models = {k: v for k, v in body.models.items() if k in settings.models}
        shadowed = save_credentials(
            {
                "NIHONGO_TEXT_BASE_URL": body.base_url,
                "NIHONGO_TEXT_API_KEY": body.api_key,
                "NIHONGO_TEXT_CALLER": body.caller,
            },
            models,
        )
        fresh = load_settings()
        settings.base_url, settings.api_key, settings.caller = (
            fresh.base_url,
            fresh.api_key,
            fresh.caller,
        )
        settings.models.update(fresh.models)
        return {"settings": settings.public(), "shadowed_by_env": shadowed}

    @app.post("/api/doctor")
    def doctor() -> dict[str, Any]:
        return {"reply": tutor.ping(llm), "models": settings.models}

    # ---- 假名 ----
    @app.get("/api/kana")
    def kana() -> dict[str, Any]:
        return {"table": KANA_TABLE, "stats": store.kana_stats()}

    @app.post("/api/kana/answer")
    def kana_answer(body: KanaIn) -> dict[str, bool]:
        store.record_kana(body.kana, body.correct)
        return {"ok": True}

    # ---- 单词 ----
    @app.get("/api/cards")
    def cards() -> list[dict]:
        return store.list_cards()

    @app.get("/api/cards/due")
    def due(limit: int = 20, new_per_day: int = 10) -> list[dict]:
        return store.due_cards(limit=min(limit, 100), new_per_day=min(new_per_day, 50))

    @app.post("/api/cards")
    def add_card(body: CardIn) -> dict:
        return store.add_card(body.word.strip(), body.reading.strip(), body.meaning.strip())

    @app.post("/api/cards/{card_id}/review")
    def review(card_id: int, body: ReviewIn) -> dict:
        try:
            return store.review(card_id, body.grade)
        except KeyError:
            raise HTTPException(404, "单词不存在")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/cards/{card_id}/example")
    def card_example(card_id: int) -> dict:
        card = next((c for c in store.list_cards() if c["id"] == card_id), None)
        if card is None:
            raise HTTPException(404, "单词不存在")
        data = tutor.make_example(llm, card["word"], card["reading"], card["meaning"])
        store.set_example(card_id, json.dumps(data, ensure_ascii=False))
        return data

    # ---- 语法 ----
    @app.get("/api/grammar")
    def grammar_list() -> list[dict]:
        return grammar

    @app.post("/api/grammar/{point_id}/explain")
    def grammar_explain(point_id: str, body: AskIn) -> dict:
        point = grammar_by_id.get(point_id)
        if point is None:
            raise HTTPException(404, "语法点不存在")
        return tutor.explain_grammar(llm, point, body.question.strip()[:500], body.level)

    # ---- AI 老师 ----
    @app.get("/api/scenarios")
    def scenarios() -> dict[str, str]:
        return tutor.SCENARIOS

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict:
        data = tutor.tutor_reply(llm, body.history, body.level, body.scenario)
        store.record_chat()
        return data

    @app.post("/api/breakdown")
    def breakdown(body: TextIn) -> dict:
        return tutor.breakdown(llm, body.text, body.level)

    # ---- 发音 ----
    @app.post("/api/pronounce")
    async def pronounce(
        target: Annotated[str, Form(max_length=200)],
        audio: Annotated[UploadFile, File()],
        reading: Annotated[str, Form(max_length=300)] = "",
    ) -> dict:
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "录音太长，请控制在 30 秒以内")
        if len(data) < 1000:
            raise HTTPException(400, "录音为空")
        return tutor.assess_pronunciation(llm, target, reading, data)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
