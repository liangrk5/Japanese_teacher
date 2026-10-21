"""本地 Web 服务：只监听 127.0.0.1。"""

from __future__ import annotations

import csv
import json
import re
import threading
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


BASIC_SENTENCES = [
    {"ja": "はじめまして。", "zh": "初次见面。"},
    {"ja": "よろしくお願[ねが]いします。", "zh": "请多关照。"},
    {"ja": "ありがとうございます。", "zh": "谢谢您。"},
    {"ja": "すみません、もう一度[いちど]お願[ねが]いします。", "zh": "不好意思，请再说一遍。"},
]


def _load_json(name: str):
    return json.loads(resources.files("nihongo.data").joinpath(name).read_text("utf-8"))


def parse_word_list(text: str) -> list[tuple[str, str, str]]:
    """解析 CSV / TSV / Anki 导出的纯文本：单词, 读音, 意思（两列时为 单词, 意思）。"""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []
    delim = "\t" if "\t" in lines[0] else ","
    rows = []
    for i, cols in enumerate(csv.reader(lines, delimiter=delim)):
        cols = [re.sub(r"<[^>]+>|&nbsp;", " ", c).strip() for c in cols]
        cols = [c for c in cols if c]
        if len(cols) < 2:
            continue
        if i == 0 and cols[0].lower() in ("word", "expression", "单词", "front"):
            continue
        if len(cols) == 2:
            word, meaning = cols
            reading = word
        else:
            word, reading, meaning = cols[:3]
        if len(word) <= 40 and len(reading) <= 60:
            rows.append((word, reading, meaning[:120]))
    return rows


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


class IdsIn(BaseModel):
    ids: list[int] = Field(max_length=60)


class ImportIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)


class LessonIn(BaseModel):
    level: str = "zero"


class CompleteIn(BaseModel):
    score: int | None = Field(None, ge=0, le=100)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    level: str = "zero"


def create_app(
    settings: Settings | None = None, store: Store | None = None, llm: LLMClient | None = None
) -> FastAPI:
    settings = settings or load_settings()
    store = store or Store(data_home() / "nihongo.db")
    llm = llm or LLMClient(settings)
    syllabus = _load_json("grammar_syllabus.json")
    syllabus_by_id = {g["id"]: g for g in syllabus}
    # 手写的入门课作为离线内容预置，之后仍可让 AI 重新生成
    states = store.lesson_states()
    for lesson in _load_json("grammar_lessons.json"):
        if lesson["id"] in syllabus_by_id and lesson["id"] not in states:
            store.save_lesson(lesson["id"], lesson, source="builtin")
    lesson_locks: dict[str, threading.Lock] = {}

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
    def cards(
        level: str = "", q: str = "", status: str = "", limit: int = 100, offset: int = 0
    ) -> dict:
        return store.list_cards(level, q.strip()[:40], status, min(limit, 500), max(0, offset))

    @app.get("/api/cards/due")
    def due(limit: int = 20, new_per_day: int = 10, levels: str = "N5") -> list[dict]:
        return store.due_cards(
            limit=min(limit, 100), new_per_day=min(new_per_day, 50), levels=levels.split(",")
        )

    @app.post("/api/cards")
    def add_card(body: CardIn) -> dict:
        return store.add_card(body.word.strip(), body.reading.strip(), body.meaning.strip())

    @app.post("/api/cards/import")
    def import_cards(body: ImportIn) -> dict:
        rows = parse_word_list(body.text)
        if not rows:
            raise HTTPException(400, "没有识别到单词，每行应为：单词, 读音, 意思")
        return {"imported": store.import_cards(rows[:20000])}

    @app.post("/api/cards/translate")
    def translate(body: IdsIn) -> dict[int, str]:
        items = store.untranslated(body.ids)
        if not items or not settings.configured:
            return {}
        meanings = tutor.translate_meanings(llm, items)
        known = {it["id"] for it in items}
        meanings = {i: m for i, m in meanings.items() if i in known}
        store.set_meanings(meanings)
        return meanings

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
        card = store.get_card(card_id)
        if card is None:
            raise HTTPException(404, "单词不存在")
        meaning = card["meaning"] or card["meaning_en"]
        data = tutor.make_example(llm, card["word"], card["reading"], meaning)
        store.set_example(card_id, json.dumps(data, ensure_ascii=False))
        return data

    # ---- 语法课程 ----
    @app.get("/api/lessons")
    def lessons() -> list[dict]:
        states = store.lesson_states()
        return [
            p
            | {
                "ready": p["id"] in states,
                "completed": bool(states.get(p["id"], {}).get("completed_at")),
                "score": states.get(p["id"], {}).get("quiz_score"),
            }
            for p in syllabus
        ]

    def _point(lesson_id: str) -> dict:
        point = syllabus_by_id.get(lesson_id)
        if point is None:
            raise HTTPException(404, "语法点不存在")
        return point

    @app.get("/api/lessons/{lesson_id}")
    def lesson(lesson_id: str) -> dict:
        point = _point(lesson_id)
        saved = store.get_lesson(lesson_id)
        return {"point": point, "lesson": saved}

    @app.post("/api/lessons/{lesson_id}/generate")
    def generate(lesson_id: str, body: LessonIn, force: bool = False) -> dict:
        point = _point(lesson_id)
        lock = lesson_locks.setdefault(lesson_id, threading.Lock())
        with lock:
            saved = store.get_lesson(lesson_id)
            if saved is None or force:
                idx = syllabus.index(point)
                earlier = [p["title"] for p in syllabus[:idx]]
                content = tutor.generate_lesson(llm, point, body.level, earlier)
                store.save_lesson(lesson_id, content)
                saved = store.get_lesson(lesson_id)
        return {"point": point, "lesson": saved}

    @app.post("/api/lessons/{lesson_id}/complete")
    def complete(lesson_id: str, body: CompleteIn) -> dict:
        _point(lesson_id)
        store.complete_lesson(lesson_id, body.score)
        return {"ok": True}

    @app.post("/api/lessons/{lesson_id}/ask")
    def ask(lesson_id: str, body: AskIn) -> dict:
        point = dict(_point(lesson_id))
        saved = store.get_lesson(lesson_id)
        point["explanation"] = (saved or {}).get("content", {}).get("explanation", point["summary"])
        return tutor.explain_grammar(llm, point, body.question.strip()[:500], body.level)

    @app.get("/api/sentences")
    def sentences() -> list[dict]:
        out, seen = [], set()
        groups = [("入门", BASIC_SENTENCES)]
        for lesson_id, content in store.lesson_contents():
            title = syllabus_by_id.get(lesson_id, {}).get("title", "")
            groups.append((title, content.get("speaking", []) + content.get("examples", [])))
        groups.append(("单词例句", store.card_examples()))
        for source, items in groups:
            for item in items:
                ja = str(item.get("ja", "")).strip()
                if ja and ja not in seen and len(ja) <= 120:
                    seen.add(ja)
                    out.append({"ja": ja, "zh": item.get("zh", ""), "source": source})
        return out

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
