"""The stages behind the CLI: import, check, plan, synthesize, package.

Each stage reads what the previous one left in the work directory, so any of them can be
re-run on its own (CLI-3) and a crash in one never costs the work of the others.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from huashuo import __version__
from huashuo.cast import default_cast, load_cast, merge_cast
from huashuo.huaben import Script, check, merge, read_script, write_script
from huashuo.ingest import read_book
from huashuo.structure import build
from huashuo.units import DEFAULT_MAX_CHARS, Plan, plan
from huashuo.workdir import Workdir, read_json, write_bytes_atomic, write_json_atomic, write_text_atomic

# Rough planning figures from the M1 Max measurements (design doc §5.5, appendix A).
CHARS_PER_AUDIO_SECOND = {"zh": 4.4, "en": 15.0}
DEFAULT_RTF = 2.7
CACHE_BYTES_PER_SECOND = 24000 * 2            # 16-bit mono WAV at 24 kHz
M4B_BYTES_PER_SECOND = 64000 / 8


class PipelineError(Exception):
    pass


@dataclass
class ImportResult:
    workdir: Workdir
    script: Script
    text: str
    chapters: int
    skipped: int
    encoding: str | None
    merge_report: list[str] = field(default_factory=list)
    llm: "LLMReport | None" = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class LLMOptions:
    """How the import may use an LLM for speaker attribution."""
    enabled: bool = True             # False (--no-llm): cached answers only, no calls
    model: str | None = None
    base_url: str | None = None
    max_cost: float | None = None
    assume_yes: bool = False         # --yes: agree to send the text to the endpoint
    confirm: Callable[[str], bool] | None = None   # asks the user; None when not interactive
    concurrency: int | None = None   # pass-2 calls in flight (default 6)
    progress: Callable | None = None # (stage, done, total, usage) after each chunk
    model_override: object = None    # tests: a pydantic-ai FunctionModel
    config_override: object = None   # tests: an LLMConfig


@dataclass
class LLMReport:
    mode: str                        # "calls", "cache" (no calls allowed), "none" (no quotes / nothing cached)
    model: str | None = None
    usage: object = None
    estimate: float | None = None
    stopped: str | None = None
    review: list[dict] = field(default_factory=list)
    notice: str | None = None
    suggestions_failed: str | None = None


@dataclass
class MachineOutput:
    """Everything the machine derives from the source: text, script (split into narration
    and dialogue, with speakers) and cast. Each is merged with the user's files once."""
    text: str
    script: Script
    cast: dict
    chapters: int
    skipped: int
    llm: LLMReport | None = None


def _readable_chars(script: Script) -> int:
    return sum(len(b.get("text", "")) for b in script.blocks if b.get("type") in ("narration", "dialogue"))


def run_llm_stage(script: Script, language: str, wd: Workdir, record: dict,
                  options: LLMOptions, narrator: str | None = None) -> tuple[object | None, LLMReport]:
    """Attribute speakers (and suggest voices for the main characters), with calls if
    allowed and configured, else from the cache."""
    from huashuo.attribution import Caller, attribute_script, estimate_cost, llm_config, offline_config
    from huashuo.library import castable, has_library

    voices = castable(language) if has_library(language) else []      # nothing to suggest from

    if not any(b.get("type") == "dialogue" for b in script.blocks):
        return None, LLMReport("none")
    cache = wd.state / "llm-cache"
    config = options.config_override or (llm_config(options.model, options.base_url, options.max_cost,
                                                    options.concurrency) if options.enabled else None)
    if config is None:
        notice = None if not options.enabled else (
            "no LLM configured (HUASHUO_LLM_API_KEY / OPENAI_API_KEY); dialogue is read by the narrator. "
            "Use --no-llm to silence this.")
        if not cache.is_dir() or not any(cache.iterdir()):
            return None, LLMReport("none", notice=notice)
        caller = Caller(offline_config(options.model or record.get("llm_model")), cache, offline=True)
        attribution = attribute_script(script, language, caller, options.progress, voices, narrator)
        return attribution, LLMReport("cache", caller.config.model, caller.usage, None,
                                      attribution.stopped and "some quotes are not in the answer cache and stay "
                                                              "\"unknown\"", attribution.review, notice,
                                      attribution.suggestions_failed)

    chars = _readable_chars(script)
    estimate = estimate_cost(chars, config)
    if estimate > config.max_cost:
        raise PipelineError(f"speaker attribution is estimated at ${estimate:.2f} with {config.model}, above the "
                            f"${config.max_cost:.2f} cap; raise it with --max-llm-cost, or use --no-llm")
    consented = record.get("llm_consent", [])
    if not config.local and config.endpoint not in consented:
        message = (f"全书约 {chars:,} 字将发送给 {config.endpoint}（模型 {config.model}）用于说话人标注，"
                   f"预计费用约 ${estimate:.2f}（上限 ${config.max_cost:.2f}）。\n"
                   f"The text (about {chars:,} characters) will be sent to {config.endpoint} ({config.model}) "
                   f"for speaker attribution, estimated ${estimate:.2f} (cap ${config.max_cost:.2f}). Continue?")
        if not (options.assume_yes or (options.confirm is not None and options.confirm(message))):
            raise PipelineError(f"not sending the book to {config.endpoint} without your agreement: run again "
                                f"with --yes, or use --no-llm to skip speaker attribution")
        record["llm_consent"] = consented + [config.endpoint]
    caller = Caller(config, cache, model=options.model_override)
    attribution = attribute_script(script, language, caller, options.progress, voices, narrator)
    record["llm_model"] = config.model
    return attribution, LLMReport("calls", config.model, caller.usage, estimate, attribution.stopped,
                                  attribution.review, suggestions_failed=attribution.suggestions_failed)


