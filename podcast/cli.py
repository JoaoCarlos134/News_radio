"""Command-line interface for the pipeline.

    python -m podcast.cli doctor          # diagnose the current environment
    python -m podcast.cli collect         # stage 1 (runs on any machine)
    python -m podcast.cli summarize       # stage 2 (needs GPU/Ollama)
    python -m podcast.cli script          # stage 3 (needs ANTHROPIC_API_KEY)
    python -m podcast.cli audio           # stage 4 (needs Kokoro)
    python -m podcast.cli publish         # stage 5
    python -m podcast.cli run             # all five in order — the scheduler's entry point

`doctor` is the first command to run on a new machine: it reports exactly what
is still missing, without executing any stage.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, ConfigError, load_config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _latest(directory: Path) -> Path | None:
    """Most recent JSON file in a stage output directory."""
    if not directory.exists():
        return None
    arquivos = sorted(directory.glob("*.json"))
    return arquivos[-1] if arquivos else None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    from .stage2_summarize import check_ollama
    from .stage4_audio import check_audio_setup
    from .stage5_publish import check_publish_setup

    print("Environment check\n" + "=" * 40)
    print(f"Project root   : {config.data_dir.parent}")
    print(f"Data directory : {config.data_dir}")
    print()

    tudo_ok = True

    # Stage 1 - no external dependency
    try:
        import feedparser  # noqa: F401
        import requests  # noqa: F401
        print("[ok]    stage 1 (RSS)      feedparser and requests installed")
    except ImportError as exc:
        tudo_ok = False
        print(f"[FAIL]  stage 1 (RSS)      {exc}")

    # Stage 2 - Ollama
    ok, msg = check_ollama(config.ollama)
    tudo_ok &= ok
    print(f"[{'ok' if ok else 'FAIL'}]{'    ' if ok else '  '}stage 2 (Ollama)   {msg.splitlines()[0]}")
    if not ok:
        for linha in msg.splitlines()[1:]:
            print(f"                           {linha}")

    # Stage 3 - paid API
    if config.script.api_key:
        print(f"[ok]    stage 3 (API)      key present, model {config.script.model}")
    else:
        tudo_ok = False
        print("[FAIL]  stage 3 (API)      ANTHROPIC_API_KEY not set in .env")

    # Stage 4 - Kokoro
    ok, msg = check_audio_setup(config.audio)
    tudo_ok &= ok
    print(f"[{'ok' if ok else 'FAIL'}]{'    ' if ok else '  '}stage 4 (Kokoro)   {msg.splitlines()[0]}")
    if not ok:
        for linha in msg.splitlines()[1:]:
            print(f"                           {linha}")

    # Stage 5 - publication
    ok, msg = check_publish_setup(config.publish)
    tudo_ok &= ok
    print(f"[{'ok' if ok else 'FAIL'}]{'    ' if ok else '  '}stage 5 (feed)     {msg}")

    print()
    print("All set." if tudo_ok else "Unresolved items above. See README.")
    return 0 if tudo_ok else 1


def cmd_sources(config: Config, args: argparse.Namespace) -> int:
    from .sources import FEED_SOURCES
    from .stage1_collect import fetch_feed, parse_feed

    for source in FEED_SOURCES:
        marca = " " if source.enabled else "x"
        if not args.check:
            print(f"[{marca}] {source.key:32} {source.name:26} {source.url}")
            continue

        try:
            raw = fetch_feed(source, config.collect.timeout)
            n = len(parse_feed(source, raw))
            print(f"[ok]    {source.key:32} {n:3} items")
        except Exception as exc:
            print(f"[FAIL]  {source.key:32} {exc}")
    return 0


def cmd_collect(config: Config, args: argparse.Namespace) -> int:
    from .stage1_collect import SeenStore, collect, save_collection

    # getattr com padrao: `run` reaproveita estes comandos com o proprio
    # Namespace, que nao tem as flags especificas de cada subcomando.
    dry_run = getattr(args, "dry_run", False)
    ignore_seen = getattr(args, "ignore_seen", False)

    seen = None
    if not ignore_seen:
        seen = SeenStore(config.seen_store_path, config.collect.seen_retention_days).load()

    collection = collect(config.collect, seen=seen)

    if seen is not None and not dry_run:
        seen.mark(collection.items)
        seen.save()

    print(f"\n{len(collection.items)} items collected "
          f"({collection.window_hours}h window)")
    if collection.errors:
        print(f"{len(collection.errors)} feed(s) failed:")
        for erro in collection.errors:
            print(f"  - {erro.source_key}: {erro.message}")

    if dry_run:
        for item in collection.items[:20]:
            quando = item.published_at.strftime("%d/%m %H:%M") if item.published_at else "  —   "
            print(f"  {quando}  [{item.source_name}] {item.title}")
        if len(collection.items) > 20:
            print(f"  ... and {len(collection.items) - 20} more")
        return 0

    path = save_collection(collection, config.raw_dir)
    print(f"Saved to {path}")
    return 0


def cmd_summarize(config: Config, args: argparse.Namespace) -> int:
    from .stage1_collect import load_collection
    from .stage2_summarize import save_digest, summarize

    explicito = getattr(args, "input", None)
    entrada = Path(explicito) if explicito else _latest(config.raw_dir)
    if entrada is None:
        print("No collection found. Run `collect` first.", file=sys.stderr)
        return 1

    digest = summarize(load_collection(entrada), config.ollama)
    path = save_digest(digest, config.summaries_dir)
    print(f"{len(digest.items)} items after triage. Saved to {path}")
    return 0


def cmd_script(config: Config, args: argparse.Namespace) -> int:
    from .stage2_summarize import load_digest
    from .stage3_script import (
        digest_from_collection_file,
        generate_script,
        save_script,
    )

    from_raw = getattr(args, "from_raw", None)
    explicito = getattr(args, "input", None)

    if from_raw:
        entrada = Path(from_raw)
        print(f"Test mode: using raw collection {entrada} (skipping stage 2 triage)")
        digest = digest_from_collection_file(entrada)
    else:
        entrada = Path(explicito) if explicito else _latest(config.summaries_dir)
        if entrada is None:
            print("No digest found. Run `summarize` first, or use "
                  "--from-raw data/raw/YYYY-MM-DD.json to test without a GPU.",
                  file=sys.stderr)
            return 1
        digest = load_digest(entrada)

    script = generate_script(digest, config.script)
    path = save_script(script, config.scripts_dir)

    from .stage3_script import estimate_minutes

    print(f"\n{script.title}")
    print(f"{len(script.lines)} lines, {script.word_count} words "
          f"(~{estimate_minutes(script.word_count):.0f} min)")
    print(f"Themes: {', '.join(script.themes)}")
    print(f"Saved to {path}")

    if getattr(args, "show", False):
        print()
        for line in script.lines:
            print(f"{line.speaker}: {line.text}\n")
    return 0


def cmd_audio(config: Config, args: argparse.Namespace) -> int:
    from .stage3_script import load_script
    from .stage4_audio import synthesize

    explicito = getattr(args, "input", None)
    entrada = Path(explicito) if explicito else _latest(config.scripts_dir)
    if entrada is None:
        print("No script found. Run `script` first.", file=sys.stderr)
        return 1

    path = synthesize(load_script(entrada), config.audio, config.audio_dir)
    print(f"Audio saved to {path}")
    return 0


def cmd_publish(config: Config, args: argparse.Namespace) -> int:
    from .stage3_script import load_script
    from .stage5_publish import publish_episode

    roteiro = _latest(config.scripts_dir)
    if roteiro is None:
        print("No script found.", file=sys.stderr)
        return 1

    script = load_script(roteiro)
    audio = config.audio_dir / f"{script.episode_date}.mp3"
    if not audio.exists():
        print(f"Audio not found: {audio}. Run `audio` first.", file=sys.stderr)
        return 1

    feed = publish_episode(script, audio, config.publish, config.public_dir)
    print(f"Feed updated at {feed}")
    return 0


def cmd_demo(config: Config, args: argparse.Namespace) -> int:
    """Run the real pipeline code over committed fixtures.

    Exists because the pipeline needs a GPU, a paid key and the Kokoro weights,
    so a fresh clone cannot otherwise produce anything. Every stage below runs
    its actual implementation; only the three external calls are skipped, and
    the feed is written to a scratch directory with a no-op pusher.
    """
    from .config import PublishConfig
    from .stage2_summarize import load_digest
    from .stage3_script import (
        WORDS_PER_MINUTE,
        build_user_prompt,
        estimate_minutes,
        load_script,
    )
    from .stage4_audio import plan_segments
    from .stage5_publish import publish_episode

    raiz = Path(__file__).resolve().parent.parent
    digest_file = raiz / "demo" / "digest.json"
    script_file = raiz / "demo" / "script.json"
    audio = raiz / "docs" / "sample-voice.mp3"

    faltando = [p for p in (digest_file, script_file, audio) if not p.exists()]
    if faltando:
        print("Demo files missing: " + ", ".join(str(p) for p in faltando),
              file=sys.stderr)
        return 1

    print("Demo - real pipeline code over committed fixtures.")
    print("No API key, no GPU, no network. Nothing is published.\n")

    digest = load_digest(digest_file)
    print(f"[stage 2] digest of {len(digest.items)} items")
    print(f"          themes: {', '.join(digest.themes)}")

    prompt = build_user_prompt(
        digest, "2026-08-04", config.script.target_minutes, config.script.max_minutes,
    )
    print(f"\n[stage 3] built a {len(prompt)}-character prompt and did NOT send it.")
    print("          This is the only call in the pipeline that costs money.")
    print("          Asking for a total word count returns about half, so the")
    print("          prompt decomposes it per theme:")
    for linha in prompt.splitlines()[4:6]:
        print(f"            {linha}")

    script = load_script(script_file)
    print(f"\n[stage 3] fixture output: {script.title}")
    print(f"          {len(script.lines)} lines, {script.word_count} words "
          f"(~{estimate_minutes(script.word_count):.1f} min at {WORDS_PER_MINUTE} wpm)")
    for line in script.lines[:2]:
        print(f"            {line.speaker}: {line.text[:96]}...")

    segments = plan_segments(script, config.audio)
    print(f"\n[stage 4] planned {len(segments)} TTS segments from "
          f"{len(script.lines)} lines.")
    print("          No synthesis here - that needs the Kokoro model files.")

    public_dir = config.data_dir / "demo"
    feed = publish_episode(
        script,
        audio,
        PublishConfig(
            base_url="https://example.github.io/demo-feed",
            title="News Radio - demo",
            author="Demo",
            email="demo@example.com",
        ),
        public_dir,
        pusher=lambda directory, message: None,  # never touches git
    )
    print(f"\n[stage 5] real RSS feed generated: {feed}")
    print(f"          {feed.stat().st_size} bytes, "
          f"audio from {audio.name}")
    print("\nThat feed.xml is genuine output - subscribe to it locally if you like.")
    return 0


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    """The whole pipeline. This is what the overnight scheduler calls."""
    inicio = datetime.now(timezone.utc)
    for nome, funcao in (
        ("collect", cmd_collect),
        ("summarize", cmd_summarize),
        ("script", cmd_script),
        ("audio", cmd_audio),
        ("publish", cmd_publish),
    ):
        print(f"\n=== {nome} ===")
        codigo = funcao(config, args)
        if codigo != 0:
            print(f"Pipeline stopped at stage {nome}.", file=sys.stderr)
            return codigo

    duracao = (datetime.now(timezone.utc) - inicio).total_seconds()
    print(f"\nPipeline finished in {duracao / 60:.1f} min.")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="podcast",
        description="Daily economy and geopolitics podcast pipeline.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="report what is installed and configured")

    p_sources = sub.add_parser("sources", help="list the configured RSS sources")
    p_sources.add_argument("--check", action="store_true",
                           help="fetch every feed and report which respond")

    p_collect = sub.add_parser("collect", help="stage 1 - RSS collection")
    p_collect.add_argument("--dry-run", action="store_true",
                           help="print without saving or marking as seen")
    p_collect.add_argument("--ignore-seen", action="store_true",
                           help="do not filter out items from previous runs")

    p_sum = sub.add_parser("summarize", help="stage 2 - summarise/triage (Ollama)")
    p_sum.add_argument("--input", help="collection file (default: most recent)")

    p_script = sub.add_parser("script", help="stage 3 - script (paid API)")
    p_script.add_argument("--input", help="digest (default: most recent)")
    p_script.add_argument("--from-raw", metavar="FILE",
                          help="skip stage 2 and use the raw collection (test without a GPU)")
    p_script.add_argument("--show", action="store_true", help="print the generated script")

    p_audio = sub.add_parser("audio", help="stage 4 - audio (Kokoro)")
    p_audio.add_argument("--input", help="script (default: most recent)")

    sub.add_parser("publish", help="stage 5 - RSS feed")
    sub.add_parser("run", help="run all five stages in order")
    sub.add_parser("demo", help="run the pipeline over fixtures; no key, GPU or network")

    return parser


COMMANDS = {
    "doctor": cmd_doctor,
    "sources": cmd_sources,
    "collect": cmd_collect,
    "summarize": cmd_summarize,
    "script": cmd_script,
    "audio": cmd_audio,
    "publish": cmd_publish,
    "run": cmd_run,
    "demo": cmd_demo,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    try:
        config = load_config()
        return COMMANDS[args.command](config, args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(f"{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
