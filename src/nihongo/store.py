"""学习进度：SQLite 保存单词卡片（间隔复习）、假名熟练度和 AI 生成的课程。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    word TEXT NOT NULL,
    reading TEXT NOT NULL,
    meaning TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    example TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'builtin',
    ease REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    due TEXT,
    introduced INTEGER NOT NULL DEFAULT 0,
    introduced_at TEXT,
    level TEXT NOT NULL DEFAULT '',
    meaning_en TEXT NOT NULL DEFAULT '',
    rank INTEGER NOT NULL DEFAULT 100000,
    UNIQUE(word, reading)
);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'ai',
    generated_at TEXT NOT NULL,
    completed_at TEXT,
    quiz_score INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kana_stats (
    kana TEXT PRIMARY KEY,
    correct INTEGER NOT NULL DEFAULT 0,
    wrong INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    day TEXT PRIMARY KEY,
    reviews INTEGER NOT NULL DEFAULT 0,
    kana INTEGER NOT NULL DEFAULT 0,
    chats INTEGER NOT NULL DEFAULT 0,
    lessons INTEGER NOT NULL DEFAULT 0
);
"""

GRADES = {"again": 0, "hard": 1, "good": 2, "easy": 3}
LEVELS = ("N5", "N4", "N3", "N2", "N1")
SEED_VERSION = "2"
# 早期版本的 cards 表缺少的列
_CARD_COLUMNS = {
    "introduced_at": "TEXT",
    "level": "TEXT NOT NULL DEFAULT ''",
    "meaning_en": "TEXT NOT NULL DEFAULT ''",
    "rank": "INTEGER NOT NULL DEFAULT 100000",
}


def _data_lines(name: str):
    text = resources.files("nihongo.data").joinpath(name).read_text("utf-8")
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            yield line.split("\t")


