"""Golden tests on two stories by 半轻人 (CC BY-NC-ND 4.0): examples/fish (一条被洗澡水拍死的鱼,
three characters, first person, TXT and EPUB) and examples/ad (广告, eleven speakers of
every age, for the voice library).

Offline and free like every test: speaker attribution replays the LLM answers stored in
examples/<name>/llm-cache. When the prompts change those answers no longer match; the
tests are then skipped with a note to re-record them (examples/refresh_golden.py <name>,
a capped manual run of a few cents).
"""

import re
import shutil
from pathlib import Path

import pytest

from huashuo.chinese import is_traditional
from huashuo.ingest import read_book
from huashuo.pipeline import LLMOptions, import_book, load_project, make_plan
from huashuo.structure import build
from huashuo.workdir import Workdir

FISH = Path(__file__).resolve().parents[1] / "examples" / "fish"
TXT = FISH / "txt" / "一条被洗澡水拍死的鱼.txt"
EPUB = FISH / "epub" / "一条被洗澡水拍死的鱼.epub"
AD = FISH.parent / "ad"
AD_TXT = AD / "txt" / "广告.txt"
MIN_ACCURACY = 0.95                      # requirements §5.2


def _story(built) -> list[tuple[str, str]]:
    return [(b["type"], b["text"]) for b in built.script.blocks if b["type"] in ("narration", "dialogue")]


def test_txt_and_epub_give_the_same_story():
    txt, epub = build(read_book(TXT)), build(read_book(EPUB))
    assert _story(txt) == _story(epub)
    assert txt.script.header["title"] == epub.script.header["title"] == "一条被洗澡水拍死的鱼"
    assert txt.script.header["author"] == epub.script.header["author"] == "半轻人"
    assert not is_traditional(txt.text)


def test_structure_of_each_format():
    txt, epub = build(read_book(TXT)), build(read_book(EPUB))
    assert [b["text"] for b in txt.script.blocks if b["type"] == "heading"] == [str(n) for n in range(1, 10)]
    assert [b["reason"] for b in txt.script.blocks if b["type"] == "skip"] == ["author"]
    assert [b["text"] for b in epub.script.blocks if b["type"] == "chapter"] == [str(n) for n in range(1, 10)]
    assert sum(b["type"] == "dialogue" for b in txt.script.blocks) == 88       # quotes before attribution


