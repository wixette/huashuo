"""The stages behind the CLI: import, check, plan, synthesize, package.

Each stage reads what the previous one left in the work directory, so any of them can be
re-run on its own (CLI-3) and a crash in one never costs the work of the others.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from huashuo import pron, punct
from huashuo.chinese import is_traditional, to_simplified
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
    narrator: str | None = None
    narrator_reason: str = ""
    voices: str = "multi"             # --single-voice / --multi-voice


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
    kept: int = 0                    # quotes that kept their previous answer


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
    narrator_reason: str = "default"


def _readable_chars(script: Script) -> int:
    return sum(len(b.get("text", "")) for b in script.blocks if b.get("type") in ("narration", "dialogue"))


def _check_cap(estimate: float, config) -> None:
    if estimate > config.max_cost:
        raise PipelineError(f"speaker attribution is estimated at ${estimate:.2f} with {config.model}, above the "
                            f"${config.max_cost:.2f} cap; raise it with --max-llm-cost, or use --no-llm")


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
    estimate = estimate_cost(script, language, config)
    agreed = False                           # to sending the text, in this run
    if cache.is_dir() and any(cache.iterdir()) and not config.local:
        # Attributed before: answer from the cache first, and ask before paying again
        # when it no longer covers the book (the text changed, or answers predate emotion
        # hints). Without an answer, the previous answers are kept (machine_output).
        offline = Caller(offline_config(config.model), cache, offline=True)
        attribution = attribute_script(script, language, offline, options.progress, voices, narrator)
        missing = sum(1 for b in attribution.blocks if b.get("type") == "dialogue" and "conf" not in b)
        if not missing and not attribution.without_emotions:
            return attribution, LLMReport("cache", config.model, offline.usage, None, None, attribution.review,
                                          suggestions_failed=attribution.suggestions_failed)
        quotes = sum(1 for b in script.blocks if b.get("type") == "dialogue")
        gap = (f"{missing} of {quotes} quotes have no answer in the cache (an earlier run stopped at the cost "
               f"cap, or the text changed since)" if missing else "the cached answers predate emotion hints")
        estimate = estimate_cost(script, language, config, cache)       # answers in the cache are free
        _check_cap(estimate, config)
        message = (f"{gap}. Attributing them sends the text to {config.endpoint} ({config.model}), estimated "
                   f"${estimate:.2f} more (cap ${config.max_cost:.2f}); otherwise the previous answers are kept. "
                   f"Continue?")
        if not (options.assume_yes or (options.confirm is not None and options.confirm(message))):
            return attribution, LLMReport(
                "cache", config.model, offline.usage, None, None, attribution.review,
                notice=f"{gap}; kept the previous answers. Run again with --yes to attribute again "
                       f"(about ${estimate:.2f}).", suggestions_failed=attribution.suggestions_failed)
        agreed = True
    _check_cap(estimate, config)
    consented = record.get("llm_consent", [])
    if not config.local and config.endpoint not in consented:
        if not agreed:
            message = (f"The text (about {chars:,} characters) will be sent to {config.endpoint} ({config.model}) "
                       f"for speaker attribution, estimated ${estimate:.2f} (cap ${config.max_cost:.2f}). Continue?")
            if not (options.assume_yes or (options.confirm is not None and options.confirm(message))):
                raise PipelineError(f"not sending the book to {config.endpoint} without your agreement: run again "
                                    f"with --yes, or use --no-llm to skip speaker attribution")
        # Remembered at once: an import stopped halfway (Ctrl-C) must not ask again.
        record["llm_consent"] = consented + [config.endpoint]
        stored = read_json(wd.ingest_record) or {}
        write_json_atomic(wd.ingest_record, {**stored, "llm_consent": record["llm_consent"]})
    caller = Caller(config, cache, model=options.model_override)
    attribution = attribute_script(script, language, caller, options.progress, voices, narrator)
    record["llm_model"] = config.model
    return attribution, LLMReport("calls", config.model, caller.usage, estimate, attribution.stopped,
                                  attribution.review, suggestions_failed=attribution.suggestions_failed)


def _carry_over(attribution, previous: Script, previous_cast: dict | None, language: str) -> int:
    """Quotes the model did not answer this time (answers not cached, calls not allowed or
    failed) keep the previous machine answer for the same text, and the characters they
    need come back from the previous cast. Returns how many quotes were filled."""
    # Previous texts are compared after today's clean-up, so a quote whose punctuation was
    # normalized since (TXT-7) is still found.
    same = lambda text: punct.normalize(text, language)
    answers: dict[str, set[tuple]] = {}
    narrated = {same(b["text"]) for b in previous.blocks if b.get("type") == "narration"}
    for b in previous.blocks:
        if b.get("type") == "dialogue" and b.get("speaker") not in (None, "unknown") and "conf" in b:
            answers.setdefault(same(b["text"]), set()).add((b["speaker"], b["conf"], b.get("emotion")))
    old_characters = (previous_cast or {}).get("characters", {})
    # Line the quotes up in order first, so a short line said by different people in
    # different places (「什么？」) gets the answer given at its own place.
    import difflib

    before = [b for b in previous.blocks if b.get("type") == "dialogue"]
    now = [b for b in attribution.blocks if b.get("type") == "dialogue"]
    placed: dict[int, dict] = {}
    matcher = difflib.SequenceMatcher(None, [same(b["text"]) for b in before], [b["text"] for b in now], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                placed[id(now[j1 + k])] = before[i1 + k]
    filled, answered_ids = 0, set()
    for b in attribution.blocks:
        if b.get("type") != "dialogue" or "conf" in b:
            continue
        old = placed.get(id(b))
        if old is not None and old.get("speaker") not in (None, "unknown") and "conf" in old:
            found = {(old["speaker"], old["conf"], old.get("emotion"))}
        else:
            found = answers.get(b["text"], set())
        if len(found) == 1:                                  # the same text, answered the same way
            speaker, conf, emotion = next(iter(found))
            if speaker not in attribution.characters and speaker not in old_characters:
                continue
            b["speaker"], b["conf"] = speaker, conf
            if emotion:
                b["emotion"] = emotion
            if speaker not in attribution.characters:
                attribution.characters[speaker] = {k: v for k, v in old_characters[speaker].items() if k != "voice"}
        elif not found and b["text"] in narrated:            # previously found not to be speech
            b["type"] = "narration"
            b.pop("speaker", None)
        else:
            continue
        filled += 1
        answered_ids.add(b["id"])
    if filled:
        # The cast pass may have stopped early too: bring back every previous character.
        for name, entry in old_characters.items():
            if name not in attribution.characters:
                attribution.characters[name] = {k: v for k, v in entry.items() if k != "voice"}
        lines: dict[str, int] = {}
        for b in attribution.blocks:
            if b.get("type") == "dialogue" and b.get("speaker") in attribution.characters:
                lines[b["speaker"]] = lines.get(b["speaker"], 0) + 1
        for name, entry in attribution.characters.items():
            entry["lines"] = lines.get(name, 0)
        attribution.review = [r for r in attribution.review if r["id"] not in answered_ids]
    return filled


def machine_output(book, language: str | None, read_notes: bool, wd: Workdir | None = None,
                   record: dict | None = None, llm: LLMOptions | None = None,
                   narrator: str | None = None, fixed_voices: dict[str, str] | None = None,
                   previous: Script | None = None, previous_cast: dict | None = None) -> MachineOutput:
    """build -> split -> attribute speakers -> cast voices. `narrator` and `fixed_voices`
    are the user's current choices, which casting works around. `previous` and
    `previous_cast` are the last machine results, which fill in what this run could not
    answer."""
    from huashuo.casting import cast_voices, chapters_of, choose_narrator, conversations
    from huashuo.library import has_library

    built = build(book, language, read_notes)
    lang = built.script.header["language"]
    cast = default_cast(lang)
    report = None
    chosen = narrator is not None           # by the user: casting works around it
    narrator = narrator or cast["narrator"]["voice"]
    reason = "your choice" if chosen else "default"
    if wd is not None and llm is not None:
        attribution, report = run_llm_stage(built.script, lang, wd, record if record is not None else {}, llm,
                                            narrator)
        if attribution is not None:
            kept = _carry_over(attribution, previous, previous_cast, lang) if previous is not None else 0
            if kept and report is not None:
                report.kept, report.review = kept, attribution.review
                if not any(b.get("type") == "dialogue" and "conf" not in b for b in attribution.blocks):
                    report.stopped = None
            built.script.blocks = attribution.blocks
            characters = attribution.characters
            if not chosen:
                narrator, reason = choose_narrator(characters, lang)
            if has_library(lang):
                # Without fresh suggestions, the previous voices are the suggestions, so a
                # re-import does not reshuffle the main characters.
                suggested = attribution.suggested_voices or {
                    name: c["voice"] for name, c in (previous_cast or {}).get("characters", {}).items() if c.get("voice")}
                voices = cast_voices(characters, narrator, lang, conversations(attribution.blocks), fixed_voices,
                                     suggested=suggested, chapters=chapters_of(attribution.blocks))
            else:
                # No designed voices for this language yet (English in stage 1, requirements
                # Q19): the narrator reads everyone, unless the user named a voice.
                voices = {name: (fixed_voices or {}).get(name, narrator) for name in characters}
            cast["characters"] = {name: {**entry, "voice": voices[name]} for name, entry in characters.items()}
    cast["narrator"]["voice"] = narrator
    return MachineOutput(built.text, built.script, cast, built.chapters, built.skipped, report, reason)


def _user_voice_choices(wd: Workdir, language: str) -> tuple[str | None, dict[str, str]]:
    """The narrator and character voices the user changed by hand in cast.json (a
    narrator left as the machine chose it is not a choice, so it may change)."""
    if not wd.cast.is_file():
        return None, {}
    current = load_cast(wd.cast, language)
    base_cast = read_json(wd.cast_base) or {}
    base = base_cast.get("characters", {})
    fixed = {name: c["voice"] for name, c in current.get("characters", {}).items()
             if c.get("voice") and base.get(name, {}).get("voice") != c["voice"]}
    narrator = current["narrator"]["voice"]
    edited = not base_cast or base_cast.get("narrator", {}).get("voice") != narrator
    return (narrator if edited else None), fixed


def resolve_narrator(choice: str | None, language: str) -> str | None:
    """--narrator: female / male (the library's narrator voice of that gender), a voice
    reference, or auto / None (choose per book)."""
    from huashuo.library import LibraryError, get, narrators

    if choice in (None, "", "auto"):
        return None
    if choice in ("female", "male"):
        voices = [v.ref for v in narrators(language) if v.gender == choice]
        if not voices:
            raise PipelineError(f"--narrator {choice}: no {choice} narrator voice for {language} books; "
                                f"name a voice instead (huashuo voices --library)")
        return voices[0]
    try:
        return get(choice).ref
    except LibraryError as exc:
        raise PipelineError(f"--narrator: {exc}") from None


def write_review(wd: Workdir, review: list[dict]) -> Path | None:
    """Quotes whose speaker is unknown or uncertain, for the user to check first (SCR-11)."""
    path = wd.root / "review.txt"
    if not review:
        path.unlink(missing_ok=True)
        return None
    lines = ["# Quotes with an unknown or uncertain speaker; fix `speaker` for the id in script.huaben.jsonl.", ""]
    for item in review:
        conf = "" if item["conf"] is None else f" ({item['conf']:.2f})"
        lines.append(f"{item['id']}\t{item['speaker']}{conf}\t{item['text']}")
    write_text_atomic(path, "\n".join(lines) + "\n")
    return path


def _old_generated(path: Path | None) -> bool:
    """A plain cover drawn by versions before the templates: 1400 px, dark slate. It was
    saved as cover.jpg, where it would be mistaken for the book's own."""
    if path is None or path.suffix != ".jpg":
        return False
    try:
        from PIL import Image
        with Image.open(path) as image:
            pixel = image.convert("RGB").getpixel((20, 20))
            return image.size == (1400, 1400) and all(abs(a - b) <= 4 for a, b in zip(pixel, (38, 42, 48)))
    except OSError:
        return False


def book_cover(wd: Workdir, title: str) -> Path:
    """The cover to package: the user's (--cover, or an image put into the work directory
    by hand) or the book's own, else one made from the templates (cover.py)."""
    from huashuo.cover import generated_cover

    found = wd.find_cover()
    if found is not None and not _old_generated(found):
        return found
    return generated_cover(wd.root, wd.state, title)


def _place_cover(wd: Workdir, book, cover: Path | None, previous: dict) -> tuple[Path | None, str | None]:
    """The cover to use, and who chose it. A cover the user supplied (--cover, recorded in
    state/ingest.json) is never replaced by the book's own on a later import."""
    existing = wd.find_cover()
    if _old_generated(existing):
        existing.unlink()                    # replaced by a template cover at packaging
        existing = None
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
                title: str | None = None, author: str | None = None,
                narrator: str | None = None, voices: str | None = None) -> ImportResult:
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
    voices = voices or previous.get("voices") or "multi"
    if voices not in ("single", "multi"):
        raise PipelineError(f"voices must be single or multi, not {voices!r}")
    if voices == "single" and llm is not None:
        # One voice needs no speakers: no calls, no question; answers already in the cache
        # are still applied, so switching back to --multi-voice costs nothing for them.
        llm = LLMOptions(enabled=False, model=llm.model)
    narrator_now = narrator                            # given on this run: wins over cast.json too
    narrator = narrator if narrator is not None else previous.get("narrator")
    book = read_book(source, encoding)
    # The chosen title and author are also what structure detection looks for (a title
    # line naming the opening section, an author line to skip).
    book.title, book.author = title or book.title, author or book.author
    lang = language or book.language or record.get("language") or "zh"
    flag_voice = resolve_narrator(narrator, lang)
    user_narrator, fixed = _user_voice_choices(wd, lang)
    chosen = flag_voice if narrator_now not in (None, "auto") else (user_narrator or flag_voice)
    previous = read_script(wd.script_base) if wd.script_base.is_file() else None
    machine = machine_output(book, language, read_notes, wd, record, llm if llm is not None else LLMOptions(),
                             chosen, fixed, previous, read_json(wd.cast_base))
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
    if not wd.pron.exists():
        write_text_atomic(wd.pron, pron.TEMPLATE)

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
    if narrator_now is not None:
        # An explicit --narrator replaces whatever cast.json had; auto hands it back to the rule.
        cast["narrator"]["voice"] = machine.cast["narrator"]["voice"]
    write_json_atomic(wd.cast, cast)
    write_json_atomic(wd.cast_base, machine.cast)

    if voices == "single":
        write_review(wd, [])                            # speakers do not matter with one voice
    elif machine.llm is not None and machine.llm.mode != "none":
        write_review(wd, machine.llm.review)
    write_json_atomic(wd.ingest_record, {"huashuo": __version__, "imported": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "source": str(source.resolve()), "encoding": book.encoding,
                                         "language": header["language"], "cover_from": cover_from,
                                         "llm_model": record.get("llm_model"),
                                         "llm_consent": record.get("llm_consent", []),
                                         "options": {"encoding": encoding, "language": language,
                                                     "read_notes": read_notes, "title": title,
                                                     "author": author, "voices": voices,
                                                     "narrator": None if narrator == "auto" else narrator}})
    if narrator_now not in (None, "auto"):
        reason = "--narrator"
    elif user_narrator is not None or cast["narrator"]["voice"] != machine.cast["narrator"]["voice"]:
        reason = "your choice in cast.json"
    elif flag_voice is not None:
        reason = "--narrator, remembered"
    else:
        reason = machine.narrator_reason
    return ImportResult(wd, merged, machine.text, machine.chapters, machine.skipped, book.encoding,
                        result.report + cast_report, machine.llm, cast["narrator"]["voice"], reason, voices)


