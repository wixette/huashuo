import json

import pytest

from helpers import make_epub
from huashuo.cli import main, parse_time
from huashuo.huaben import read_script, write_script
from huashuo.workdir import Workdir


def run(*args):
    argv = [str(a) for a in args]
    return main(argv + ["--engine", "fake"] if argv[0] in ("make", "synth", "package", "redo") else argv)


def test_parse_time():
    assert parse_time("1:28") == 88 and parse_time("1:02:03") == 3723 and parse_time("88.5") == 88.5


def test_missing_book(tmp_path, capsys):
    assert run("import", tmp_path / "nope.txt") == 2
    assert "not found" in capsys.readouterr().err


def test_bad_cast_json_is_a_readable_error(sample_txt, capsys):
    assert run("import", sample_txt) == 0
    Workdir.for_input(sample_txt).cast.write_text("{oops", encoding="utf-8")
    assert run("synth", sample_txt, "--no-asr") == 2
    assert "not valid JSON" in capsys.readouterr().err


@pytest.mark.ffmpeg
def test_import_numbers_chapters_as_chapters_selects_them(tmp_path, capsys):
    book = tmp_path / "书.txt"
    book.write_text("书\n第一卷 起\n第一章 甲\n正文甲。\n第二章 乙\n正文乙。\n", encoding="utf-8")
    assert run("import", book) == 0
    out = capsys.readouterr().out
    assert "  1  第一卷 起 · 第一章 甲" in out and "  2  第一卷 起 · 第二章 乙" in out
    assert run("make", book, "--no-asr", "--chapters", "2") == 0
    from huashuo.m4b import probe
    chapters = probe(book.with_name("书.chapters-2.m4b"))["chapters"]
    assert [c["tags"]["title"] for c in chapters] == ["第一卷 起 · 第二章 乙"]


def test_reimport_after_upstream_insert_keeps_edit_on_its_paragraph(tmp_path, capsys):
    book = tmp_path / "书.txt"
    book.write_text("第一章 起\n甲段。\n乙段。\n", encoding="utf-8")
    assert run("import", book) == 0
    wd = Workdir.for_input(book)
    script = read_script(wd.script)
    next(b for b in script.blocks if b["text"] == "乙段。")["say"] = "乙段（改读）。"
    write_script(wd.script, script)
    book.write_text("第一章 起\n新的一段。\n甲段。\n乙段。\n", encoding="utf-8")
    assert run("import", book) == 0
    says = {b["text"]: b.get("say") for b in read_script(wd.script).blocks}
    assert says["乙段。"] == "乙段（改读）。" and says["甲段。"] is None
    assert run("check", book) == 0


def test_user_cover_survives_reimport(tmp_path):
    book = make_epub(tmp_path / "b.epub", cover=b"\xff\xd8book-cover")
    mine = tmp_path / "mine.png"
    mine.write_bytes(b"\x89PNGmine")
    assert run("import", book, "--cover", mine) == 0
    assert run("import", book) == 0
    wd = Workdir.for_input(book)
    assert wd.find_cover().read_bytes() == b"\x89PNGmine"
    assert read_script(wd.script).header["cover"] == "cover.png"


@pytest.mark.ffmpeg
def test_package_and_redo_reuse_the_synthesis_options(sample_txt):
    assert run("import", sample_txt) == 0
    assert run("synth", sample_txt, "--no-asr", "--voice", "preset:vivian", "--no-titles",
               "--no-emotions") == 0
    wd = Workdir.for_input(sample_txt)
    assert json.loads(wd.run_options.read_text()) == {"voice": "preset:vivian", "model": None, "titles": False,
                                                         "emotions": False}
    assert run("package", sample_txt) == 0                 # finds the same units without repeating flags


def test_title_and_author_flags_are_remembered_and_hand_edits_still_win(sample_txt):
    from huashuo.huaben import read_script, write_script

    assert run("import", sample_txt, "--title", "石头记", "--author", "曹雪芹 著") == 0
    wd = Workdir.for_input(sample_txt)
    header = read_script(wd.script).header
    assert (header["title"], header["author"]) == ("石头记", "曹雪芹 著")
    assert run("import", sample_txt) == 0                              # no need to repeat the flags
    assert read_script(wd.script).header["title"] == "石头记"
    script = read_script(wd.script)
    script.header["author"] = "曹雪芹、高鹗"                             # edited by hand
    write_script(wd.script, script)
    assert run("import", sample_txt) == 0
    assert read_script(wd.script).header["author"] == "曹雪芹、高鹗"
    assert run("import", sample_txt, "--title", "红楼梦") == 0
    assert read_script(wd.script).header["title"] == "红楼梦"
