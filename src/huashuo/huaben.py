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
HEADER_FIELDS = ("type", "version", "title", "author", "language")


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
    leading = HEADER_FIELDS if record.get("type") == "huaben" else LEADING_FIELDS
    ordered = {k: record[k] for k in leading if k in record}
    ordered.update((k, v) for k, v in record.items() if k not in leading and k != "src")
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
    whole_file: list[Problem] = []   # reported first, so a long list cannot hide them

    def add(code, message, index=None):
        block_id = script.blocks[index].get("id") if index is not None else None
        line = script.line_of(index) if index is not None else None
        problems.append(Problem(code, message, block_id, line))

    if script.header.get("text_sha256") not in (None, sha256_text(text)):
        whole_file.append(Problem("I8", "text.txt has changed since the script was built; re-run `huashuo import`"))

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
        whole_file.append(Problem("I5", f"{len(missing)} characters of text.txt are in no block: "
                                        f"{shown}{more}"))

    return (whole_file + problems)[:limit]


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


# Fields that locate a block in text.txt; they always come from the new build.
_POSITION_FIELDS = ("id", "src")


@dataclass
class MergeResult:
    script: Script
    report: list[str] = field(default_factory=list)
    # User edits whose paragraph no longer exists in the new build (the source text changed):
    # never applied to some other paragraph, returned so the caller can keep them.
    orphans: list[dict] = field(default_factory=list)


def _squash(text: str) -> str:
    return "".join(str(text).split())


def _align(base: list[dict], new: list[dict]) -> tuple[dict[int, int], dict[int, list[int]]]:
    """Map base blocks to new blocks by their text, not their id.

    Returns one-to-one matches and splits (one base block whose text is now spread over
    consecutive new blocks, e.g. a paragraph divided into narration and dialogue).
    """
    import difflib

    a = [_squash(b.get("text", "")) for b in base]
    b = [_squash(n.get("text", "")) for n in new]
    one: dict[int, int] = {}
    split: dict[int, list[int]] = {}
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            one.update({i1 + k: j1 + k for k in range(i2 - i1)})
            continue
        j = j1
        for i in range(i1, i2):          # inside a changed region, look for splits
            if not a[i]:
                continue
            k, joined = j, ""
            while k < j2 and len(joined) < len(a[i]):
                joined += b[k]
                k += 1
            if joined == a[i] and k - j > 1:
                split[i] = list(range(j, k))
                j = k
            elif j < j2 and b[j] == a[i]:
                one[i] = j
                j += 1
    return one, split


def _carry_to_pieces(edited: dict, base: dict, pieces: list[dict]) -> tuple[list[dict], dict]:
    """Apply a user's edits of one paragraph to the pieces it was split into.

    A changed type (e.g. skip) applies to every piece, pause_after to the last one, other
    added fields to the first; a `say` cannot be divided and is returned as left over.
    """
    changed = {k: v for k, v in edited.items() if base.get(k, _MISSING) != v and k not in _POSITION_FIELDS}
    removed = [k for k in base if k not in edited and k not in _POSITION_FIELDS]
    pieces = [dict(p) for p in pieces]
    left = {}
    for key, value in changed.items():
        if key == "type":
            for piece in pieces:
                piece["type"] = value
        elif key == "pause_after":
            pieces[-1]["pause_after"] = value
        elif key in ("say", "text"):
            left[key] = value
        else:
            pieces[0][key] = value
    for key in removed:
        for piece in pieces:
            piece.pop(key, None)
    return pieces, left


def merge(base: Script | None, current: Script | None, new: Script) -> MergeResult:
    """Merge a freshly generated script into the one on disk, keeping the user's edits.

    `base` is what the machine wrote last time, `current` is the file as it is now (maybe
    edited), `new` is what the machine produced this time. Blocks are matched by their
    text, so a paragraph inserted or removed upstream does not shift edits onto the wrong
    paragraph (docs/script-ir.md §7).
    """
    if current is None:
        return MergeResult(new)
    if base is None:
        # No record of what the machine wrote, so edits cannot be told apart from output.
        # Keep the file as the user has it rather than risk overwriting their work.
        return MergeResult(current, ["no merge base in state/; kept the existing script unchanged"])

    result = MergeResult(Script(header={}, blocks=[]))
    report, orphans = result.report, result.orphans
    base_by = {b["id"]: i for i, b in enumerate(base.blocks) if "id" in b}
    cur_by = {b["id"]: b for b in current.blocks if "id" in b}
    one, split = _align(base.blocks, new.blocks)

    # What becomes of each new block: replaced by a merged version, or dropped.
    out: list[dict | None] = list(new.blocks)
    origin: dict[str, int] = {}          # base id -> index in `out` where it now lives
    for i, b in enumerate(base.blocks):
        bid = b.get("id")
        if i in one:
            j = one[i]
            origin[bid] = j
            if bid not in cur_by:
                out[j] = None
                report.append(f"kept your deletion of {bid}")
            elif cur_by[bid] != b:
                merged = merge_fields(b, cur_by[bid], new.blocks[j])
                merged.update({k: new.blocks[j][k] for k in _POSITION_FIELDS if k in new.blocks[j]})
                out[j] = merged
                report.append(f"kept your edit to {bid}" + (f" (now {new.blocks[j]['id']})"
                                                            if new.blocks[j]["id"] != bid else ""))
        elif i in split:
            idx = split[i]
            origin[bid] = idx[-1]
            if bid not in cur_by:
                for j in idx:
                    out[j] = None
                report.append(f"kept your deletion of {bid} (now split into {len(idx)} blocks)")
            elif cur_by[bid] != b:
                pieces, left = _carry_to_pieces(cur_by[bid], b, [new.blocks[j] for j in idx])
                for j, piece in zip(idx, pieces):
                    out[j] = piece
                report.append(f"carried your edit to {bid} onto its {len(idx)} new blocks")
                if left:
                    orphans.append({"id": bid, "text": b.get("text"), "edits": left,
                                    "reason": "paragraph was split; this field cannot be divided"})
        elif bid in cur_by and cur_by[bid] != b:
            orphans.append({"id": bid, "text": b.get("text"),
                            "edits": {k: v for k, v in cur_by[bid].items() if b.get(k, _MISSING) != v},
                            "reason": "its text is no longer in the book"})

    for orphan in orphans:
        report.append(f"could not place your edit to {orphan['id']} ({orphan['reason']}); "
                      f"saved in state/orphaned-edits.jsonl")

    # Blocks the user added: keep each after the block it followed in the user's file.
    inserts: list[tuple[int, dict]] = []
    anchor = -1
    for block in current.blocks:
        bid = block.get("id")
        if bid in base_by:
            anchor = origin.get(bid, anchor)
            continue
        report.append(f"kept block {bid} (added by you)")
        inserts.append((anchor + 1, block))
    final: list[dict] = []
    pending = sorted(inserts, key=lambda x: x[0])
    for j, block in enumerate(out):
        while pending and pending[0][0] <= j:
            final.append(pending.pop(0)[1])
        if block is not None:
            final.append(block)
    final.extend(block for _, block in pending)

    result.script = Script(header=merge_fields(base.header, current.header, new.header), blocks=final)
    if current.header != base.header:
        report.append("kept your edits to the header")
    return result
