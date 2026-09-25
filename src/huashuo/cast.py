"""cast.json: who speaks and with which voice (docs/script-ir.md §8).

The import stage is the only writer of the machine's cast; the user may edit cast.json
freely. Re-imports merge per character, like the script merges per block: a field the
user changed wins, characters the user added or removed stay that way, and the
machine's newer view fills in everything else (CAST-10).
"""

from __future__ import annotations

import json
from pathlib import Path

from huashuo.huaben import merge_fields

CAST_VERSION = 1


class CastError(Exception):
    """cast.json cannot be used; the message says what to fix."""


def default_cast(language: str) -> dict:
    from huashuo.library import default_narrator

    return {"version": CAST_VERSION, "narrator": {"voice": default_narrator(language)},
            "characters": {}}


def validate(cast, where: str = "cast.json") -> dict:
    if not isinstance(cast, dict):
        raise CastError(f"{where}: expected a JSON object")
    narrator = cast.get("narrator")
    if not isinstance(narrator, dict) or not isinstance(narrator.get("voice"), str) or not narrator["voice"]:
        raise CastError(f'{where}: needs "narrator": {{"voice": "preset:<name>"}}')
    characters = cast.setdefault("characters", {})
    if not isinstance(characters, dict):
        raise CastError(f'{where}: "characters" must be an object keyed by character name')
    for name, entry in characters.items():
        if not isinstance(entry, dict):
            raise CastError(f"{where}: character {name!r} must be an object")
        voice = entry.get("voice")
        if voice is not None and not (isinstance(voice, str) and voice):
            raise CastError(f"{where}: character {name!r} has an invalid voice {voice!r}")
    return cast


def load_cast(path: Path, language: str) -> dict:
    """The cast on disk, validated; the language default when there is none."""
    if not path.is_file():
        return default_cast(language)
    try:
        cast = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CastError(f"{path}: not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})") from exc
    return validate(cast, str(path))


def merge_cast(base: dict | None, current: dict | None, new: dict) -> tuple[dict, list[str]]:
    """Three-way merge of casts, per character."""
    if current is None:
        return new, []
    if base is None:
        return current, ["no cast merge base in state/; kept cast.json unchanged"]
    report: list[str] = []
    merged = merge_fields(base, current, new)
    merged["narrator"] = merge_fields(base.get("narrator", {}), current.get("narrator", {}), new.get("narrator", {}))

    old, cur, fresh = (c.get("characters", {}) for c in (base, current, new))
    characters: dict[str, dict] = {}
    for name in list(fresh) + [n for n in cur if n not in fresh]:
        if name in fresh and name in cur:
            characters[name] = merge_fields(old.get(name, {}), cur[name], fresh[name])
            if name in old and cur[name] != old[name]:
                report.append(f"kept your edit to character {name}")
        elif name in fresh:
            if name in old:
                report.append(f"kept your removal of character {name}")
            else:
                characters[name] = fresh[name]
        elif name not in old:
            characters[name] = cur[name]
            report.append(f"kept character {name} (added by you)")
        elif cur[name] != old[name]:
            characters[name] = cur[name]
            report.append(f"kept character {name}: you edited it, though it is no longer detected")
    merged["characters"] = characters
    return merged, report
