"""Golden tests on a real story: examples/fish (一条被洗澡水拍死的鱼, by 半轻人, CC BY-NC-ND 4.0).

Offline and free like every test: speaker attribution replays the LLM answers stored in
examples/fish/llm-cache. When the prompts change those answers no longer match; the
speaker tests are then skipped with a note to re-record them (examples/refresh_golden.py,
a capped manual run of about $0.20).
"""

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


def _gold() -> list[tuple[str, str]]:
    rows = [line.split("\t") for line in (FISH / "speakers.tsv").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    return [(quote, speaker) for _, speaker, quote, *_ in rows]


def _attributed(tmp_path: Path, source: Path):
    book = tmp_path / source.name
    shutil.copy(source, book)
    wd = Workdir.for_input(book)
    shutil.copytree(FISH / "llm-cache", wd.state / "llm-cache")
    result = import_book(book, wd, llm=LLMOptions(enabled=False, model="gpt-6-sol"))
    if result.llm is None or result.llm.stopped:
        pytest.skip("the stored LLM answers no longer match the prompts; re-record them with "
                    "examples/refresh_golden.py (about $0.20)")
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
    assert "first-person narrator 我" in result.narrator_reason
    # 我's gender is not stated in the text; the LLM answers male or unknown, which picks
    # the male narrator or the default (female) one.
    gender = cast["characters"]["我"]["gender"]
    assert result.narrator == ("library:zh/narrator_male" if gender == "male" else "library:zh/narrator_female")
    voices = {name: c["voice"] for name, c in cast["characters"].items()}
    assert voices["我"] == cast["narrator"]["voice"]
    assert len(set(voices.values())) == len(voices)                           # 栖芒 and 李忱 each their own
    plan = make_plan(load_project(wd))
    assert plan.units[0].text == "《一条被洗澡水拍死的鱼》，作者半轻人。"
