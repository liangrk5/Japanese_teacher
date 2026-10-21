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
    assert store.get_card(cid)["source"] == "user"
    assert store.list_cards(level="user")["total"] == 1
    store.record_kana("あ", True)
    store.record_kana("あ", False)
    assert store.kana_stats()["あ"] == {"correct": 1, "wrong": 1}


def test_jlpt_vocab_seeded_with_levels(store):
    ov = store.overview()
    assert ov["levels"]["N5"]["total"] > 700
    assert ov["levels"]["N1"]["total"] > 2000
    # 手工整理的中文入门词优先出现
    first = store.due_cards(limit=3, new_per_day=3)
    assert [c["word"] for c in first] == ["こんにちは", "おはよう", "こんばんは"]
    assert all(c["meaning"] for c in first)
    n3 = store.due_cards(limit=2, new_per_day=2, levels=["N3"])
    assert all(c["level"] == "N3" and c["meaning_en"] for c in n3)


def test_imported_words_come_first(store):
    store.import_cards([("林檎", "りんご", "苹果"), ("蜜柑", "みかん", "橘子")])
    assert [c["word"] for c in store.due_cards(limit=2, new_per_day=2)] == ["林檎", "蜜柑"]


def test_translation_cache(store):
    card = store.list_cards(level="N4", limit=1)["items"][0]
    assert store.untranslated([card["id"]])
    store.set_meanings({card["id"]: "中文"})
    assert not store.untranslated([card["id"]])
    assert store.get_card(card["id"])["meaning"] == "中文"


def test_reopen_keeps_progress(tmp_path):
    from nihongo.store import Store

    s = Store(tmp_path / "p.db")
    cid = s.due_cards(limit=1)[0]["id"]
    s.review(cid, "good")
    s2 = Store(tmp_path / "p.db")
    assert s2.get_card(cid)["introduced"] == 1


def test_lessons_roundtrip(store):
    store.save_lesson("wa-desu", {"title": "x", "examples": [{"ja": "a", "zh": "b"}]})
    store.complete_lesson("wa-desu", 75)
    assert store.lesson_states()["wa-desu"]["quiz_score"] == 75
    assert store.overview()["lessons_done"] == 1
