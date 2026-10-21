"""假名表：清音、浊音/半浊音、拗音，平假名和片假名。"""

from __future__ import annotations

# (行名, [(平假名, 罗马音), ...])；None 表示五十音图里的空位
_SEION = [
    ("あ行", [("あ", "a"), ("い", "i"), ("う", "u"), ("え", "e"), ("お", "o")]),
    ("か行", [("か", "ka"), ("き", "ki"), ("く", "ku"), ("け", "ke"), ("こ", "ko")]),
    ("さ行", [("さ", "sa"), ("し", "shi"), ("す", "su"), ("せ", "se"), ("そ", "so")]),
    ("た行", [("た", "ta"), ("ち", "chi"), ("つ", "tsu"), ("て", "te"), ("と", "to")]),
    ("な行", [("な", "na"), ("に", "ni"), ("ぬ", "nu"), ("ね", "ne"), ("の", "no")]),
    ("は行", [("は", "ha"), ("ひ", "hi"), ("ふ", "fu"), ("へ", "he"), ("ほ", "ho")]),
    ("ま行", [("ま", "ma"), ("み", "mi"), ("む", "mu"), ("め", "me"), ("も", "mo")]),
    ("や行", [("や", "ya"), None, ("ゆ", "yu"), None, ("よ", "yo")]),
    ("ら行", [("ら", "ra"), ("り", "ri"), ("る", "ru"), ("れ", "re"), ("ろ", "ro")]),
    ("わ行", [("わ", "wa"), None, None, None, ("を", "wo")]),
    ("ん", [("ん", "n"), None, None, None, None]),
]

_DAKUON = [
    ("が行", [("が", "ga"), ("ぎ", "gi"), ("ぐ", "gu"), ("げ", "ge"), ("ご", "go")]),
    ("ざ行", [("ざ", "za"), ("じ", "ji"), ("ず", "zu"), ("ぜ", "ze"), ("ぞ", "zo")]),
    ("だ行", [("だ", "da"), ("ぢ", "ji"), ("づ", "zu"), ("で", "de"), ("ど", "do")]),
    ("ば行", [("ば", "ba"), ("び", "bi"), ("ぶ", "bu"), ("べ", "be"), ("ぼ", "bo")]),
    ("ぱ行", [("ぱ", "pa"), ("ぴ", "pi"), ("ぷ", "pu"), ("ぺ", "pe"), ("ぽ", "po")]),
]

_YOON_BASE = [
    ("き", "k"), ("ぎ", "g"), ("し", "sh"), ("じ", "j"), ("ち", "ch"), ("に", "n"),
    ("ひ", "h"), ("び", "b"), ("ぴ", "p"), ("み", "m"), ("り", "r"),
]  # fmt: skip


def to_katakana(text: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


def _yoon_rows():
    rows = []
    for base, cons in _YOON_BASE:
        cells = []
        for small, vowel in (("ゃ", "a"), ("ゅ", "u"), ("ょ", "o")):
            if cons in ("sh", "j", "ch"):
                romaji = cons + vowel
            else:
                romaji = cons + "y" + vowel
            cells.append((base + small, romaji))
        rows.append((f"{base}行拗音", cells))
    return rows


def _build(group: str, rows) -> list[dict]:
    out = []
    for row_name, cells in rows:
        out.append(
            {
                "group": group,
                "row": row_name,
                "cells": [
                    None
                    if cell is None
                    else {"hira": cell[0], "kata": to_katakana(cell[0]), "romaji": cell[1]}
                    for cell in cells
                ],
            }
        )
    return out


KANA_TABLE = _build("清音", _SEION) + _build("浊音・半浊音", _DAKUON) + _build("拗音", _yoon_rows())


def all_kana() -> list[dict]:
    """扁平化的假名列表，供练习出题。"""
    items = []
    for row in KANA_TABLE:
        for cell in row["cells"]:
            if cell:
                items.append({**cell, "group": row["group"], "row": row["row"]})
    return items
