"""Covers for books without one: the cover templates (M4B-4)."""

import json

from PIL import Image

from huashuo import cover
from huashuo.pipeline import book_cover
from huashuo.workdir import Workdir


def test_titles_wrap_into_balanced_lines_like_the_template_examples():
    lines = cover._balanced_lines
    assert lines("不成问题的问题", [360] * 7, 2843) == ["不成问题的问题"]
    assert lines("一个无政府主义者的意外死亡", [360] * 13, 2843) == ["一个无政府主义", "者的意外死亡"]   # 7 + 6
    assert lines("一二三四五六七八九", [360] * 9, 2843) == ["一二三四五", "六七八九"]                    # not 7 + 2
    assert lines("春江花月夜，你好！", [360] * 9, 2843) == ["春江花月", "夜，你好！"]                    # no line starts with ，
    assert lines("长", [3000], 2843) is None


def test_the_template_follows_the_title_and_all_templates_are_there():
    templates = cover._spec()["templates"]
    assert len(templates) == 4 and all((cover.COVERS / t["image"]).is_file() for t in templates)
    assert (cover.COVERS / cover._spec()["font"]).is_file() and (cover.COVERS / "OFL.txt").is_file()
    assert cover.pick_template("热包子") is cover.pick_template("热包子")
    assert len({id(cover.pick_template(t)) for t in ["一", "二", "三", "四", "五", "六", "七", "八"]}) > 1


def test_a_generated_cover_is_redrawn_only_when_the_title_changes(tmp_path):
    wd = Workdir(tmp_path / "book.huashuo")
    first = book_cover(wd, "在桥上")
    with Image.open(first) as image:
        assert first.name == "cover.generated.jpg" and image.size == (3000, 3000)
    stamp = first.stat().st_mtime_ns
    assert book_cover(wd, "在桥上").stat().st_mtime_ns == stamp                    # reused
    book_cover(wd, "桥上")
    assert json.loads((wd.state / "cover.json").read_text(encoding="utf-8"))["title"] == "桥上"
    assert first.stat().st_mtime_ns != stamp                                        # redrawn


def test_the_books_own_cover_wins(tmp_path):
    wd = Workdir(tmp_path / "book.huashuo")
    wd.root.mkdir(parents=True)
    assert book_cover(wd, "在桥上").name == "cover.generated.jpg"
    Image.new("RGB", (600, 800), (200, 10, 10)).save(wd.cover(".jpg"))              # the book's, or the user's
    assert book_cover(wd, "在桥上") == wd.cover(".jpg")
