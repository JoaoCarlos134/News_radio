"""Interface de linha de comando do pipeline.

    python -m podcast.cli doctor          # diagnostica o ambiente atual
    python -m podcast.cli collect         # etapa 1 (roda em qualquer maquina)
    python -m podcast.cli summarize       # etapa 2 (exige GPU/Ollama)
    python -m podcast.cli script          # etapa 3 (exige ANTHROPIC_API_KEY)
    python -m podcast.cli audio           # etapa 4 (exige Kokoro)
    python -m podcast.cli publish         # etapa 5
    python -m podcast.cli run             # tudo, na ordem — uso do agendador

`doctor` e o comando a rodar primeiro no PC de destino: diz exatamente o que
ainda falta instalar, sem executar nenhuma etapa.
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
    """Arquivo JSON mais recente de uma pasta de etapa."""
    if not directory.exists():
        return None
    arquivos = sorted(directory.glob("*.json"))
    return arquivos[-1] if arquivos else None


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #

def cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    from .stage2_summarize import check_ollama
    from .stage4_audio import check_audio_setup
    from .stage5_publish import check_publish_setup

    print("Diagnóstico do ambiente\n" + "=" * 40)
    print(f"Raiz do projeto : {config.data_dir.parent}")
    print(f"Pasta de dados  : {config.data_dir}")
    print()

    tudo_ok = True

    # Etapa 1 — sem dependencia externa
    try:
        import feedparser  # noqa: F401
        import requests  # noqa: F401
        print("[ok]    etapa 1 (RSS)      feedparser e requests instalados")
    except ImportError as exc:
        tudo_ok = False
        print(f"[FALHA] etapa 1 (RSS)      {exc}")

    # Etapa 2 — Ollama
    ok, msg = check_ollama(config.ollama)
    tudo_ok &= ok
    print(f"[{'ok' if ok else 'FALHA'}]{'    ' if ok else ' '}etapa 2 (Ollama)   {msg.splitlines()[0]}")
    if not ok:
        for linha in msg.splitlines()[1:]:
            print(f"                           {linha}")

    # Etapa 3 — API paga
    if config.script.api_key:
        print(f"[ok]    etapa 3 (API)      chave presente, modelo {config.script.model}")
    else:
        tudo_ok = False
        print("[FALHA] etapa 3 (API)      ANTHROPIC_API_KEY não definida no .env")

    # Etapa 4 — Kokoro
    ok, msg = check_audio_setup(config.audio)
    tudo_ok &= ok
    print(f"[{'ok' if ok else 'FALHA'}]{'    ' if ok else ' '}etapa 4 (Kokoro)   {msg.splitlines()[0]}")
    if not ok:
        for linha in msg.splitlines()[1:]:
            print(f"                           {linha}")

    # Etapa 5 — publicacao
    ok, msg = check_publish_setup(config.publish)
    tudo_ok &= ok
    print(f"[{'ok' if ok else 'FALHA'}]{'    ' if ok else ' '}etapa 5 (feed)     {msg}")

    print()
    print("Tudo pronto." if tudo_ok else "Há pendências acima. Ver README.")
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
            print(f"[ok]    {source.key:32} {n:3} itens")
        except Exception as exc:
            print(f"[FALHA] {source.key:32} {exc}")
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

    print(f"\n{len(collection.items)} notícias coletadas "
          f"(janela de {collection.window_hours}h)")
    if collection.errors:
        print(f"{len(collection.errors)} feed(s) com erro:")
        for erro in collection.errors:
            print(f"  - {erro.source_key}: {erro.message}")

    if dry_run:
        for item in collection.items[:20]:
            quando = item.published_at.strftime("%d/%m %H:%M") if item.published_at else "  —   "
            print(f"  {quando}  [{item.source_name}] {item.title}")
        if len(collection.items) > 20:
            print(f"  ... e mais {len(collection.items) - 20}")
        return 0

    path = save_collection(collection, config.raw_dir)
    print(f"Gravado em {path}")
    return 0


def cmd_summarize(config: Config, args: argparse.Namespace) -> int:
    from .stage1_collect import load_collection
    from .stage2_summarize import save_digest, summarize

    explicito = getattr(args, "input", None)
    entrada = Path(explicito) if explicito else _latest(config.raw_dir)
    if entrada is None:
        print("Nenhuma coleta encontrada. Rode `collect` primeiro.", file=sys.stderr)
        return 1

    digest = summarize(load_collection(entrada), config.ollama)
    path = save_digest(digest, config.summaries_dir)
    print(f"{len(digest.items)} itens após triagem. Gravado em {path}")
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
        print(f"Modo de teste: usando a coleta bruta {entrada} (sem triagem da etapa 2)")
        digest = digest_from_collection_file(entrada)
    else:
        entrada = Path(explicito) if explicito else _latest(config.summaries_dir)
        if entrada is None:
            print("Nenhum digest encontrado. Rode `summarize` primeiro, ou use "
                  "--from-raw data/raw/AAAA-MM-DD.json para testar sem GPU.",
                  file=sys.stderr)
            return 1
        digest = load_digest(entrada)

    script = generate_script(digest, config.script)
    path = save_script(script, config.scripts_dir)

    from .stage3_script import estimate_minutes

    print(f"\n{script.title}")
    print(f"{len(script.lines)} falas, {script.word_count} palavras "
          f"(~{estimate_minutes(script.word_count):.0f} min)")
    print(f"Temas: {', '.join(script.themes)}")
    print(f"Gravado em {path}")

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
        print("Nenhum roteiro encontrado. Rode `script` primeiro.", file=sys.stderr)
        return 1

    path = synthesize(load_script(entrada), config.audio, config.audio_dir)
    print(f"Áudio gravado em {path}")
    return 0


def cmd_publish(config: Config, args: argparse.Namespace) -> int:
    from .stage3_script import load_script
    from .stage5_publish import publish_episode

    roteiro = _latest(config.scripts_dir)
    if roteiro is None:
        print("Nenhum roteiro encontrado.", file=sys.stderr)
        return 1

    script = load_script(roteiro)
    audio = config.audio_dir / f"{script.episode_date}.mp3"
    if not audio.exists():
        print(f"Áudio não encontrado: {audio}. Rode `audio` primeiro.", file=sys.stderr)
        return 1

    feed = publish_episode(script, audio, config.publish, config.public_dir)
    print(f"Feed atualizado em {feed}")
    return 0


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    """Pipeline completo. E este o comando que o agendador chama de madrugada."""
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
            print(f"Pipeline interrompido na etapa {nome}.", file=sys.stderr)
            return codigo

    duracao = (datetime.now(timezone.utc) - inicio).total_seconds()
    print(f"\nPipeline concluído em {duracao / 60:.1f} min.")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="podcast",
        description="Pipeline do podcast diário de economia e geopolítica.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="diagnostica o que está instalado e configurado")

    p_sources = sub.add_parser("sources", help="lista as fontes RSS configuradas")
    p_sources.add_argument("--check", action="store_true",
                           help="baixa cada feed e reporta quais respondem")

    p_collect = sub.add_parser("collect", help="etapa 1 — coleta RSS")
    p_collect.add_argument("--dry-run", action="store_true",
                           help="mostra na tela sem gravar nem marcar como visto")
    p_collect.add_argument("--ignore-seen", action="store_true",
                           help="não filtra itens de execuções anteriores")

    p_sum = sub.add_parser("summarize", help="etapa 2 — resumo/triagem (Ollama)")
    p_sum.add_argument("--input", help="arquivo de coleta (padrão: o mais recente)")

    p_script = sub.add_parser("script", help="etapa 3 — roteiro (API paga)")
    p_script.add_argument("--input", help="digest (padrão: o mais recente)")
    p_script.add_argument("--from-raw", metavar="ARQUIVO",
                          help="pula a etapa 2 e usa a coleta bruta (teste sem GPU)")
    p_script.add_argument("--show", action="store_true", help="imprime o roteiro")

    p_audio = sub.add_parser("audio", help="etapa 4 — áudio (Kokoro)")
    p_audio.add_argument("--input", help="roteiro (padrão: o mais recente)")

    sub.add_parser("publish", help="etapa 5 — feed RSS")
    sub.add_parser("run", help="executa as 5 etapas em sequência")

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
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    try:
        config = load_config()
        return COMMANDS[args.command](config, args)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(f"{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
