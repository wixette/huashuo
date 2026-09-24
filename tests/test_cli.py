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
                                                         "emotions": False, "opening": True, "closing": True,
                                                         "credit": False}
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


@pytest.mark.ffmpeg
def test_loudness_and_pauses_are_remembered_and_checked(sample_txt, capsys):
    from huashuo.m4b import probe

    assert run("make", sample_txt, "--no-asr") == 0
    before = float(probe(sample_txt.with_suffix(".m4b"))["format"]["duration"])
    assert run("package", sample_txt, "--loudness", "-16", "--pause", "paragraph=2.5") == 0
    wd = Workdir.for_input(sample_txt)
    stored = json.loads(wd.run_options.read_text())
    assert stored["loudness"] == -16 and stored["pauses"] == {"paragraph": 2.5} and stored["titles"] is True
    assert run("package", sample_txt, "--pause", "title=2") == 0          # merged with what is remembered
    assert json.loads(wd.run_options.read_text())["pauses"] == {"paragraph": 2.5, "title": 2.0}
    after = float(probe(sample_txt.with_suffix(".m4b"))["format"]["duration"])
    assert after > before + 1
    for bad in (["--loudness", "-3"], ["--pause", "nap=1"], ["--pause", "paragraph=long"], ["--pause", "title=60"]):
        with pytest.raises(SystemExit):
            run("package", sample_txt, *bad)


def test_gbk_web_novel_with_volumes_and_mixed_punctuation(tmp_path, capsys):
    from helpers import WEBNOVEL_TXT
    from huashuo.huaben import read_script
    from huashuo.pipeline import load_project, make_plan

    book = tmp_path / "剑来长安.txt"
    book.write_bytes(WEBNOVEL_TXT.encode("gbk"))
    assert run("import", book, "--no-llm") == 0
    out = capsys.readouterr().out
    assert "gb18030" in out and "《剑来长安》" in out
    wd = Workdir.for_input(book)
    project = load_project(wd)
    chapters = [c.title for c in make_plan(project).chapters]
    assert chapters == ["第一卷 风起青萍 · 第一章 雨夜来客", "第一卷 风起青萍 · 第二章 旧剑",
                            "第二卷 云涌 · 第三章 出城"]
    text = wd.text.read_text(encoding="utf-8")
    assert "雨下了一整夜，长安城的青石板路泛着冷光。" in text
    assert "掌柜抬起头：" in text and "从很远的地方来……你别问了。" in text
    assert "刻着1234个小字" in text and "出了城——再也没有回头" in text
    assert "www.example-novel.com" in text                  # URLs untouched
    dialogue = [b["text"] for b in read_script(wd.script).blocks if b["type"] == "dialogue"]
    assert '"店家，来一壶酒！"' in dialogue


def test_opening_and_closing_announcements(sample_txt):
    from huashuo.pipeline import load_project, make_plan

    assert run("import", sample_txt) == 0
    project = load_project(Workdir.for_input(sample_txt))
    p = make_plan(project)
    assert p.units[0].text == "《红楼梦》，作者曹雪芹。" and p.units[0].voice == project.cast["narrator"]["voice"]
    assert p.units[1].text == "第一回 甄士隐梦幻识通灵"            # then the first chapter's title
    assert p.chapters[0].first_unit == 0                               # the opening is in chapter 1
    assert all(p.units[c.first_unit].block_ids[0].startswith("c") for c in p.chapters[1:])
    assert p.units[-1].text == "全书完。" and p.units[-1].after == "end"
    assert p.units[-2].after == "chapter_end"
    credited = make_plan(project, credit=True)
    assert credited.units[-1].text == "全书完。本有声书由话说 Huashuo 生成。"
    plain = make_plan(project, opening=False, closing=False)
    assert plain.units[0].text.startswith("第一回") and plain.units[-1].text.endswith("就睡着了。")
    sample = make_plan(project, sample_chars=20)
    assert sample.units[0].text.startswith("《红楼梦》") and sample.units[-1].text != "全书完。"
    second = make_plan(project, chapters={2})
    assert second.units[0].text.startswith("第二回") and second.units[-1].text == "全书完。"


def test_clean_deletes_only_the_audio(sample_txt, capsys):
    from huashuo.engines.fake import FakeEngine
    from huashuo.pipeline import load_project, make_plan
    from huashuo.synth import synthesize

    assert run("import", sample_txt) == 0
    wd = Workdir.for_input(sample_txt)
    synthesize(make_plan(load_project(wd)).units, FakeEngine(), wd, "zh", show_progress=False)
    assert any(wd.units.iterdir())
    with pytest.raises(SystemExit, match="--yes"):              # not a terminal: must be explicit
        run("clean", sample_txt)
    assert run("clean", sample_txt, "--yes") == 0
    assert not wd.units.parent.exists() and wd.script.is_file() and wd.cast.is_file() and wd.pron.is_file()
    assert "deleted" in capsys.readouterr().out
    assert run("clean", sample_txt, "--yes") == 0 and "nothing to clean" in capsys.readouterr().out
