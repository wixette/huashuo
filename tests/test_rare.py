"""Characters the TTS does not know, read through a homophone (rare.py, PRON-5)."""

import json

import pytest

from huashuo import rare
from huashuo.pipeline import load_project, make_plan
from huashuo.workdir import Workdir

STORY = """笹垣的故事

第一章
敝姓笹垣，从大阪来。筱冢点点头。
“笹垣先生，请坐。”
"""


def test_the_tokenizer_decides_which_characters_are_unknown():
    assert rare.unknown("笹") and rare.unknown("堀") and rare.unknown("𠀀")
    for known in "筱冢菅阪嗯薰的":                              # rare but read right: left alone
        assert not rare.unknown(known)
    assert rare.homophone("笹") == ("替", "tì")
    assert rare.homophone("峠") == ("qiǎ", "qiǎ")              # no common homophone: pinyin
    assert rare.replacements(["笹垣，笹垣润三。", "筱冢"]) == {"笹": ("替", "tì", 2)}


def test_every_homophone_is_a_known_character_of_that_one_reading():
    from pypinyin import Style, pinyin

    table = json.loads(rare.TABLE.read_text(encoding="utf-8"))
    assert len(table["homophones"]) > 800
    for sound, ch in table["homophones"].items():
        assert not rare.unknown(ch) and pinyin(ch, style=Style.TONE, heteronym=True)[0] == [sound]


def _import(tmp_path, text=STORY):
    from test_cli import run

    book = tmp_path / "笹垣.txt"
    book.write_text(text, encoding="utf-8")
    assert run("import", book, "--single-voice") == 0
    return Workdir.for_input(book)


def test_units_read_the_homophone_and_the_check_hears_the_page(tmp_path):
    wd = _import(tmp_path)
    units = [u for u in make_plan(load_project(wd)).units if "笹" in u.page_text]
    assert len(units) == 3                                       # the title, the narration, the line
    assert all("笹" not in u.text and "替垣" in u.text for u in units)
    assert all("筱冢" in u.text for u in units if "筱冢" in u.page_text)


def test_pron_txt_wins_over_the_homophone(tmp_path):
    wd = _import(tmp_path)
    wd.pron.write_text("笹垣先生 = tì yuán xiān sheng\n笹 = 笹\n", encoding="utf-8")
    texts = [u.text for u in make_plan(load_project(wd)).units]
    assert any("tì yuán xiān sheng" in t for t in texts)        # the longer entry
    assert any("敝姓笹垣" in t for t in texts)                   # 笹 = 笹 keeps the character


def test_import_lists_the_replaced_characters(tmp_path, capsys):
    _import(tmp_path)
    out = capsys.readouterr().out
    assert "characters the TTS does not know, read as: 笹 → 替 (tì) ×3" in out


@pytest.mark.model
def test_the_table_matches_the_tokenizer():
    """Regenerate with `python -m huashuo.rare TOKENIZER_DIR` when the model changes."""
    from pathlib import Path

    from huggingface_hub import try_to_load_from_cache
    from transformers import AutoTokenizer

    table = json.loads(rare.TABLE.read_text(encoding="utf-8"))
    vocab = try_to_load_from_cache(table["model"], "vocab.json")
    if not isinstance(vocab, str):
        pytest.skip(f"{table['model']} is not downloaded")
    built = rare.build(AutoTokenizer.from_pretrained(Path(vocab).parent))
    assert built["unknown"] == table["unknown"] and built["homophones"] == table["homophones"]
