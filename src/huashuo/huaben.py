"""The 话本 (Script IR): reading, writing, checking and merging *.huaben.jsonl files.

Format: docs/script-ir.md. Blocks are plain dicts rather than classes so that fields this
version does not know about (future block types, the user's own notes) survive a
read-write round trip untouched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from huashuo.workdir import write_text_atomic

FORMAT_VERSION = 1

READABLE_TYPES = {"chapter", "heading", "narration", "dialogue"}
SILENT_TYPES = {"skip", "break"}
RESERVED_TYPES = {"equation", "figure", "table", "footnote"}
KNOWN_TYPES = READABLE_TYPES | SILENT_TYPES | RESERVED_TYPES

# Written first, in this order; every other field follows in insertion order, then `src`.
LEADING_FIELDS = ("id", "type", "text")


class HuabenError(Exception):
    """A 话本 file that cannot be parsed at all."""


@dataclass
class Script:
    header: dict
    blocks: list[dict]
    # 1-based line number of each block in the file it was read from, for messages.
    lines: list[int] = field(default_factory=list)

    def line_of(self, index: int) -> int | None:
        return self.lines[index] if index < len(self.lines) else None


@dataclass
class Problem:
    code: str          # invariant id from docs/script-ir.md §5, e.g. "I4"
    message: str
    block_id: str | None = None
    line: int | None = None

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else ""
        if self.block_id:
            where = f"{where} [{self.block_id}]".strip()
        return f"{self.code} {where}: {self.message}" if where else f"{self.code}: {self.message}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def spoken_text(block: dict) -> str:
    """What the block actually reads aloud: `say` when present, otherwise `text`."""
    say = block.get("say")
    return say if isinstance(say, str) else block.get("text", "")


# --------------------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------------------


def dump_record(record: dict) -> str:
    ordered = {k: record[k] for k in LEADING_FIELDS if k in record}
    ordered.update((k, v) for k, v in record.items() if k not in LEADING_FIELDS and k != "src")
    if "src" in record:
        ordered["src"] = record["src"]
    return json.dumps(ordered, ensure_ascii=False)


def dumps(script: Script) -> str:
    return "\n".join([dump_record(script.header), *map(dump_record, script.blocks)]) + "\n"


def write_script(path: Path, script: Script) -> None:
    write_text_atomic(path, dumps(script))


def loads(content: str, source: str = "<string>") -> Script:
    header, blocks, lines = None, [], []
    for number, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise HuabenError(f"{source}:{number}: not valid JSON ({exc.msg})") from exc
        if not isinstance(record, dict):
            raise HuabenError(f"{source}:{number}: each line must be a JSON object")
        if header is None:
            if record.get("type") != "huaben":
                raise HuabenError(f"{source}:{number}: the first record must be the header "
                                  f'(type "huaben")')
            version = record.get("version")
            if not isinstance(version, int) or version > FORMAT_VERSION:
                raise HuabenError(f"{source}: format version {version!r} is newer than this "
                                  f"program supports ({FORMAT_VERSION}); upgrade huashuo")
            header = record
            continue
        if record.get("type") == "huaben":
            raise HuabenError(f"{source}:{number}: a second header record (I1)")
        blocks.append(record)
        lines.append(number)
    if header is None:
        raise HuabenError(f"{source}: empty file, no header record")
    return Script(header=header, blocks=blocks, lines=lines)


def read_script(path: Path) -> Script:
    return loads(path.read_text(encoding="utf-8"), source=str(path))


# --------------------------------------------------------------------------------------
# Invariants (docs/script-ir.md §5)
# --------------------------------------------------------------------------------------


def check(script: Script, text: str, cast: dict | None = None, limit: int = 50) -> list[Problem]:
    """Return every invariant violation, most important first; empty means the script is sound."""
    problems: list[Problem] = []

    def add(code, message, index=None):
        block_id = script.blocks[index].get("id") if index is not None else None
        line = script.line_of(index) if index is not None else None
        problems.append(Problem(code, message, block_id, line))

    if script.header.get("text_sha256") not in (None, sha256_text(text)):
        add("I8", "text.txt has changed since the script was built; re-run `huashuo import`")

    seen: dict[str, int] = {}
    covered = bytearray(len(text))
    last_end = 0
    first_readable = None
    speakers = set((cast or {}).get("characters", {})) | {"unknown"}

    for i, block in enumerate(script.blocks):
        block_id, kind = block.get("id"), block.get("type")
        if not isinstance(block_id, str) or not block_id:
            add("I2", "block without an id", i)
        elif block_id in seen:
            add("I2", f"duplicate id (first used on line {script.line_of(seen[block_id])})", i)
        else:
            seen[block_id] = i

        if kind not in KNOWN_TYPES:
            add("type", f"unknown block type {kind!r}", i)
        if kind in READABLE_TYPES and first_readable is None:
            first_readable = i
        if kind in READABLE_TYPES and not spoken_text(block).strip():
            add("empty", "readable block with nothing to read", i)
        if kind == "dialogue" and cast is not None and block.get("speaker") not in speakers:
            add("I7", f"speaker {block.get('speaker')!r} is not in cast.json", i)

        src = block.get("src")
        if src is None:
            continue  # inserted by the user: read, but not part of the source text
        if (not isinstance(src, list) or len(src) != 2 or not all(isinstance(x, int) for x in src)
                or not 0 <= src[0] <= src[1] <= len(text)):
            add("I3", f"invalid src {src!r}", i)
            continue
        start, end = src
        if start < last_end:
            add("I3", f"src {src} overlaps or goes back before the previous block (ends at {last_end})", i)
        last_end = max(last_end, end)
        if block.get("text") != text[start:end]:
            add("I4", f"text differs from text.txt[{start}:{end}] = {text[start:end]!r}; "
                      f"to change what is read, set `say` instead", i)
        covered[start:end] = b"\x01" * (end - start)

    if first_readable is not None and script.blocks[first_readable].get("type") != "chapter":
        add("I6", "the first readable block must be a chapter", first_readable)

    missing = [pos for pos, ch in enumerate(text) if not covered[pos] and not ch.isspace()]
    if missing:
        runs, start = [], missing[0]
        for a, b in zip(missing, missing[1:] + [None]):
            if b != a + 1:
                runs.append((start, a + 1))
                if b is not None:
                    start = b
        shown = ", ".join(f"[{s}, {e}) {text[s:e][:20]!r}" for s, e in runs[:5])
        more = f" and {len(runs) - 5} more" if len(runs) > 5 else ""
        problems.append(Problem("I5", f"{len(missing)} characters of text.txt are in no block: "
                                      f"{shown}{more}"))

    return problems[:limit]


# --------------------------------------------------------------------------------------
# Three-way merge (docs/script-ir.md §7)
# --------------------------------------------------------------------------------------


def merge_fields(base: dict, current: dict, new: dict) -> dict:
    """Field-wise merge: a field the user changed (current != base) wins, otherwise take new."""
    merged = {}
    for key in list(new) + [k for k in current if k not in new]:
        cur, old = current.get(key, _MISSING), base.get(key, _MISSING)
        value = cur if cur != old else new.get(key, _MISSING)
        if value is not _MISSING:
            merged[key] = value
    return merged


_MISSING = object()


def merge(base: Script | None, current: Script | None, new: Script) -> tuple[Script, list[str]]:
    """Merge a freshly generated script into the one on disk, keeping the user's edits.

    `base` is what the machine wrote last time, `current` is the file as it is now (maybe
    edited), `new` is what the machine produced this time. Returns the merged script and a
    report of the user edits that were kept or could not be placed.
    """
    if current is None:
        return new, []
    if base is None:
        # No record of what the machine wrote, so edits cannot be told apart from output.
        # Keep the file as the user has it rather than risk overwriting their work.
        return current, ["no merge base in state/; kept the existing script unchanged"]

    report: list[str] = []
    base_by = {b["id"]: b for b in base.blocks if "id" in b}
    cur_by = {b["id"]: b for b in current.blocks if "id" in b}
    new_ids = {b["id"] for b in new.blocks}

    merged: list[dict] = []
    for block in new.blocks:
        bid = block["id"]
        if bid in cur_by:
            cur = cur_by[bid]
            out = merge_fields(base_by.get(bid, {}), cur, block)
            if bid in base_by and cur != base_by[bid]:
                report.append(f"kept your edit to {bid}")
            merged.append(out)
        elif bid in base_by:
            report.append(f"kept your deletion of {bid}")
        else:
            merged.append(block)

    # Blocks the user added, and edited blocks the machine no longer produces: place each
    # after the nearest preceding block (in the user's order) that made it into the result.
    position = {b["id"]: i for i, b in enumerate(merged)}
    inserts: list[tuple[int, dict]] = []
    previous = None
    for block in current.blocks:
        bid = block.get("id")
        if bid in new_ids:
            if bid in position:  # the user may have deleted it
                previous = bid
            continue
        if bid in base_by and block == base_by[bid]:
            continue  # untouched block the machine dropped: let it go
        note = "added" if bid not in base_by else "edited, no longer generated"
        report.append(f"kept block {bid} ({note}); check its position")
        inserts.append((position[previous] + 1 if previous in position else 0, block))
    for offset, (at, block) in enumerate(sorted(inserts, key=lambda x: x[0])):
        merged.insert(at + offset, block)

    header = merge_fields(base.header, current.header, new.header)
    if current.header != base.header:
        report.append("kept your edits to the header")
    return Script(header=header, blocks=merged), report