@dataclass
class Project:
    workdir: Workdir
    script: Script
    text: str
    cast: dict

    @property
    def language(self) -> str:
        return self.script.header.get("language", "zh")

    @property
    def single_voice(self) -> bool:
        """--single-voice, remembered by the import: the narrator reads everything."""
        return (read_json(self.workdir.ingest_record) or {}).get("options", {}).get("voices") == "single"


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
              chapters: set[int] | None = None, emotions: bool = True, opening: bool = True,
              closing: bool = True, credit: bool = False, simplify: bool | None = None) -> Plan:
    """The units to synthesize; optionally only the first `sample_chars` characters of
    reading, or only some chapters (1-based, as listed by `huashuo import`)."""
    full = plan(project.script, project.cast, project.language, max_chars, read_titles, voice, emotions,
                project.single_voice)
    _announce(full, project, voice or project.cast["narrator"]["voice"], opening, closing, credit)
    readings = pron.load(project.workdir.pron)
    convert = speaks_simplified(project) if simplify is None else (simplify and project.language == "zh")
    if convert:
        readings = readings.mapped(to_simplified)            # entries match in either script
    for unit in full.units:
        spoken = readings.apply(to_simplified(unit.text) if convert else unit.text)
        if spoken != unit.text:
            unit.reference, unit.text = unit.text, spoken
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


