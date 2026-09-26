"""The pronunciation dictionary, pron.txt (PRON-1, PRON-4)."""

import json

from huashuo import pron
from huashuo.engines.fake import FakeEngine
from huashuo.pipeline import load_project, make_plan
from huashuo.synth import synthesize, unit_keys
from huashuo.workdir import Workdir


def test_tone_digits_become_marks():
    assert pron.tone_marks("chan2 yu2") == "chán yú"
    assert pron.tone_marks("yu4 chi2 gong1") == "yù chí gōng"
    assert pron.tone_marks("lv4 gui4 liu2 xiong2 ma5") == "lǜ guì liú xióng ma"
    assert pron.tone_marks("chán yú") == "chán yú"            # already marked


def test_dictionary_file_longest_match_first_and_problems(tmp_path):
    path = tmp_path / "pron.txt"
    path.write_text("# comment\n单于 = chan2 yu2\n单于夜遁 = chán yú yè dùn\n重楼\t虫楼\n乐亭 → lào tíng\n坏行\n",
                    encoding="utf-8")
    d = pron.load(path)
    assert d.entries == {"单于": "chán yú", "单于夜遁": "chán yú yè dùn", "重楼": "虫楼", "乐亭": "lào tíng"}
    assert d.apply("单于夜遁，重楼之上，另一个单于。") == "chán yú yè dùn，虫楼之上，另一个chán yú。"
    assert len(d.problems) == 1 and "pron.txt:6" in d.problems[0]
    assert d.matches(["单于来了", "重楼"]) == {"单于": 1, "单于夜遁": 0, "重楼": 1, "乐亭": 0}
    assert pron.load(tmp_path / "missing.txt").apply("单于") == "单于"


def test_readings_change_only_the_units_with_the_word(sample_txt):
    from test_cli import run

    assert run("import", sample_txt) == 0
    wd = Workdir.for_input(sample_txt)
    assert wd.pron.read_text(encoding="utf-8").startswith("# Pronunciation dictionary")      # a template to fill in
    engine = FakeEngine()
    before = unit_keys(make_plan(load_project(wd)).units, engine, "zh")
    wd.pron.write_text("甄士隐 = zhēn shì yǐn\n", encoding="utf-8")
    plan = make_plan(load_project(wd))
    changed = [u for u in plan.units if u.reference is not None]
    assert changed and all("zhēn shì yǐn" in u.text and "甄士隐" in u.page_text for u in changed)
    after = unit_keys(plan.units, engine, "zh")
    assert sum(a != b for a, b in zip(before, after)) == len(changed)          # only those are redone


def test_the_asr_check_compares_with_the_words_on_the_page(sample_txt):
    from test_cli import run
    from test_synth import FakeAsr

    assert run("import", sample_txt) == 0
    wd = Workdir.for_input(sample_txt)
    wd.pron.write_text("甄士隐 = zhēn shì yǐn\n", encoding="utf-8")
    unit = next(u for u in make_plan(load_project(wd)).units if u.reference is not None)
    asr = FakeAsr([unit.page_text])                     # the recognizer writes characters
    stats = synthesize([unit], FakeEngine(), wd, "zh", asr=asr, show_progress=False)
    assert asr.calls == 1 and stats.retried == 0 and not stats.warnings


def test_import_reports_what_the_dictionary_matched(sample_txt, capsys):
    from test_cli import run

    assert run("import", sample_txt) == 0
    wd = Workdir.for_input(sample_txt)
    wd.pron.write_text("甄士隐 = zhēn shì yǐn\n贾宝玉 = jiǎ bǎo yù\n不成行\n", encoding="utf-8")
    capsys.readouterr()
    assert run("import", sample_txt) == 0
    out = capsys.readouterr().out
    assert "pron.txt: 2 entries; 甄士隐 ×1; not in the book: 贾宝玉" in out and "pron.txt:3" in out