def machine_output(book, language: str | None, read_notes: bool, wd: Workdir | None = None,
                   record: dict | None = None, llm: LLMOptions | None = None,
                   narrator: str | None = None, fixed_voices: dict[str, str] | None = None) -> MachineOutput:
    """build -> split -> attribute speakers -> cast voices. `narrator` and `fixed_voices`
    are the user's current choices, which casting works around."""
    from huashuo.casting import cast_voices, conversations
    from huashuo.library import has_library

    built = build(book, language, read_notes)
    lang = built.script.header["language"]
    cast = default_cast(lang)
    report = None
    if wd is not None and llm is not None:
        narrator = narrator or cast["narrator"]["voice"]
        attribution, report = run_llm_stage(built.script, lang, wd, record if record is not None else {}, llm,
                                            narrator)
        if attribution is not None:
            built.script.blocks = attribution.blocks
            characters = attribution.characters
            if has_library(lang):
                voices = cast_voices(characters, narrator, lang, conversations(attribution.blocks), fixed_voices,
                                     suggested=attribution.suggested_voices)
            else:
                # No designed voices for this language yet (English in stage 1, requirements
                # Q19): the narrator reads everyone, unless the user named a voice.
                voices = {name: (fixed_voices or {}).get(name, narrator) for name in characters}
            cast["characters"] = {name: {**entry, "voice": voices[name]} for name, entry in characters.items()}
    return MachineOutput(built.text, built.script, cast, built.chapters, built.skipped, report)


def _user_voice_choices(wd: Workdir, language: str) -> tuple[str | None, dict[str, str]]:
    """The narrator voice in cast.json, and character voices the user changed by hand."""
    if not wd.cast.is_file():
        return None, {}
    current = load_cast(wd.cast, language)
    base = (read_json(wd.cast_base) or {}).get("characters", {})
    fixed = {name: c["voice"] for name, c in current.get("characters", {}).items()
             if c.get("voice") and base.get(name, {}).get("voice") != c["voice"]}
    return current["narrator"]["voice"], fixed


def write_review(wd: Workdir, review: list[dict]) -> Path | None:
    """Quotes whose speaker is unknown or uncertain, for the user to check first (SCR-11)."""
    path = wd.root / "review.txt"
    if not review:
        path.unlink(missing_ok=True)
        return None
    lines = ["# 待审阅的对白：说话人未知或把握不高。改 script.huaben.jsonl 中对应 id 的 speaker 即可。",
             "# Quotes with an unknown or uncertain speaker; fix `speaker` for the id in script.huaben.jsonl.", ""]
    for item in review:
        conf = "" if item["conf"] is None else f" ({item['conf']:.2f})"
        lines.append(f"{item['id']}\t{item['speaker']}{conf}\t{item['text']}")
    write_text_atomic(path, "\n".join(lines) + "\n")
    return path


def _place_cover(wd: Workdir, book, cover: Path | None, previous: dict) -> tuple[Path | None, str | None]:
    """The cover to use, and who chose it. A cover the user supplied (--cover, recorded in
    state/ingest.json) is never replaced by the book's own on a later import."""
    existing = wd.find_cover()
    if cover is not None:
        for old in (wd.cover(e) for e in (".jpg", ".jpeg", ".png")):
            old.unlink(missing_ok=True)
        path = wd.cover(cover.suffix.lower() if cover.suffix.lower() in (".jpg", ".jpeg", ".png") else ".jpg")
        write_bytes_atomic(path, cover.read_bytes())
        return path, "user"
    if existing is not None and previous.get("cover_from") == "user":
        return existing, "user"
    if book.cover:
        path = wd.cover(book.cover_ext)
        write_bytes_atomic(path, book.cover)
        return path, "book"
    return existing, previous.get("cover_from")


