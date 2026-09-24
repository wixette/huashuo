"""`huashuo` command line (CLI-1 … CLI-7).

    huashuo BOOK                 import, check, synthesize and package in one go
    huashuo import BOOK          build the work directory; list chapters and skipped text
    huashuo check BOOK           verify the script against text.txt (docs/script-ir.md §5)
    huashuo synth BOOK           synthesize (resumable); --sample / --chapters for auditions
    huashuo package BOOK         write the M4B from what has been synthesized
    huashuo redo BOOK --at 1:28  re-synthesize what plays at a time in the M4B, then repackage
    huashuo audition BOOK        one line per character in its cast voice, as <book>.audition.m4b
    huashuo clean BOOK           delete the synthesized audio (the book can be rebuilt from the rest)
    huashuo voices               list the engine's preset voices (--library: every castable voice)

BOOK is the source file (.txt / .epub); its work directory defaults to <BOOK>.huashuo/.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from huashuo import __version__

COMMANDS = ("make", "import", "check", "synth", "package", "redo", "audition", "clean", "voices")
log = logging.getLogger("huashuo")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="huashuo", description="话说 Huashuo: novel in, M4B out.",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("--version", action="version", version=f"huashuo {__version__}")
    sub = parser.add_subparsers(dest="command")

    def book_command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("book", type=Path, help="source .txt or .epub")
        p.add_argument("--workdir", type=Path, help="work directory (default: <book>.huashuo/)")
        return p

    def import_options(p):
        p.add_argument("--encoding", help="TXT encoding when detection fails, e.g. big5")
        p.add_argument("--language", choices=["zh", "en"], help="override language detection")
        p.add_argument("--title", help="book title, instead of the book's own (remembered for this book)")
        p.add_argument("--author", help="author, instead of the book's own (remembered for this book)")
        p.add_argument("--cover", type=Path, help="cover image to use instead of the book's own")
        p.add_argument("--read-notes", action=argparse.BooleanOptionalAction, default=None,
                       help="read editorial annotations (注釋 sections); skipped by default, "
                            "and the choice is remembered for this book")
        g = p.add_argument_group("speaker attribution (LLM)")
        g.add_argument("--no-llm", action="store_true",
                       help="make no LLM calls; earlier answers are still reused from the cache")
        g.add_argument("--llm-model", help="default: $HUASHUO_LLM_MODEL or gpt-6-sol")
        g.add_argument("--llm-base-url", help="OpenAI-compatible endpoint (default: $HUASHUO_LLM_BASE_URL or OpenAI)")
        g.add_argument("--max-llm-cost", type=float, help="stop before spending more than this many USD (default 5)")
        g.add_argument("--llm-concurrency", type=int, help="speaker-attribution calls in flight at once (default 6)")
        g.add_argument("--yes", action="store_true",
                       help="agree to send the book's text to the LLM endpoint without asking")

    def synth_options(p, asr: bool = True):
        p.add_argument("--voice", help="narrator voice for this run, e.g. preset:vivian "
                                       "(to keep it, edit cast.json)")
        p.add_argument("--model", help="Qwen3-TTS CustomVoice model (default: 1.7B 8-bit)")
        if asr:
            p.add_argument("--no-asr", action="store_true",
                           help="skip the speech-recognition check of each unit (~5-10%% faster)")
        p.add_argument("--titles", action=argparse.BooleanOptionalAction, default=None,
                       help="read chapter titles aloud (default: yes)")
        p.add_argument("--emotions", action=argparse.BooleanOptionalAction, default=None,
                       help="read dialogue with the emotion hints in the script (default: yes)")
        p.add_argument("--opening", action=argparse.BooleanOptionalAction, default=None,
                       help="begin with the title and author (default: yes)")
        p.add_argument("--closing", action=argparse.BooleanOptionalAction, default=None,
                       help="end with 「全书完」 / \"The End\" (default: yes)")
        p.add_argument("--credit", action=argparse.BooleanOptionalAction, default=None,
                       help="add a line saying the audiobook was made with Huashuo (default: no)")
        p.add_argument("--sample", type=int, nargs="?", const=600, metavar="CHARS",
                       help="only the first CHARS characters (default 600); packaged as <book>.sample.m4b")
        p.add_argument("--chapters", help="only these chapters, e.g. 1,3-5 (numbers from `import`); "
                                          "packaged as <book>.chapters-1_3-5.m4b")
        p.add_argument("--engine", default="qwen3", help=argparse.SUPPRESS)

    def package_options(p):
        p.add_argument("-o", "--output", type=Path, help="output M4B (default: next to the book)")
        p.add_argument("--bitrate", default="64k", help="AAC bitrate (default 64k)")
        p.add_argument("--loudness", type=float, metavar="LUFS",
                       help="loudness target (default -18; remembered for this book)")
        p.add_argument("--pause", action="append", metavar="KIND=SECONDS",
                       help="pause after a kind of boundary: " + ", ".join(
                           f"{k} ({v:g})" for k, v in _default_pauses().items())
                            + "; repeatable, remembered for this book")

    p = book_command("make", "import, check, synthesize and package (the default)")
    import_options(p), synth_options(p), package_options(p)
    p.add_argument("--dry-run", action="store_true", help="import and show the plan, synthesize nothing")
    p = book_command("import", "build or refresh the work directory")
    import_options(p)
    book_command("check", "check the script's invariants")
    p = book_command("synth", "synthesize units into the cache")
    synth_options(p)
    p = book_command("package", "encode the M4B from cached units")
    synth_options(p, asr=False), package_options(p)
    p = book_command("redo", "re-synthesize the unit playing at a time in the M4B, with new seeds")
    p.add_argument("--at", action="append", required=True, metavar="TIME",
                   help="time in the M4B, e.g. 1:28, 1:02:03 or 88.5; repeatable")
    synth_options(p), package_options(p)
    p = book_command("audition", "hear each character's voice before synthesizing the book (CAST-11)")
    p.add_argument("--character", action="append", metavar="NAME", help="only these characters; repeatable")
    p.add_argument("--library", action="store_true",
                   help="instead, one probe sentence in every voice casting can choose from")
    p.add_argument("--model", help="Qwen3-TTS CustomVoice model (default: 1.7B 8-bit)")
    p.add_argument("-o", "--output", type=Path, help="output M4B (default: <book>.audition.m4b)")
    p.add_argument("--engine", default="qwen3", help=argparse.SUPPRESS)
    p = book_command("clean", "delete the synthesized audio in the work directory (CLI-6)")
    p.add_argument("--yes", action="store_true", help="do not ask")
    p = sub.add_parser("voices", help="list preset voices")
    p.add_argument("--model", help="CustomVoice model")
    p.add_argument("--library", action="store_true",
                   help="list every voice casting can choose from (library and presets), without loading a model")
    p.add_argument("--language", choices=["zh", "en"], default="zh", help="with --library (default zh)")
    return parser


def _parse_chapters(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    numbers: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        numbers.update(range(int(a), int(b or a) + 1))
    return numbers


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _mb(n: float) -> str:
    return f"{n / 1e6:,.0f} MB" if n < 1e9 else f"{n / 1e9:.1f} GB"


def _setup_logging(wd) -> None:
    """A detailed log per run in <workdir>/logs/ (CLI-7). Attached to this work directory:
    a handler left by an earlier run in the same process (tests, `make`) is kept only if
    it already writes there."""
    for handler in list(log.handlers):
        if Path(getattr(handler, "baseFilename", "")).parent == wd.logs.resolve():
            return
        log.removeHandler(handler)
        handler.close()
    wd.logs.mkdir(parents=True, exist_ok=True)
    path = wd.logs / f"run-{time.strftime('%Y%m%d-%H%M%S')}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("huashuo %s: %s", __version__, " ".join(sys.argv))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "make")
    args = _parser().parse_args(argv)
    if args.command is None:
        _parser().print_help()
        return 1
    try:
        return {"make": cmd_make, "import": cmd_import, "check": cmd_check, "synth": cmd_synth,
                "package": cmd_package, "redo": cmd_redo, "audition": cmd_audition, "clean": cmd_clean,
                "voices": cmd_voices}[args.command](args)
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to continue where it stopped")
        return 130
    except Exception as exc:  # user-facing errors carry their own advice (CLI-7)
        from huashuo.attribution import LLMError
        from huashuo.cast import CastError
        from huashuo.engines import EngineError
        from huashuo.huaben import HuabenError
        from huashuo.ingest import IngestError
        from huashuo.library import LibraryError
        from huashuo.m4b import PackageError
        from huashuo.pipeline import PipelineError
        if isinstance(exc, (CastError, EngineError, HuabenError, IngestError, LibraryError, LLMError, PackageError,
                            PipelineError)):
            log.error("%s", exc)
            print(f"error: {exc}", file=sys.stderr)
            return 2
        log.exception("unexpected error")
        raise


# --------------------------------------------------------------------------------------


def _workdir(args):
    from huashuo.workdir import Workdir
    return Workdir.for_input(args.book, args.workdir)


def cmd_import(args, quiet: bool = False):
    from huashuo.pipeline import import_book

    if not args.book.is_file():
        print(f"error: {args.book} not found", file=sys.stderr)
        return 2
    from huashuo.pipeline import LLMOptions

    wd = _workdir(args)
    _setup_logging(wd)
    llm = LLMOptions(enabled=not args.no_llm, model=args.llm_model, base_url=args.llm_base_url,
                     max_cost=args.max_llm_cost, assume_yes=args.yes, concurrency=args.llm_concurrency,
                     confirm=_ask if sys.stdin.isatty() else None, progress=LLMProgress())
    result = import_book(args.book, wd, args.encoding, args.language, args.cover, args.read_notes, llm,
                         title=args.title, author=args.author)
    h = result.script.header
    chars = sum(len(b.get("text", "")) for b in result.script.blocks if b.get("type") != "skip")
    print(f"《{h['title']}》 {h.get('author') or '(author unknown)'}  [{h['language']}"
          f"{', ' + result.encoding if result.encoding else ''}]  {chars:,} characters")
    print(f"work directory: {wd.root}")
    for line in result.merge_report:
        print(f"  merge: {line}")
    _print_llm(result.llm, wd)
    _print_pron(wd, result.script)
    if not quiet:
        from huashuo.pipeline import load_project
        _print_structure(load_project(wd))
    return 0


def _print_pron(wd, script) -> None:
    """How the pronunciation dictionary matches this book, so a typo is noticed."""
    from huashuo import pron
    from huashuo.huaben import spoken_text

    readings = pron.load(wd.pron)
    for problem in readings.problems:
        print(f"warning: {problem}")
    if not readings.entries:
        return
    counts = readings.matches(spoken_text(b) for b in script.blocks if b.get("type") != "skip")
    found = [f"{word} ×{n}" for word, n in counts.items() if n]
    missing = [word for word, n in counts.items() if not n]
    print(f"pron.txt: {len(readings.entries)} entries" + (f"; {', '.join(found)}" if found else "")
          + (f"; not in the book: {', '.join(missing)}" if missing else ""))


class LLMProgress:
    """Where speaker attribution is (it can take several minutes on a long book): one
    updating line on a terminal, a line per 10% of each pass otherwise."""

    STAGES = {"cast": "pass 1/2, cast", "speakers": "pass 2/2, speakers", "retry": "re-asking missed quotes"}

    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()
        self.stage = None
        self.mark = 0
        self.started = None              # set by the first report: after any consent question

    def __call__(self, stage: str, done: int, total: int, usage) -> None:
        if self.started is None:
            self.started = time.time()
        if stage != self.stage:
            if self.tty and self.stage is not None:
                sys.stdout.write("\n")
            self.stage, self.mark = stage, 0
        line = (f"  speaker attribution, {self.STAGES.get(stage, stage)}: {done}/{total} "
                f"({100 * done // max(total, 1)}%), {usage.requests} requests, ${usage.cost:.3f}, "
                f"{_hms(time.time() - self.started)}")
        if self.tty:
            sys.stdout.write("\r\033[K" + line)
            if done == total:
                sys.stdout.write("\n")
                self.stage = None
            sys.stdout.flush()
        elif done == total or 100 * done // max(total, 1) >= self.mark + 10:
            self.mark = 100 * done // max(total, 1)
            print(line, flush=True)


def _ask(message: str) -> bool:
    print(message)
    try:
        return input("[y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _print_llm(report, wd) -> None:
    """What speaker attribution did, cost and what needs a look (LLM-3, SCR-11)."""
    if report is None:
        return
    if report.notice:
        print(f"note: {report.notice}")
    if report.mode == "none":
        return
    u = report.usage
    if report.mode == "calls":
        print(f"speakers ({report.model}): {u.requests} requests, {u.cached} from cache, "
              f"{u.input_tokens:,}+{u.output_tokens:,} tokens, ${u.cost:.3f} (estimated up to ${report.estimate:.2f})")
    else:
        print(f"speakers: {u.cached} answers from the cache ({report.model}); no LLM calls made")
    log.info("llm: mode=%s model=%s requests=%s cached=%s cost=%.4f", report.mode, report.model,
             u.requests, u.cached, u.cost)
    if report.stopped:
        print(f"warning: speaker attribution stopped early: {report.stopped}")
        log.warning("llm stopped: %s", report.stopped)
    if report.suggestions_failed:
        print(f"note: no voice suggestions from the LLM ({report.suggestions_failed}); voices were cast by "
              f"gender and age alone")
        log.warning("voice suggestions failed: %s", report.suggestions_failed)
    if report.review:
        print(f"{len(report.review)} quotes need a look (unknown or uncertain speaker): {wd.root / 'review.txt'}")


def _print_structure(project) -> None:
    """Chapters as `--chapters` numbers them, with sizes, and everything that will not be
    read (TXT-8). Numbers come from the plan, which drops chapters with nothing to read and
    folds a volume title into its first chapter."""
    from huashuo.units import plan

    p = plan(project.script, project.cast, project.language)
    sizes = [0] * len(p.chapters)
    for unit in p.units:
        if unit.kind == "body":
            sizes[unit.chapter] += len(unit.text)
    print(f"\n{len(p.chapters)} chapters:")
    for number, (chapter, chars) in enumerate(zip(p.chapters, sizes), 1):
        print(f"  {number:4d}  {chapter.title[:44]:<44} {chars:>8,} 字")
    skipped = [b for b in project.script.blocks if b.get("type") == "skip"]
    if skipped:
        print(f"\n{len(skipped)} paragraphs kept but not read:")
        for block in skipped[:15]:
            print(f"  [{block.get('reason')}] {block.get('text', '')[:50]}")
        if len(skipped) > 15:
            print(f"  … and {len(skipped) - 15} more (type \"skip\" in the script)")


def cmd_check(args) -> int:
    from huashuo.pipeline import check_project, load_project

    wd = _workdir(args)
    _setup_logging(wd)
    problems = check_project(load_project(wd))
    for problem in problems:
        print(problem)
    print("script OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def _engine(args):
    from huashuo.engines import create_engine

    options = {"model": args.model} if getattr(args, "model", None) else {}
    return create_engine(args.engine, **options)


# Options that change which units exist. synth records them in state/run.json and package
# and redo reuse them, so a flag need not be repeated to find the same units again.
_RUN_OPTIONS = {"voice": None, "model": None, "titles": True, "emotions": True, "opening": True, "closing": True,
                "credit": False}


def _resolve_run_options(args, wd, save: bool) -> None:
    from huashuo.workdir import read_json, write_json_atomic

    stored = read_json(wd.run_options, {}) or {}
    for name, default in _RUN_OPTIONS.items():
        if getattr(args, name, None) is None:
            setattr(args, name, stored.get(name, default))
    if save:
        write_json_atomic(wd.run_options, {**stored, **{name: getattr(args, name) for name in _RUN_OPTIONS}})


def _default_pauses() -> dict[str, float]:
    from huashuo.post import PAUSES
    return PAUSES


def _post_options(args, wd) -> tuple[float, dict[str, float]]:
    """Loudness target and pauses (POST-1, POST-3): given ones are merged into those
    remembered in state/run.json. They only change packaging, never the synthesized units."""
    from huashuo.audio import TARGET_LUFS
    from huashuo.workdir import read_json, write_json_atomic

    stored = read_json(wd.run_options, {}) or {}
    loudness = stored.get("loudness", TARGET_LUFS)
    pauses = dict(stored.get("pauses", {}))
    given = getattr(args, "loudness", None)
    if given is not None:
        if not -30 <= given <= -10:
            raise SystemExit(f"--loudness {given:g}: choose a target between -30 and -10 LUFS")
        loudness = given
    for item in getattr(args, "pause", None) or []:
        kind, _, value = item.partition("=")
        kind = kind.strip()
        if kind not in _default_pauses():
            raise SystemExit(f"--pause {item}: unknown kind {kind!r}; choose from {', '.join(_default_pauses())}")
        try:
            seconds = float(value)
        except ValueError:
            raise SystemExit(f"--pause {item}: expected KIND=SECONDS, e.g. paragraph=0.8") from None
        if not 0 <= seconds <= 10:
            raise SystemExit(f"--pause {item}: choose between 0 and 10 seconds")
        pauses[kind] = seconds
    if given is not None or getattr(args, "pause", None):
        write_json_atomic(wd.run_options, {**stored, "loudness": loudness, "pauses": pauses})
    return loudness, pauses


def _prepare(args, save_options: bool = False):
    """Load, check and plan; shared by synth, package, redo and make."""
    from huashuo.pipeline import check_project, load_project, make_plan

    wd = _workdir(args)
    _resolve_run_options(args, wd, save_options)
    project = load_project(wd)
    problems = check_project(project)
    if problems:
        for problem in problems[:20]:
            print(problem, file=sys.stderr)
        raise SystemExit("the script has problems (above); fix them or re-run `huashuo import`")
    plan = make_plan(project, read_titles=args.titles, voice=args.voice, emotions=args.emotions,
                     opening=args.opening, closing=args.closing, credit=args.credit,
                     sample_chars=args.sample, chapters=_parse_chapters(args.chapters))
    if not plan.units:
        raise SystemExit("nothing to read")
    return wd, project, plan


def cmd_synth(args) -> int:
    from huashuo.pipeline import estimate
    from huashuo.synth import synthesize

    wd, project, plan = _prepare(args, save_options=True)
    _setup_logging(wd)
    est = estimate(plan, project.language)
    print(f"{len(plan.units)} units, {plan.chars:,} characters: about {_hms(est.audio_seconds)} of audio, "
          f"roughly {_hms(est.synth_seconds)} to synthesize")
    engine = _engine(args)
    asr = None
    if not args.no_asr and args.engine != "fake":
        from huashuo.asr import AsrChecker
        asr = AsrChecker()
    started = time.time()
    try:
        stats = synthesize(plan.units, engine, wd, project.language, asr=asr)
    finally:
        engine.close()
    print(f"synthesized {stats.generated}, reused {stats.cached}, retried {stats.retried} "
          f"in {_hms(time.time() - started)}; {_hms(stats.audio_seconds)} of audio")
    log.info("synth: %d generated, %d cached, %d retried", stats.generated, stats.cached, stats.retried)
    if stats.warnings:
        print(f"{len(stats.warnings)} unit(s) kept despite failing checks:")
        for w in stats.warnings[:20]:
            print(f"  {w['problem']} | {w['text'][:30]}…")
            log.warning("kept %s: %s | %s", w["key"], w["problem"], w["text"])
    return 0


def _partial_suffix(args) -> str:
    """Auditions never overwrite the finished book: .sample / .chapters-3-8 in the name."""
    if args.sample is not None:
        return ".sample"
    if args.chapters:
        return ".chapters-" + args.chapters.replace(",", "_").replace(" ", "")
    return ""


def cmd_package(args) -> int:
    from huashuo.m4b import BookInfo, make_cover, probe, write_m4b
    from huashuo.post import layout, stream
    from huashuo.synth import cached_ok, unit_keys

    wd, project, plan = _prepare(args)
    _setup_logging(wd)
    engine = _engine(args)
    keys = unit_keys(plan.units, engine, project.language)
    missing = sum(1 for k in keys if cached_ok(wd, k) is None)
    if missing:
        raise SystemExit(f"{missing} of {len(keys)} units are not synthesized yet; run `huashuo synth` "
                         f"with the same options first")
    header = project.script.header
    default_name = args.book.stem + _partial_suffix(args) + ".m4b"
    output = args.output or args.book.with_name(default_name)

    cover = wd.find_cover()
    if cover is None:
        cover = make_cover(wd.cover(".jpg"), header.get("title", ""), header.get("author", ""))
    info = BookInfo(title=header.get("title", args.book.stem), author=header.get("author", ""),
                    language=project.language, description=header.get("meta", {}).get("description", ""),
                    date=header.get("meta", {}).get("date", ""))
    loudness, pauses = _post_options(args, wd)
    timeline = layout(plan, keys, wd, loudness, pauses)
    print(f"encoding {output.name}: {_hms(timeline.seconds)}, {len(timeline.chapters)} chapters …")
    write_m4b(output, stream(timeline, wd), timeline.sample_rate, info, timeline.chapters, cover,
              args.bitrate)
    result = probe(output)
    seconds = float(result["format"]["duration"])
    print(f"wrote {output} ({_mb(output.stat().st_size)}, {_hms(seconds)}, "
          f"{len(result.get('chapters', []))} chapters)")
    return 0


def parse_time(value: str) -> float:
    """「1:28」, 「1:02:03」, 「88」 or 「88.5」 -> seconds."""
    seconds = 0.0
    for part in value.strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def cmd_redo(args) -> int:
    """Map each time in the M4B to its unit, re-roll those units, synthesize, repackage."""
    from huashuo.post import layout, unit_at
    from huashuo.synth import cached_ok, reroll, unit_keys

    wd, project, plan = _prepare(args)
    _setup_logging(wd)
    engine = _engine(args)
    keys = unit_keys(plan.units, engine, project.language)
    if any(cached_ok(wd, k) is None for k in keys):
        raise SystemExit("some units are not synthesized yet; run `huashuo synth` (or make) with the "
                         "same options first, so times refer to a finished M4B")
    loudness, pauses = _post_options(args, wd)
    timeline = layout(plan, keys, wd, loudness, pauses)
    chosen: dict[int, str] = {}
    for value in args.at:
        index = unit_at(timeline, parse_time(value))
        if index is None:
            raise SystemExit(f"{value} is outside the book ({_hms(timeline.seconds)})")
        chosen.setdefault(index, value)
    for index, value in chosen.items():
        item, unit = timeline.placed[index], plan.units[index]
        start = item.start / timeline.sample_rate
        end = start + (item.trim[1] - item.trim[0]) / timeline.sample_rate
        times = reroll(wd, keys[index])
        print(f"redo {value}: {_hms(start)}-{_hms(end)} 「{unit.text[:30]}…」 (redo #{times})")
        log.info("redo %s -> unit %s (redo #%d)", value, keys[index], times)
    status = cmd_synth(args)
    return status or cmd_package(args)


def cmd_make(args) -> int:
    from huashuo.pipeline import estimate

    status = cmd_import(args, quiet=not args.dry_run)
    if status:
        return status
    wd, project, plan = _prepare(args)
    est = estimate(plan, project.language)
    print(f"\nplan: {len(plan.units)} units, {plan.chars:,} characters, {len(plan.chapters)} chapters")
    print(f"  audio        about {_hms(est.audio_seconds)}")
    print(f"  synthesis    roughly {_hms(est.synth_seconds)} on an M1 (1.7B model)")
    print(f"  disk         cache {_mb(est.cache_bytes)}, M4B {_mb(est.m4b_bytes)}")
    if args.dry_run:
        return 0
    status = cmd_synth(args)
    return status or cmd_package(args)


PROBE = {"zh": "这条路我走过很多次，从来没有迷过路。你若信得过我，就跟紧些，天黑之前我们能赶到渡口。",
         "en": "I have walked this road many times and never lost my way. Stay close, and we will reach the ferry before dark."}
AUDITION_MAX_CHARS = 60


def _audition_line(project, name: str) -> str:
    """The character's longest line that fits in a short audition, else a probe sentence."""
    from huashuo.huaben import spoken_text

    lines = [spoken_text(b).strip() for b in project.script.blocks
             if b.get("type") == "dialogue" and b.get("speaker") == name]
    fitting = [t for t in lines if t and len(t) <= AUDITION_MAX_CHARS]
    if fitting:
        return max(fitting, key=len)
    if lines:
        return min(lines, key=len)[:AUDITION_MAX_CHARS]
    return PROBE.get(project.language, PROBE["zh"])