# 「作者」 rather than 「著」: plainer to hear, and the recognizer writes 著 as 住 (M5 check).
def speaks_simplified(project: Project) -> bool:
    """A Chinese book in Traditional characters is read from a Simplified conversion
    (chinese.py); its own text stays as it is."""
    return project.language == "zh" and is_traditional(project.text)


OPENING = {"zh": "《{title}》，作者{author}。", "en": "{title}, by {author}."}
OPENING_NO_AUTHOR = {"zh": "《{title}》。", "en": "{title}."}
CLOSING = {"zh": "全书完。", "en": "The End."}
CREDIT = {"zh": "本有声书由话说 Huashuo 生成。", "en": "This audiobook was made with Huashuo."}


def _announce(p: Plan, project: Project, narrator: str, opening: bool, closing: bool, credit: bool) -> None:
    """The opening (title and author) and the closing (POST-6), read by the narrator. The
    opening belongs to the first chapter and the closing to the last; neither is a
    chapter of its own."""
    from huashuo.units import END, TITLE, Unit

    if not p.units:
        return
    lang = project.language if project.language in OPENING else "en"
    header = project.script.header
    title, author = (header.get("title") or "").strip(), (header.get("author") or "").strip()
    if opening and title:
        # The opening already says the title: a first chapter title that only repeats it
        # (an opening section named by the book's title line) is not read again.
        squash = lambda s: re.sub(r"[\s《》「」“”\"'·:：.。]", "", s).lower()
        if p.units[0].kind == "title" and squash(p.units[0].text) == squash(title):
            p.units.pop(0)
            for chapter in p.chapters[1:]:
                chapter.first_unit -= 1
        text = (OPENING if author else OPENING_NO_AUTHOR)[lang].format(title=title, author=author)
        p.units.insert(0, Unit("title", text, narrator, None, ["opening"], 0, after=TITLE))
        for chapter in p.chapters[1:]:
            chapter.first_unit += 1
    words = ([CLOSING[lang]] if closing else []) + ([CREDIT[lang]] if credit else [])
    if words:
        p.units[-1].after = "chapter_end"
        p.units.append(Unit("heading", "".join(words) if lang == "zh" else " ".join(words), narrator, None,
                            ["closing"], p.units[-1].chapter, after=END))


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
