"""`huashuo` command line (CLI-1 … CLI-7).

    huashuo BOOK                 import, check, synthesize and package in one go
    huashuo import BOOK          build the work directory; list chapters and skipped text
    huashuo check BOOK           verify the script against text.txt (docs/script-ir.md §5)
    huashuo synth BOOK           synthesize (resumable); --sample / --chapters for auditions
    huashuo package BOOK         write the M4B from what has been synthesized
    huashuo voices               list the engine's preset voices

BOOK is the source file (.txt / .epub); its work directory defaults to <BOOK>.huashuo/.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from huashuo import __version__

COMMANDS = ("make", "import", "check", "synth", "package", "voices")
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
        p.add_argument("--cover", type=Path, help="cover image to use instead of the book's own")
        p.add_argument("--read-notes", action=argparse.BooleanOptionalAction, default=None,
                       help="read editorial annotations (注釋 sections); skipped by default, "
                            "and the choice is remembered for this book")

    def synth_options(p):
        p.add_argument("--voice", help="narrator voice for this run, e.g. preset:vivian "
                                       "(to keep it, edit cast.json)")
        p.add_argument("--model", help="Qwen3-TTS CustomVoice model (default: 1.7B 8-bit)")
        p.add_argument("--no-asr", action="store_true",
                       help="skip the speech-recognition check of each unit (~5-10%% faster)")
        p.add_argument("--no-titles", action="store_true", help="do not read chapter titles aloud")
        p.add_argument("--sample", type=int, nargs="?", const=600, metavar="CHARS",
                       help="only the first CHARS characters (default 600), to <book>.sample.m4b")
        p.add_argument("--chapters", help="only these chapters, e.g. 1,3-5 (numbers from `import`)")
        p.add_argument("--engine", default="qwen3", help=argparse.SUPPRESS)

    def package_options(p):
        p.add_argument("-o", "--output", type=Path, help="output M4B (default: next to the book)")
        p.add_argument("--bitrate", default="64k", help="AAC bitrate (default 64k)")

    p = book_command("make", "import, check, synthesize and package (the default)")
    import_options(p), synth_options(p), package_options(p)
    p.add_argument("--dry-run", action="store_true", help="import and show the plan, synthesize nothing")
    p = book_command("import", "build or refresh the work directory")
    import_options(p)
    book_command("check", "check the script's invariants")
    p = book_command("synth", "synthesize units into the cache")
    synth_options(p)
    p = book_command("package", "encode the M4B from cached units")
    synth_options(p), package_options(p)
    p = sub.add_parser("voices", help="list preset voices")
    p.add_argument("--model", help="CustomVoice model")
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
    """A detailed log per run in <workdir>/logs/ (CLI-7); set up once per process."""
    if log.handlers:
        return
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
                "package": cmd_package, "voices": cmd_voices}[args.command](args)
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to continue where it stopped")
        return 130
    except Exception as exc:  # user-facing errors carry their own advice (CLI-7)
        from huashuo.engines import EngineError
        from huashuo.huaben import HuabenError
        from huashuo.ingest import IngestError
        from huashuo.m4b import PackageError
        from huashuo.pipeline import PipelineError
        if isinstance(exc, (EngineError, HuabenError, IngestError, PackageError, PipelineError)):
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
    wd = _workdir(args)
    _setup_logging(wd)
    result = import_book(args.book, wd, args.encoding, args.language, args.cover, args.read_notes)
    h = result.script.header
    chars = sum(len(b.get("text", "")) for b in result.script.blocks if b.get("type") != "skip")
    print(f"《{h['title']}》 {h.get('author') or '(author unknown)'}  [{h['language']}"
          f"{', ' + result.encoding if result.encoding else ''}]  {chars:,} characters")
    print(f"work directory: {wd.root}")
    for line in result.merge_report:
        print(f"  merge: {line}")
    if not quiet:
        _print_structure(result.script)
    return 0


def _print_structure(script) -> None:
    """Chapters with sizes, and everything that will not be read (TXT-8)."""
    chapters, current = [], None
    for block in script.blocks:
        if block.get("type") == "chapter":
            current = [block.get("text", ""), 0, block.get("level", 1)]
            chapters.append(current)
        elif current is not None and block.get("type") in ("narration", "dialogue", "heading"):
            current[1] += len(block.get("text", ""))
    print(f"\n{len(chapters)} chapters:")
    for number, (title, chars, level) in enumerate(chapters, 1):
        print(f"  {number:4d}  {'  ' if level == 2 else ''}{title[:40]:<40} {chars:>8,} 字")
    skipped = [b for b in script.blocks if b.get("type") == "skip"]
    if skipped:
        print(f"\n{len(skipped)} paragraphs kept but not read:")
        for block in skipped[:15]:
            print(f"  [{block.get('reason')}] {block.get('text', '')[:50]}")
        if len(skipped) > 15:
            print(f"  … and {len(skipped) - 15} more (type \"skip\" in the script)")


def cmd_check(args) -> int:
    from huashuo.pipeline import check_project, load_project

    problems = check_project(load_project(_workdir(args)))
    for problem in problems:
        print(problem)
    print("script OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def _engine(args):
    from huashuo.engines import create_engine

    options = {"model": args.model} if getattr(args, "model", None) else {}
    return create_engine(args.engine, **options)


def _prepare(args):
    """Load, check and plan; shared by synth, package and make."""
    from huashuo.pipeline import check_project, load_project, make_plan

    wd = _workdir(args)
    project = load_project(wd)
    problems = check_project(project)
    if problems:
        for problem in problems[:20]:
            print(problem, file=sys.stderr)
        raise SystemExit("the script has problems (above); fix them or re-run `huashuo import`")
    plan = make_plan(project, read_titles=not args.no_titles, voice=args.voice,
                     sample_chars=args.sample, chapters=_parse_chapters(args.chapters))
    if not plan.units:
        raise SystemExit("nothing to read")
    return wd, project, plan


def cmd_synth(args) -> int:
    from huashuo.pipeline import estimate
    from huashuo.synth import synthesize

    wd, project, plan = _prepare(args)
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


def cmd_package(args) -> int:
    from huashuo.m4b import BookInfo, make_cover, probe, write_m4b
    from huashuo.post import layout, stream
    from huashuo.synth import cached_ok, unit_key

    wd, project, plan = _prepare(args)
    engine = _engine(args)
    keys = [unit_key(u, engine.identity(), project.language) for u in plan.units]
    missing = sum(1 for k in keys if cached_ok(wd, k) is None)
    if missing:
        raise SystemExit(f"{missing} of {len(keys)} units are not synthesized yet; run `huashuo synth` "
                         f"with the same options first")
    header = project.script.header
    default_name = args.book.stem + (".sample" if args.sample is not None else "") + ".m4b"
    output = args.output or args.book.with_name(default_name)

    cover = wd.find_cover()
    if cover is None:
        cover = make_cover(wd.cover(".jpg"), header.get("title", ""), header.get("author", ""))
    info = BookInfo(title=header.get("title", args.book.stem), author=header.get("author", ""),
                    language=project.language, description=header.get("meta", {}).get("description", ""),
                    date=header.get("meta", {}).get("date", ""))
    timeline = layout(plan, keys, wd)
    print(f"encoding {output.name}: {_hms(timeline.seconds)}, {len(timeline.chapters)} chapters …")
    write_m4b(output, stream(timeline, wd), timeline.sample_rate, info, timeline.chapters, cover,
              args.bitrate)
    result = probe(output)
    seconds = float(result["format"]["duration"])
    print(f"wrote {output} ({_mb(output.stat().st_size)}, {_hms(seconds)}, "
          f"{len(result.get('chapters', []))} chapters)")
    return 0


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


def cmd_voices(args) -> int:
    from huashuo.engines.qwen3 import DIALECT_PRESETS, Qwen3Engine

    engine = Qwen3Engine(**({"model": args.model} if args.model else {}))
    for name in engine.presets():
        note = f"  ({DIALECT_PRESETS[name]}; not used unless named)" if name in DIALECT_PRESETS else ""
        print(f"preset:{name}{note}")
    engine.close()
    return 0
