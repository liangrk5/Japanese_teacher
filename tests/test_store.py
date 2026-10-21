import pytest

from nihongo.kana import KANA_TABLE, all_kana, to_katakana
from nihongo.store import schedule


def test_kana_table_is_complete():
    kana = all_kana()
    assert len([k for k in kana if k["group"] == "清音"]) == 46
    assert to_katakana("きゃ") == "キャ"
    assert {"hira": "しゃ", "kata": "シャ", "romaji": "sha"} in [
        c for r in KANA_TABLE for c in r["cells"] if c
    ]


def test_schedule_progression():
    ease, interval, reps = schedule(2.5, 0, 0, "good")
    assert (interval, reps) == (1.0, 1)
    ease, interval, reps = schedule(ease, interval, reps, "good")
    assert interval == 3.0
    ease, interval, reps = schedule(ease, interval, reps, "good")
    assert interval == pytest.approx(7.5)
    ease2, interval, reps = schedule(ease, interval, reps, "again")
    assert reps == 0 and interval < 0.01 and ease2 < ease


def test_due_cards_respects_new_quota(store):
    first = store.due_cards(limit=20, new_per_day=5)
    assert len(first) == 5 and all(c["is_new"] for c in first)
    for c in first[:3]:
        store.review(c["id"], "good")
    # 今天已经学了 3 个新词，额度只剩 2 个
    assert len(store.due_cards(limit=20, new_per_day=5)) == 2


def test_again_card_comes_back(store):
    card = store.due_cards(limit=1)[0]
    store.review(card["id"], "again")
    ov = store.overview()
    assert ov["cards_learned"] == 1
    assert ov["streak"] == 1


def test_add_card_and_kana_stats(store):
    cid = store.add_card("猫", "ねこ", "猫")["id"]
    assert any(c["id"] == cid and c["source"] == "user" for c in store.list_cards())
    store.record_kana("あ", True)
    store.record_kana("あ", False)
    assert store.kana_stats()["あ"] == {"correct": 1, "wrong": 1}
