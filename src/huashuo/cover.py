"""Covers for books that have none (M4B-4).

A cover the user gives (--cover) or the EPUB's own always wins. Otherwise the title is set
on one of the designer's templates (covers/templates.json): four 3000x3000 backgrounds of
one style, the title in Noto Serif SC Bold, white, 360 px, centred at the top of a box.
The template is picked from the title, so a book keeps its cover across runs.

The title wraps into balanced lines (13 characters: 7 + 6, as in the designer's examples)
and never starts a line with closing punctuation; a title that needs more lines than the
box holds at 360 px is set smaller. Line spacing is the font's own (ascent + descent, as
Figma's "auto"). The author is not shown, as the design asks.

A generated cover lives in the work directory as cover.generated.jpg, next to a record of
what it was made from; it is made again when the title or the templates change, and is
never taken for a cover the user or the book supplied.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

COVERS = Path(__file__).with_name("covers")
RENDERER_VERSION = 1
GENERATED = "cover.generated.jpg"
_NO_LINE_START = set("，。、；：？！」』”’）》〉】…—,.;:?!)]")
_FIT_TOLERANCE = 1.01          # two 360 px lines are 1036 px in the 1034 px box, as designed


@lru_cache(maxsize=1)
def _spec() -> dict:
    return json.loads((COVERS / "templates.json").read_text(encoding="utf-8"))


def spec_fingerprint() -> str:
    """Changes when the templates, the font or the renderer change."""
    digest = hashlib.sha256(json.dumps(_spec(), sort_keys=True).encode())
    for name in [t["image"] for t in _spec()["templates"]] + [_spec()["font"]]:
        stat = (COVERS / name).stat()
        digest.update(f"{name}:{stat.st_size}".encode())
    digest.update(str(RENDERER_VERSION).encode())
    return digest.hexdigest()[:12]


def pick_template(title: str) -> dict:
    templates = _spec()["templates"]
    return templates[int(hashlib.sha256(title.encode("utf-8")).hexdigest(), 16) % len(templates)]


def _balanced_lines(title: str, widths: list[float], max_width: float) -> list[str] | None:
    """Split into the fewest lines no wider than max_width, as even as possible; a line
    never starts with closing punctuation. None if a single character is too wide."""
    n = len(title)
    if not n or max(widths) > max_width:
        return None
    prefix = [0.0]
    for w in widths:
        prefix.append(prefix[-1] + w)
    width = lambda i, j: prefix[j] - prefix[i]
    for lines in range(1, n + 1):
        # Minimise the widest line over all splits into `lines` pieces (small n: DP).
        best: dict[tuple[int, int], tuple[float, list[int]]] = {}

        def solve(start: int, left: int) -> tuple[float, list[int]] | None:
            if (start, left) in best:
                return best[(start, left)]
            if left == 1:
                result = (width(start, n), [n]) if width(start, n) <= max_width else None
            else:
                result = None
                for end in range(start + 1, n - left + 2):
                    if width(start, end) > max_width:
                        break
                    if title[end] in _NO_LINE_START:
                        continue
                    rest = solve(end, left - 1)
                    if rest is not None:
                        candidate = (max(width(start, end), rest[0]), [end] + rest[1])
                        if result is None or candidate[0] <= result[0]:   # ties: longer lines first
                            result = candidate
            best[(start, left)] = result
            return result

        found = solve(0, lines)
        if found is not None:
            cuts = [0] + found[1]
            return [title[a:b] for a, b in zip(cuts, cuts[1:])]
    return None


def render(title: str, path: Path) -> Path:
    """Draw the cover for `title` into `path` (JPEG, 3000x3000)."""
    from PIL import Image, ImageDraw, ImageFont

    spec, template = _spec(), pick_template(title)
    left, top, box_w, box_h = template["title_box"]
    style = spec["title"]
    with Image.open(COVERS / template["image"]) as background:
        image = background.convert("RGB")
    draw = ImageDraw.Draw(image)
    title = " ".join(title.split())
    for size in range(style["size"], style["min_size"] - 1, -10):
        font = ImageFont.truetype(str(COVERS / spec["font"]), size)
        ascent, descent = font.getmetrics()
        line_height = ascent + descent
        lines = _balanced_lines(title, [font.getlength(ch) for ch in title], box_w)
        if lines and len(lines) * line_height <= box_h * _FIT_TOLERANCE:
            break
    lines = lines or [title]
    x = left + box_w / 2 if style["align"] == "center" else left
    anchor = "ma" if style["align"] == "center" else "la"          # top of the line box
    for i, line in enumerate(lines):
        draw.text((x, top + i * line_height), line, font=font, fill=style["color"], anchor=anchor)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=90)
    return path


def generated_cover(root: Path, state: Path, title: str) -> Path:
    """The generated cover in a work directory, made again only when needed."""
    path, record = root / GENERATED, state / "cover.json"
    key = {"title": title, "templates": spec_fingerprint()}
    try:
        current = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    if current != key or not path.is_file():
        render(title, path)
        state.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(key, ensure_ascii=False), encoding="utf-8")
    return path