def import_book(source: Path, wd: Workdir, encoding: str | None = None,
                language: str | None = None, cover: Path | None = None,
                read_notes: bool | None = None, llm: LLMOptions | None = None,
                title: str | None = None, author: str | None = None) -> ImportResult:
    """Read the source and (re)build text.txt, the script and the cast, keeping user edits.

    Options left as None reuse what the previous import of this book recorded.
    """
    record = read_json(wd.ingest_record) or {}
    previous = record.get("options", {})
    encoding = encoding if encoding is not None else previous.get("encoding")
    language = language if language is not None else previous.get("language")
    read_notes = read_notes if read_notes is not None else previous.get("read_notes", False)
    title = title if title is not None else previous.get("title")
    author = author if author is not None else previous.get("author")
    book = read_book(source, encoding)
    narrator, fixed = _user_voice_choices(wd, language or record.get("language") or "zh")
    machine = machine_output(book, language, read_notes, wd, record, llm if llm is not None else LLMOptions(),
                             narrator, fixed)
    header = machine.script.header
    # --title / --author win over what the book says (IN-4); applied to the machine's
    # version, so an edit the user makes to the header afterwards still wins on merge.
    if title:
        header["title"] = title
    if author:
        header["author"] = author
    header["source"] = {"path": os.path.relpath(source.resolve(), wd.root.resolve()),
                        "format": book.format, "sha256": _sha256_file(source)}
    cover_path, cover_from = _place_cover(wd, book, cover, record)
    if cover_path is not None:
        header["cover"] = cover_path.name

    write_text_atomic(wd.text, machine.text)

    base = read_script(wd.script_base) if wd.script_base.is_file() else None
    current = read_script(wd.script) if wd.script.is_file() else None
    result = merge(base, current, machine.script)
    merged = result.script
    # The text is regenerated on every import, so its fingerprint always follows it.
    merged.header["text_sha256"] = header["text_sha256"]
    write_script(wd.script, merged)
    write_script(wd.script_base, machine.script)
    if result.orphans:
        with (wd.state / "orphaned-edits.jsonl").open("a", encoding="utf-8") as handle:
            for orphan in result.orphans:
                handle.write(json.dumps({"saved": time.strftime("%Y-%m-%d %H:%M:%S"), **orphan},
                                        ensure_ascii=False) + "\n")

    base_cast = read_json(wd.cast_base)
    current_cast = load_cast(wd.cast, header["language"]) if wd.cast.is_file() else None
    cast, cast_report = merge_cast(base_cast, current_cast, machine.cast)
    write_json_atomic(wd.cast, cast)
    write_json_atomic(wd.cast_base, machine.cast)

    if machine.llm is not None and machine.llm.mode != "none":
        write_review(wd, machine.llm.review)
    write_json_atomic(wd.ingest_record, {"huashuo": __version__, "imported": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "source": str(source.resolve()), "encoding": book.encoding,
                                         "language": header["language"], "cover_from": cover_from,
                                         "llm_model": record.get("llm_model"),
                                         "llm_consent": record.get("llm_consent", []),
                                         "options": {"encoding": encoding, "language": language,
                                                     "read_notes": read_notes, "title": title,
                                                     "author": author}})
    return ImportResult(wd, merged, machine.text, machine.chapters, machine.skipped, book.encoding,
                        result.report + cast_report, machine.llm)


@dataclass
class Project:
    workdir: Workdir
    script: Script
    text: str
    cast: dict

    @property
    def language(self) -> str:
        return self.script.header.get("language", "zh")


def load_project(wd: Workdir) -> Project:
    if not wd.script.is_file():
        raise PipelineError(f"no script in {wd.root}; run `huashuo import` first")
    script = read_script(wd.script)
    return Project(wd, script, wd.text.read_text(encoding="utf-8"),
                   load_cast(wd.cast, script.header.get("language", "zh")))


def check_project(project: Project) -> list:
    return check(project.script, project.text, project.cast)


def make_plan(project: Project, *, read_titles: bool = True, voice: str | None = None,
              max_chars: int = DEFAULT_MAX_CHARS, sample_chars: int | None = None,
              chapters: set[int] | None = None, emotions: bool = True) -> Plan:
    """The units to synthesize; optionally only the first `sample_chars` characters of
    reading, or only some chapters (1-based, as listed by `huashuo import`)."""
    full = plan(project.script, project.cast, project.language, max_chars, read_titles, voice, emotions)
    if chapters:
        keep = [i for i, u in enumerate(full.units) if u.chapter + 1 in chapters]
        return _subset(full, keep)
    if sample_chars is not None:
        keep, budget = [], 0
        for i, unit in enumerate(full.units):
            keep.append(i)
            budget += len(unit.text) if unit.kind == "body" else 0
            if budget >= sample_chars:
                break
        return _subset(full, keep)
    return full


def _subset(full: Plan, keep: list[int]) -> Plan:
    from huashuo.units import END, Chapter

    index = {old: new for new, old in enumerate(keep)}
    units = [full.units[i] for i in keep]
    chapters = []
    for number, ch in enumerate(full.chapters):
        firsts = [index[i] for i in keep if full.units[i].chapter == number]
        if firsts:
            chapters.append(Chapter(ch.title, ch.block_id, min(firsts), ch.level))
    if units:
        units[-1].after = END
    return Plan(units=units, chapters=chapters)


@dataclass
class Estimate:
    chars: int
    audio_seconds: float
    synth_seconds: float
    cache_bytes: float
    m4b_bytes: float


def estimate(p: Plan, language: str, rtf: float = DEFAULT_RTF) -> Estimate:
    audio = p.chars / CHARS_PER_AUDIO_SECOND.get(language, 4.4)
    return Estimate(p.chars, audio, audio / rtf, audio * CACHE_BYTES_PER_SECOND, audio * M4B_BYTES_PER_SECOND)
