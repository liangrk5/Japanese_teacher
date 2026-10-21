"""学习进度：SQLite 保存单词卡片（间隔复习）和假名熟练度。"""

from __future__ import annotations

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
    UNIQUE(word, reading)
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
    chats INTEGER NOT NULL DEFAULT 0
);
"""

GRADES = {"again": 0, "hard": 1, "good": 2, "easy": 3}


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
        self._seed()

    def _seed(self) -> None:
        text = resources.files("nihongo.data").joinpath("vocab_n5.tsv").read_text("utf-8")
        rows = []
        for line in text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            word, reading, meaning, topic = line.split("\t")
            rows.append((word, reading, meaning, topic))
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO cards (word, reading, meaning, topic) VALUES (?,?,?,?)",
                rows,
            )

    def _bump(self, column: str, n: int = 1) -> None:
        day = datetime.now().date().isoformat()
        self._db.execute(
            f"INSERT INTO activity (day, {column}) VALUES (?, ?) "
            f"ON CONFLICT(day) DO UPDATE SET {column} = {column} + excluded.{column}",
            (day, n),
        )

    # ---- 单词 ----

    def due_cards(self, limit: int = 20, new_per_day: int = 10) -> list[dict]:
        ts = _iso(now())
        with self._lock:
            due = self._db.execute(
                "SELECT * FROM cards WHERE introduced = 1 AND due <= ? ORDER BY due LIMIT ?",
                (ts, limit),
            ).fetchall()
            remaining = max(0, limit - len(due))
            new_quota = max(0, new_per_day - self._new_today())
            fresh = self._db.execute(
                "SELECT * FROM cards WHERE introduced = 0 ORDER BY id LIMIT ?",
                (min(remaining, new_quota),),
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
            cur = self._db.execute(
                "INSERT INTO cards (word, reading, meaning, topic, example, source) "
                "VALUES (?,?,?,?,?, 'user') ON CONFLICT(word, reading) DO UPDATE SET "
                "meaning=excluded.meaning RETURNING id",
                (word, reading, meaning, "我的单词", example),
            )
            card_id = cur.fetchone()[0]
        return {"id": card_id}

    def set_example(self, card_id: int, example: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE cards SET example=? WHERE id=?", (example, card_id))

    def list_cards(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM cards ORDER BY id").fetchall()
        return [dict(r) for r in rows]

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