def now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def schedule(ease: float, interval: float, reps: int, grade: str) -> tuple[float, float, int]:
    """简化的 SM-2。返回 (新难度系数, 新间隔天数, 新连续次数)。

    「重来」在 10 分钟后再出现；新卡第一次「良好」隔 1 天，第二次 3 天，之后按系数增长。
    """
    g = GRADES[grade]
    if g == 0:
        return max(1.3, ease - 0.2), 10 / (24 * 60), 0
    if g == 1:
        ease = max(1.3, ease - 0.15)
        interval = max(1.0, interval * 1.2) if reps else 0.5
    elif g == 2:
        interval = 1.0 if reps == 0 else 3.0 if reps == 1 else interval * ease
    else:
        ease += 0.15
        interval = 3.0 if reps == 0 else 7.0 if reps == 1 else interval * ease * 1.3
    return ease, round(interval, 4), reps + 1


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
        self._migrate()
        self._seed()

    def _migrate(self) -> None:
        with self._lock, self._db:
            have = {r[1] for r in self._db.execute("PRAGMA table_info(cards)")}
            for col, decl in _CARD_COLUMNS.items():
                if col not in have:
                    self._db.execute(f"ALTER TABLE cards ADD COLUMN {col} {decl}")
            if "lessons" not in {r[1] for r in self._db.execute("PRAGMA table_info(activity)")}:
                self._db.execute(
                    "ALTER TABLE activity ADD COLUMN lessons INTEGER NOT NULL DEFAULT 0"
                )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS cards_new ON cards (introduced, level, rank, id)"
            )

    def _seed(self) -> None:
        """导入内置词库：JLPT 词表（英文释义）+ 手工整理的入门词（中文释义，优先学习）。"""
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key='seed'").fetchone()
        if row and row[0] == SEED_VERSION:
            return
        jlpt = [
            (word, reading, meaning_en, level, level, 1000 + i)
            for i, (level, word, reading, meaning_en) in enumerate(_data_lines("jlpt_vocab.tsv"))
        ]
        curated = [
            (word, reading, meaning, topic, i)
            for i, (word, reading, meaning, topic) in enumerate(_data_lines("vocab_n5.tsv"))
        ]
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO cards (word, reading, meaning, meaning_en, level, topic, rank) "
                "VALUES (?, ?, '', ?, ?, ?, ?) ON CONFLICT(word, reading) DO UPDATE SET "
                "meaning_en=excluded.meaning_en, level=excluded.level, "
                "rank=MIN(rank, excluded.rank)",
                jlpt,
            )
            self._db.executemany(
                "INSERT INTO cards (word, reading, meaning, topic, rank, level) "
                "VALUES (?, ?, ?, ?, ?, 'N5') ON CONFLICT(word, reading) DO UPDATE SET "
                "meaning=CASE WHEN meaning='' THEN excluded.meaning ELSE meaning END, "
                "topic=excluded.topic, rank=excluded.rank, level='N5'",
                curated,
            )
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('seed', ?)", (SEED_VERSION,)
            )

    def _bump(self, column: str, n: int = 1) -> None:
        day = datetime.now().date().isoformat()
        self._db.execute(
            f"INSERT INTO activity (day, {column}) VALUES (?, ?) "
            f"ON CONFLICT(day) DO UPDATE SET {column} = {column} + excluded.{column}",
            (day, n),
        )

    # ---- 单词 ----

    def due_cards(
        self, limit: int = 20, new_per_day: int = 10, levels: list[str] | None = None
    ) -> list[dict]:
        levels = [lv for lv in (levels or ["N5"]) if lv in LEVELS] or ["N5"]
        marks = ",".join("?" * len(levels))
        ts = _iso(now())
        with self._lock:
            due = self._db.execute(
                "SELECT * FROM cards WHERE introduced = 1 AND due <= ? ORDER BY due LIMIT ?",
                (ts, limit),
            ).fetchall()
            remaining = max(0, limit - len(due))
            new_quota = max(0, new_per_day - self._new_today())
            fresh = self._db.execute(
                "SELECT * FROM cards WHERE introduced = 0 "
                f"AND (source = 'user' OR level IN ({marks})) ORDER BY rank, id LIMIT ?",
                (*levels, min(remaining, new_quota)),
            ).fetchall()
        return [dict(r) | {"is_new": False} for r in due] + [
            dict(r) | {"is_new": True} for r in fresh
        ]

    def _new_today(self) -> int:
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        row = self._db.execute(
            "SELECT COUNT(*) FROM cards WHERE introduced_at >= ?", (_iso(start),)
        ).fetchone()
        return row[0]

    def review(self, card_id: int, grade: str) -> dict:
        if grade not in GRADES:
            raise ValueError(f"未知评分：{grade}")
        with self._lock, self._db:
            row = self._db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
            if row is None:
                raise KeyError(card_id)
            ease, interval, reps = schedule(row["ease"], row["interval_days"], row["reps"], grade)
            lapses = row["lapses"] + (1 if grade == "again" and row["reps"] else 0)
            due = _iso(now() + timedelta(days=interval))
            self._db.execute(
                "UPDATE cards SET ease=?, interval_days=?, reps=?, lapses=?, due=?, introduced=1, "
                "introduced_at=COALESCE(introduced_at, ?) WHERE id=?",
                (ease, interval, reps, lapses, due, _iso(now()), card_id),
            )
            self._bump("reviews")
        return {"id": card_id, "interval_days": interval, "due": due}

    def add_card(self, word: str, reading: str, meaning: str, example: str = "") -> dict:
        with self._lock, self._db:
            card_id = self._upsert_user_card(word, reading, meaning, example)
        return {"id": card_id}

    def _upsert_user_card(self, word: str, reading: str, meaning: str, example: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO cards (word, reading, meaning, topic, example, source, rank) "
            "VALUES (?,?,?,?,?, 'user', -1) ON CONFLICT(word, reading) DO UPDATE SET "
            "meaning=excluded.meaning, rank=-1, "
            "source=CASE WHEN introduced=1 THEN source ELSE 'user' END RETURNING id",
            (word, reading, meaning, "我的单词", example),
        )
        return cur.fetchone()[0]

    def import_cards(self, rows: list[tuple[str, str, str]]) -> int:
        with self._lock, self._db:
            for word, reading, meaning in rows:
                self._upsert_user_card(word, reading, meaning)
        return len(rows)

    def untranslated(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._db.execute(
                f"SELECT id, word, reading, meaning_en FROM cards WHERE id IN ({marks}) "
                "AND meaning = '' AND meaning_en != ''",
                ids,
            ).fetchall()
        return [dict(r) for r in rows]

    def set_meanings(self, meanings: dict[int, str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE cards SET meaning=? WHERE id=? AND meaning=''",
                [(m.strip()[:120], i) for i, m in meanings.items() if m and m.strip()],
            )

    def set_example(self, card_id: int, example: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE cards SET example=? WHERE id=?", (example, card_id))

    def get_card(self, card_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return dict(row) if row else None

    def list_cards(
        self, level: str = "", q: str = "", status: str = "", limit: int = 100, offset: int = 0
    ) -> dict:
        where, args = [], []
        if level == "user":
            where.append("source = 'user'")
        elif level:
            where.append("level = ?")
            args.append(level)
        if q:
            where.append("(word LIKE ? OR reading LIKE ? OR meaning LIKE ? OR meaning_en LIKE ?)")
            args += [f"%{q}%"] * 4
        if status == "new":
            where.append("introduced = 0")
        elif status == "learning":
            where.append("introduced = 1 AND interval_days < 21")
        elif status == "mature":
            where.append("interval_days >= 21")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM cards {clause}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT * FROM cards {clause} ORDER BY introduced DESC, rank, id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}

    # ---- 课程 ----

    def get_lesson(self, lesson_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        if row is None:
            return None
        return dict(row) | {"content": json.loads(row["content"])}

    def save_lesson(self, lesson_id: str, content: dict, source: str = "ai") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO lessons (id, content, source, generated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET content=excluded.content, source=excluded.source, "
                "generated_at=excluded.generated_at",
                (lesson_id, json.dumps(content, ensure_ascii=False), source, _iso(now())),
            )

    def complete_lesson(self, lesson_id: str, score: int | None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE lessons SET completed_at=?, quiz_score=COALESCE(?, quiz_score) WHERE id=?",
                (_iso(now()), score, lesson_id),
            )
            self._bump("lessons")

    def lesson_states(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, source, completed_at, quiz_score FROM lessons"
            ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def lesson_contents(self) -> list[tuple[str, dict]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, content FROM lessons ORDER BY generated_at"
            ).fetchall()
        return [(r["id"], json.loads(r["content"])) for r in rows]

    def card_examples(self, limit: int = 40) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT example FROM cards WHERE example != '' AND introduced = 1 "
                "ORDER BY due DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["example"]))
            except ValueError:
                continue
        return out

    # ---- 假名 ----

    def record_kana(self, kana: str, correct: bool) -> None:
        col = "correct" if correct else "wrong"
        with self._lock, self._db:
            self._db.execute(
                f"INSERT INTO kana_stats (kana, {col}, last_seen) VALUES (?, 1, ?) "
                f"ON CONFLICT(kana) DO UPDATE SET {col} = {col} + 1, last_seen = excluded.last_seen",
                (kana, _iso(now())),
            )
            self._bump("kana")

    def kana_stats(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM kana_stats").fetchall()
        return {r["kana"]: {"correct": r["correct"], "wrong": r["wrong"]} for r in rows}

    def record_chat(self) -> None:
        with self._lock, self._db:
            self._bump("chats")

    # ---- 概览 ----

    def overview(self) -> dict:
        ts = _iso(now())
        with self._lock:
            c = self._db.execute(
                "SELECT COUNT(*) total, SUM(introduced) learned, "
                "SUM(CASE WHEN introduced=1 AND due<=? THEN 1 ELSE 0 END) due, "
                "SUM(CASE WHEN interval_days>=21 THEN 1 ELSE 0 END) mature FROM cards",
                (ts,),
            ).fetchone()
            per_level = {
                r["level"]: {"total": r["total"], "learned": r["learned"] or 0}
                for r in self._db.execute(
                    "SELECT level, COUNT(*) total, SUM(introduced) learned FROM cards "
                    "WHERE level != '' GROUP BY level"
                )
            }
            lessons_done = self._db.execute(
                "SELECT COUNT(*) FROM lessons WHERE completed_at IS NOT NULL"
            ).fetchone()[0]
            k = self._db.execute(
                "SELECT COUNT(*) FROM kana_stats WHERE correct >= 3 AND correct > wrong * 2"
            ).fetchone()[0]
            days = [
                dict(r)
                for r in self._db.execute(
                    "SELECT * FROM activity ORDER BY day DESC LIMIT 60"
                ).fetchall()
            ]
        return {
            "cards_total": c["total"] or 0,
            "cards_learned": c["learned"] or 0,
            "cards_due": c["due"] or 0,
            "cards_mature": c["mature"] or 0,
            "kana_mastered": k,
            "levels": per_level,
            "lessons_done": lessons_done,
            "streak": _streak([d["day"] for d in days]),
            "activity": days,
        }


def _streak(days: list[str]) -> int:
    have = set(days)
    d = datetime.now().date()
    if d.isoformat() not in have:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in have:
        n += 1
        d -= timedelta(days=1)
    return n