def _gold(example: Path = FISH) -> list[tuple[str, str]]:
    rows = [line.split("\t") for line in (example / "speakers.tsv").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    return [(quote, speaker) for _, speaker, quote, *_ in rows]


def _attributed(tmp_path: Path, source: Path, example: Path = FISH, cast: Path | None = None, **options):
    book = tmp_path / source.name
    shutil.copy(source, book)
    wd = Workdir.for_input(book)
    shutil.copytree(example / "llm-cache", wd.state / "llm-cache")
    if cast is not None:                                  # a cast chosen by hand before the first import
        shutil.copy(cast, wd.cast)
    result = import_book(book, wd, llm=LLMOptions(enabled=False, model="gpt-6-sol"), **options)
    project = load_project(wd)
    genders_pending = any(c.get("gender") not in ("male", "female") and not c.get("gender_inferred")
                          for c in project.cast.get("characters", {}).values()) and example == AD
    if result.llm is None or result.llm.stopped or genders_pending:
        pytest.skip(f"the stored LLM answers no longer match the prompts; re-record them with "
                    f"examples/refresh_golden.py {example.name} (a few cents)")
    return wd, result


@pytest.mark.parametrize("source", [TXT, EPUB], ids=["txt", "epub"])
def test_speakers_match_the_authors_labels(tmp_path, source):
    wd, result = _attributed(tmp_path, source)
    got = [(b["text"], b.get("speaker")) for b in load_project(wd).script.blocks if b["type"] == "dialogue"]
    gold = _gold()
    assert [q for q, _ in got] == [q for q, _ in gold]
    right = sum(a == b for (_, a), (_, b) in zip(got, gold))
    assert right / len(gold) >= MIN_ACCURACY, f"{right}/{len(gold)} speakers match the labels"


def test_the_first_person_narrator_reads_the_narration(tmp_path):
    wd, result = _attributed(tmp_path, TXT)
    cast = load_project(wd).cast
    # 我 is never called 他: pass 1 leaves the gender unknown, the gender call infers male
    # (栖芒, a woman, is his lover), so the book gets the male narrator.
    assert cast["characters"]["我"]["gender"] == "male" and cast["characters"]["我"]["gender_inferred"]
    assert result.narrator == "library:zh/narrator_male" and "我 is male (inferred)" in result.narrator_reason
    voices = {name: c["voice"] for name, c in cast["characters"].items()}
    assert voices["我"] == cast["narrator"]["voice"]
    assert len(set(voices.values())) == len(voices)                           # 栖芒 and 李忱 each their own
    plan = make_plan(load_project(wd))
    assert plan.units[0].text == "《一条被洗澡水拍死的鱼》，作者半轻人。"


_SENTENCE_END = re.compile(r'[。！？…!?][”’」』》）】"]*$')


def _unit_problems(plan) -> list[str]:
    """What the TTS should never get: a unit over the limit, a paragraph cut mid-sentence,
    or cut into a scrap. The splitting is rule-based, so it holds with one voice as well."""
    from huashuo.units import DEFAULT_MAX_CHARS, SENTENCE

    body = [u for u in plan.units if u.kind == "body"]
    problems = [f"{len(u.text)} characters: {u.text[:20]}" for u in body if len(u.text) > DEFAULT_MAX_CHARS]
    for a, b in zip(body, body[1:]):
        if a.after != SENTENCE or (a.voice, a.instruct) != (b.voice, b.instruct):
            continue                                     # a turn between voices, or a new paragraph
        if not _SENTENCE_END.search(a.text) and re.search(r"[。！？]", a.text):
            problems.append(f"cut mid-sentence: …{a.text[-15:]}")
        if min(len(a.text), len(b.text)) < 20:
            problems.append(f"scrap: {a.text[-12:]!r} | {b.text[:12]!r}")
    return problems


@pytest.mark.parametrize("voices", ["multi", "single"])
def test_units_are_whole_sentences_of_a_sensible_length(tmp_path, voices):
    """This story had both faults before: a sentence cut where a quoted term began
    (「……接受基因干预的“优质人”」) and an 8-character scrap (「他们也没有答案。」)."""
    book = tmp_path / TXT.name
    shutil.copy(TXT, book)
    wd = Workdir.for_input(book)
    shutil.copytree(FISH / "llm-cache", wd.state / "llm-cache")
    result = import_book(book, wd, voices=voices, llm=LLMOptions(enabled=False, model="gpt-6-sol"))
    project = load_project(wd)
    plan = make_plan(project)
    assert _unit_problems(plan) == []
    voices_used = {u.voice for u in plan.units}
    if voices == "single":
        assert voices_used == {result.narrator} and not any(u.instruct for u in plan.units)
    else:
        assert len(voices_used) == 3                                # the narrator (and 我), 栖芒, 李忱



# ---- examples/ad: the voice library ----------------------------------------------------------------

_CHILD_VOICES = {"boy", "girl"}


def test_ad_speakers_match_the_labels(tmp_path):
    wd, _ = _attributed(tmp_path, AD_TXT, AD)
    got = [(b["text"], b.get("speaker")) for b in load_project(wd).script.blocks if b["type"] == "dialogue"]
    gold = _gold(AD)
    assert [q for q, _ in got] == [q for q, _ in gold]
    right = sum(a == b for (_, a), (_, b) in zip(got, gold))
    assert right / len(gold) >= MIN_ACCURACY, f"{right}/{len(gold)} speakers match the labels"


def test_ad_casts_eleven_speakers_across_the_library(tmp_path):
    """Eleven speakers of every age but teenagers: each gets a voice of their own gender
    and a sensible age; genders the text leaves open are inferred, and the two aliens, who
    never speak, are not cast."""
    from huashuo.library import get

    wd, result = _attributed(tmp_path, AD_TXT, AD)
    characters = load_project(wd).cast["characters"]
    assert len(characters) == 11 and not any("外星人" in name for name in characters)
    voices = {name: get(c["voice"]) for name, c in characters.items()}
    assert len({v.ref for v in voices.values()}) >= 10
    assert all(c["gender"] in ("male", "female") for c in characters.values())      # none left unknown
    assert any(c.get("gender_inferred") for c in characters.values())                # the text leaves some open
    for name, c in characters.items():
        assert voices[name].gender == c["gender"], name
        assert (voices[name].ref.split("/")[-1] in _CHILD_VOICES) == (c["age"] == "child"), name


@pytest.mark.parametrize("voices", ["multi", "single"])
def test_ad_units_are_whole_sentences(tmp_path, voices):
    wd, result = _attributed(tmp_path, AD_TXT, AD, voices=voices)
    plan = make_plan(load_project(wd))
    assert _unit_problems(plan) == []
    if voices == "single":
        assert {u.voice for u in plan.units} == {result.narrator}


def test_ad_the_demo_cast_is_kept(tmp_path):
    """examples/ad/cast.json is the demo's cast, one choice changed by ear: the slogan-shouting
    TV host (小胡子) in the jolly voice, not the dignified one the LLM suggested (design doc
    §8.7), and the chef, who speaks right after him, in the dignified one instead."""
    from huashuo.pipeline import check_project

    wd, result = _attributed(tmp_path, AD_TXT, AD, cast=AD / "cast.json")
    project = load_project(wd)
    voices = {name: c["voice"] for name, c in project.cast["characters"].items()}
    assert voices["小胡子"] == "library:zh/mid_man_jovial" and voices["厨师"] == "library:zh/mid_man_steady"
    assert check_project(project) == []
    slogan = next(u for u in make_plan(project).units if "远道而来是朋友" in u.text)
    assert slogan.voice == "library:zh/mid_man_jovial"
