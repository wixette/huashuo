"""Whole pipeline with the fake engine: TXT/EPUB -> work directory -> units -> M4B."""

import json

import pytest

from helpers import make_epub
from huashuo.cli import main
from huashuo.huaben import read_script, write_script
from huashuo.m4b import probe
from huashuo.workdir import Workdir


ENGINE_COMMANDS = ("make", "synth", "package", "redo")   # the ones that synthesize or plan units


def run(*args):
    """The CLI with the fake engine, so no model is loaded."""
    argv = [str(a) for a in args]
    return main(argv + ["--engine", "fake"] if argv[0] in ENGINE_COMMANDS else argv)


@pytest.mark.ffmpeg
def test_txt_to_m4b_resume_and_edit(sample_txt, capsys):
    assert run("make", sample_txt, "--no-asr") == 0
    out = sample_txt.with_suffix(".m4b")
    info = probe(out)
    chapters = [c["tags"]["title"] for c in info["chapters"]]
    # The opening section held only the title and the author line, which the opening
    # announcement now reads, so the first chapter is 第一回 (POST-6).
    assert chapters == ["第一回 甄士隐梦幻识通灵", "第二回 贾夫人仙逝扬州城"]
    tags = {k.lower(): v for k, v in info["format"]["tags"].items()}
    assert (tags["title"], tags["artist"], tags["genre"]) == ("红楼梦", "曹雪芹", "Audiobook")
    assert tags["composer"].startswith("话说 Huashuo") and tags["major_brand"].strip() == "M4B"
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert audio["codec_name"] == "aac" and audio["channels"] == 1 and audio["sample_rate"] == "24000"
    assert any(s["codec_type"] == "video" for s in info["streams"])      # generated cover
    ends = [float(c["end_time"]) for c in info["chapters"]]
    assert abs(ends[-1] - float(info["format"]["duration"])) < 0.5

    # Running again reuses every unit.
    capsys.readouterr()
    assert run("synth", sample_txt, "--no-asr") == 0
    assert "synthesized 0," in capsys.readouterr().out

    # Editing one block's reading re-synthesizes only the unit that contains it.
    wd = Workdir.for_input(sample_txt)
    script = read_script(wd.script)
    target = next(b for b in script.blocks if b["text"].startswith("诗云"))
    target["say"] = "诗曰：一局输赢料不真，香销茶尽尚逡巡。"
    write_script(wd.script, script)
    assert run("synth", sample_txt, "--no-asr") == 0
    assert "synthesized 1," in capsys.readouterr().out

    # Re-importing keeps the edit (three-way merge).
    assert run("import", sample_txt) == 0
    kept = next(b for b in read_script(wd.script).blocks if b["id"] == target["id"])
    assert kept["say"].startswith("诗曰")


@pytest.mark.ffmpeg
def test_epub_sample_and_chapter_selection(tmp_path, capsys):
    book = make_epub(tmp_path / "测试之书.epub", cover=None)
    assert run("make", book, "--no-asr", "--chapters", "2") == 0
    assert not book.with_suffix(".m4b").exists()             # an audition never overwrites the book
    info = probe(book.with_name("测试之书.chapters-2.m4b"))
    assert [c["tags"]["title"] for c in info["chapters"]] == ["第二章 落雨"]
    assert run("synth", book, "--no-asr", "--sample", "5") == 0
    assert run("package", book, "--sample", "5") == 0
    assert book.with_name("测试之书.sample.m4b").is_file()


def test_dry_run_lists_chapters_and_skips(tmp_path, capsys):
    book = make_epub(tmp_path / "b.epub")
    assert run("make", book, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "第一章 起风" in out and "[copyright]" in out and "plan:" in out
    assert not book.with_suffix(".m4b").exists()


def test_failed_units_are_kept_and_reported(sample_txt, capsys):
    from huashuo.engines.fake import FakeEngine
    from huashuo.pipeline import import_book, load_project, make_plan
    from huashuo.synth import synthesize

    wd = Workdir.for_input(sample_txt)
    import_book(sample_txt, wd)
    plan = make_plan(load_project(wd))
    engine = FakeEngine(fail_on="列位看官")
    stats = synthesize(plan.units, engine, wd, "zh", show_progress=False)
    assert len(stats.warnings) == 1 and stats.warnings[0]["problem"] == "empty audio"
    assert stats.retried == 2
    key = stats.warnings[0]["key"]
    sidecar = json.loads((wd.units / f"{key}.json").read_text())
    assert sidecar["problem"] == "empty audio" and (wd.units / f"{key}.wav").is_file()


def test_check_command_reports_problems(sample_txt, capsys):
    assert run("import", sample_txt) == 0
    wd = Workdir.for_input(sample_txt)
    script = read_script(wd.script)
    next(b for b in script.blocks if b["type"] == "narration")["text"] = "被改掉的原文"
    write_script(wd.script, script)
    assert run("check", sample_txt) == 1
    assert "I4" in capsys.readouterr().out




@pytest.mark.ffmpeg
def test_redo_resynthesizes_the_unit_at_a_time(sample_txt, capsys):
    from huashuo.synth import seed_for
    assert run("make", sample_txt, "--no-asr") == 0
    wd = Workdir.for_input(sample_txt)
    before = {p.stem: json.loads(p.read_text()) for p in wd.units.glob("*.json")}
    duration = float(probe(sample_txt.with_suffix(".m4b"))["format"]["duration"])
    capsys.readouterr()
    assert run("redo", sample_txt, "--no-asr", "--at", f"{duration * 0.6:.1f}") == 0
    out = capsys.readouterr().out
    assert "redo #1" in out and "synthesized 1," in out and "wrote" in out
    rerolls = json.loads(wd.rerolls.read_text())
    (key, times), = rerolls.items()
    after = json.loads((wd.units / f"{key}.json").read_text())
    assert times == 1 and after["seed"] == seed_for(key, 0, 1) != before[key]["seed"]
    # Rebuilding from scratch keeps the redone version.
    (wd.units / f"{key}.wav").unlink(); (wd.units / f"{key}.json").unlink()
    assert run("synth", sample_txt, "--no-asr") == 0
    assert json.loads((wd.units / f"{key}.json").read_text())["seed"] == after["seed"]
    import pytest
    with pytest.raises(SystemExit, match="outside the book"):
        run("redo", sample_txt, "--no-asr", "--at", "99:00")