def cmd_audition(args) -> int:
    """One M4B with a chapter per character (or per library voice), from the unit cache."""
    from huashuo.library import castable
    from huashuo.m4b import BookInfo, make_cover, probe, write_m4b
    from huashuo.pipeline import load_project
    from huashuo.post import layout, stream
    from huashuo.synth import synthesize, unit_keys
    from huashuo.units import END, PARAGRAPH, Chapter, Plan, Unit

    wd = _workdir(args)
    _setup_logging(wd)
    project = load_project(wd)
    language, cast = project.language, project.cast
    narrator = cast["narrator"]["voice"]
    items: list[tuple[str, str, str]] = []            # (chapter title, text, voice)
    if args.library:
        for voice in castable(language):
            items.append((f"{voice.ref} {voice.role}".strip(), PROBE.get(language, PROBE["zh"]), voice.ref))
    else:
        characters = cast.get("characters", {})
        names = args.character or sorted(characters, key=lambda n: (-int(characters[n].get("lines") or 0), n))
        unknown = [n for n in names if n not in characters]
        if unknown:
            raise SystemExit(f"not in cast.json: {', '.join(unknown)}")
        if not names:
            raise SystemExit("cast.json has no characters yet; run `huashuo import` with an LLM configured")
        for name in names:
            voice = characters[name].get("voice") or narrator
            items.append((f"{name}（{voice}）", _audition_line(project, name), voice))

    units = [Unit("body", text, voice, None, [f"audition{i}"], i, after=PARAGRAPH)
             for i, (_, text, voice) in enumerate(items)]
    units[-1].after = END
    plan = Plan(units=units, chapters=[Chapter(title, f"audition{i}", i) for i, (title, _, _) in enumerate(items)])
    engine = _engine(args)
    try:
        synthesize(plan.units, engine, wd, language, asr=None)
    finally:
        engine.close()
    keys = unit_keys(plan.units, engine, language)
    loudness, pauses = _post_options(args, wd)
    timeline = layout(plan, keys, wd, loudness, pauses)
    output = args.output or args.book.with_name(args.book.stem + ".audition.m4b")
    header = project.script.header
    cover = wd.find_cover() or make_cover(wd.cover(".jpg"), header.get("title", ""), header.get("author", ""))
    info = BookInfo(title=f"{header.get('title', args.book.stem)}（试听 audition）", author=header.get("author", ""),
                    language=language)
    write_m4b(output, stream(timeline, wd), timeline.sample_rate, info, timeline.chapters, cover)
    for title, text, _ in items:
        print(f"  {title}: {text[:40]}")
    print(f"wrote {output} ({len(probe(output).get('chapters', []))} chapters, {_hms(timeline.seconds)})")
    return 0


