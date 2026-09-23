"""Whole pipeline with the fake engine: TXT/EPUB -> work directory -> units -> M4B."""

import json

from conftest import make_epub, needs_ffmpeg
from huashuo.cli import main
from huashuo.huaben import read_script, write_script
from huashuo.m4b import probe
from huashuo.workdir import Workdir


def run(*args):
    return main([*map(str, args), "--engine", "fake"] if args[0] in ("make", "synth", "package")
                else list(map(str, args)))


@needs_ffmpeg
def test_txt_to_m4b_resume_and_edit(sample_txt, capsys):
    assert run("make", sample_txt, "--no-asr") == 0
    out = sample_txt.with_suffix(".m4b")
    info = probe(out)
    chapters = [c["tags"]["title"] for c in info["chapters"]]
    assert chapters == ["红楼梦", "第一回 甄士隐梦幻识通灵", "第二回 贾夫人仙逝扬州城"]
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


@needs_ffmpeg
def test_epub_sample_and_chapter_selection(tmp_path, capsys):
    book = make_epub(tmp_path / "测试之书.epub", cover=None)
    assert run("make", book, "--no-asr", "--chapters", "2") == 0
    info = probe(book.with_suffix(".m4b"))
    assert [c["tags"]["title"] for c in info["chapters"]] == ["第二章 落雨"]
    assert run("synth", book, "--no-asr", "--sample", "5") == 0
    assert run("package", book, "--no-asr", "--sample", "5") == 0
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
    sidecar = json.loads(next(wd.units.glob("*.json")).read_text())
    assert {"key", "seed", "seconds", "problem"} <= set(sidecar)


def test_check_command_reports_problems(sample_txt, capsys):
    assert run("import", sample_txt) == 0
    wd = Workdir.for_input(sample_txt)
    script = read_script(wd.script)
    script.blocks[2]["text"] = "被改掉的原文"
    write_script(wd.script, script)
    assert run("check", sample_txt) == 1
    assert "I4" in capsys.readouterr().out


def test_asr_comparison_handles_traditional_characters():
    from huashuo.asr import cer
    assert cer("我從鄉下跑到京城里，後來打折了腿了。", "我从乡下跑到京城里，后来打折了腿了", "zh") == 0.0
    assert cer("他坐著。1987年", "他坐着，一九八七年", "zh") == 0.0
    assert cer("孔乙己是站著喝酒而穿長衫的唯一的人。", "孔乙己是站着喝酒的人", "zh") > 0.3


def test_limiter_only_touches_spikes():
    import numpy as np
    from huashuo.audio import LIMITER_CEILING_DB, limit
    sr = 24000
    t = np.arange(sr) / sr
    speech = 0.3 * np.sin(2 * np.pi * 200 * t).astype(np.float32)
    spiky = speech.copy()
    spiky[12000:12010] = 1.2
    out = limit(spiky, sr)
    assert np.abs(out).max() <= 10 ** (LIMITER_CEILING_DB / 20) + 1e-6
    far = slice(0, 9000)
    assert np.allclose(out[far], spiky[far])                  # untouched away from the spike
    assert limit(speech, sr) is speech                         # nothing to do, no copy