def cmd_clean(args) -> int:
    """Delete cache/ (the synthesized units). Everything needed to rebuild the same book is
    kept: script, cast, pron.txt, LLM answers and the redo record (state/rerolls.json)."""
    import shutil

    wd = _workdir(args)
    cache = wd.units.parent
    files = [f for f in cache.rglob("*") if f.is_file()] if cache.is_dir() else []
    if not files:
        print(f"nothing to clean in {wd.root}")
        return 0
    size = sum(f.stat().st_size for f in files)
    message = (f"delete {len(files):,} files ({_mb(size)}) of synthesized audio in {cache}? The script, cast "
               f"and LLM answers are kept; synthesizing the book again takes as long as the first time.")
    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit(message.split("?")[0] + "? run again with --yes")
        if not _ask(message):
            return 1
    shutil.rmtree(cache)
    print(f"deleted {_mb(size)} of synthesized audio")
    return 0


def cmd_voices(args) -> int:
    if args.library:
        from huashuo.library import castable

        for voice in castable(args.language):
            print(f"{voice.ref:<34} {voice.gender:<7} {voice.age:<12} {voice.role}")
        return 0
    from huashuo.engines.qwen3 import DIALECT_PRESETS, Qwen3Engine

    engine = Qwen3Engine(**({"model": args.model} if args.model else {}))
    for name in engine.presets():
        note = f"  ({DIALECT_PRESETS[name]}; not used unless named)" if name in DIALECT_PRESETS else ""
        print(f"preset:{name}{note}")
    engine.close()
    return 0
