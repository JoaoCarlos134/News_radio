"""Etapa 5 — publicacao do feed RSS privado no GitHub Pages. IMPLEMENTADA.

Nao exige GPU. Fica por ultimo porque so faz sentido depois que a etapa 4
produzir mp3 de verdade.

Fluxo:
    mp3 do episodio -> copiado para public_dir junto de um sidecar .json com
    os metadados (titulo, temas, data) -> feed.xml reconstruido do zero a
    partir do que existe em public_dir -> git commit/push da branch do
    GitHub Pages.

Decisoes que valem lembrar:
    - O feed e reconstruido inteiro a cada execucao (nunca append incremental)
      varrendo os mp3 existentes em public_dir. Se o feed corromper ou um mp3
      for apagado a mao, a proxima execucao conserta sozinha.
    - Cada mp3 vem com um sidecar `<data>.json` com os metadados do episodio
      (titulo, temas, data de geracao). Sem isso o feed teria que extrair
      titulo/tema de volta do nome do arquivo. Um mp3 sem sidecar (publicado
      por fora do pipeline) ainda entra no feed, com um titulo generico —
      nunca quebra a reconstrucao.
    - So os ultimos MAX_EPISODES_IN_FEED ficam no feed; os mp3 e sidecars mais
      antigos sao apagados de public_dir. GitHub Pages nao e o lugar de
      arquivar meses de episodios, e ninguem vai ouvir o de 3 meses atras.
    - `build_feed` (monta o XML) e separado da publicacao em si (git
      commit/push), para poder testar a montagem do feed sem git nem rede. A
      publicacao entra por injecao (`pusher`), como o cliente/engine das
      outras etapas.
    - `public_dir` precisa ser, de antemao, um checkout git da branch
      `gh-pages` (configurado uma vez, nao a cada execucao — ver README,
      secao "Publicacao — GitHub Pages"). O pusher padrao nao cria a branch
      sozinho: e uma etapa de setup manual, nao de execucao noturna.

Privacidade: o feed e "privado" apenas por obscuridade da URL — qualquer um com
o link consegue ouvir. Nao inclua nada sensivel, e nao submeta a URL a diretorios
de podcast.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .config import ConfigError, PublishConfig
from .models import Script

log = logging.getLogger(__name__)

MAX_EPISODES_IN_FEED = 30
FEED_FILENAME = "feed.xml"

Pusher = Callable[[Path, str], None]


# --------------------------------------------------------------------------- #
# Metadados por episodio (sidecar json ao lado do mp3)
# --------------------------------------------------------------------------- #

def _meta_path(mp3_path: Path) -> Path:
    return mp3_path.with_suffix(".json")


def _write_meta(script: Script, public_dir: Path) -> None:
    meta = {
        "episode_date": script.episode_date,
        "title": script.title,
        "themes": script.themes,
        "generated_at": script.generated_at.isoformat(),
    }
    mp3_path = public_dir / f"{script.episode_date}.mp3"
    _meta_path(mp3_path).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _fallback_meta(mp3_path: Path) -> dict:
    return {
        "episode_date": mp3_path.stem,
        "title": f"Episódio de {mp3_path.stem}",
        "themes": [],
        "generated_at": None,
    }


def _read_meta(mp3_path: Path) -> dict:
    """Le o sidecar json de um episodio; nunca falha por sidecar ausente ou ruim.

    Um mp3 sem `.json` (publicado por fora do pipeline, ou de uma execucao
    anterior a esta funcionalidade) ou com um sidecar corrompido/incompleto
    (escrita interrompida, disco cheio, edicao manual) ainda precisa entrar no
    feed — so com um titulo generico em vez de quebrar a reconstrucao inteira.
    """
    meta_path = _meta_path(mp3_path)
    if not meta_path.exists():
        return _fallback_meta(mp3_path)

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("corrupt sidecar, ignoring: %s", meta_path)
        return _fallback_meta(mp3_path)

    if "title" not in meta:
        log.warning("sidecar has no 'title', ignoring: %s", meta_path)
        return _fallback_meta(mp3_path)
    return meta


# --------------------------------------------------------------------------- #
# Construcao do feed (I/O local — sem git, sem rede; testavel isoladamente)
# --------------------------------------------------------------------------- #

def _prune_old_episodes(mp3_files: list[Path]) -> list[Path]:
    """Mantem so os MAX_EPISODES_IN_FEED mais recentes; apaga mp3 + sidecar dos demais.

    Nomes de arquivo sao AAAA-MM-DD.mp3, entao ordenar pelo nome equivale a
    ordenar pela data — mais recente primeiro.
    """
    mp3_files = sorted(mp3_files, key=lambda p: p.stem, reverse=True)
    manter, descartar = mp3_files[:MAX_EPISODES_IN_FEED], mp3_files[MAX_EPISODES_IN_FEED:]
    for antigo in descartar:
        log.info("removing old episode from feed: %s", antigo.name)
        antigo.unlink(missing_ok=True)
        _meta_path(antigo).unlink(missing_ok=True)
    return manter


def build_feed(config: PublishConfig, public_dir: Path) -> Path:
    """Reconstroi feed.xml a partir dos mp3 em public_dir."""
    from feedgen.feed import FeedGenerator

    if not config.base_url:
        raise ConfigError(
            "PODCAST_BASE_URL não definido — não dá para montar URLs absolutas do feed."
        )

    public_dir.mkdir(parents=True, exist_ok=True)
    mp3_files = _prune_old_episodes(list(public_dir.glob("*.mp3")))

    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(config.title)
    fg.link(href=config.base_url, rel="alternate")
    fg.description(f"{config.title} — episódios diários gerados automaticamente.")
    fg.language(config.language)
    if config.author:
        fg.podcast.itunes_author(config.author)
    if config.author and config.email:  # feedgen exige os dois juntos, ou nenhum
        fg.podcast.itunes_owner(name=config.author, email=config.email)
    fg.podcast.itunes_explicit("no")

    for mp3_path in mp3_files:  # ja ordenado do mais recente para o mais antigo
        meta = _read_meta(mp3_path)
        url = f"{config.base_url}/{mp3_path.name}"
        temas = meta.get("themes") or []

        fe = fg.add_entry(order="append")  # feedgen prepende por padrao
        fe.id(url)
        fe.title(meta["title"])
        fe.description(f"Temas: {', '.join(temas)}." if temas else meta["title"])
        fe.enclosure(url, str(mp3_path.stat().st_size), "audio/mpeg")
        if meta.get("generated_at"):
            fe.pubDate(meta["generated_at"])

    feed_path = public_dir / FEED_FILENAME
    fg.rss_file(str(feed_path))
    log.info("feed rebuilt with %d episode(s): %s", len(mp3_files), feed_path)
    return feed_path


# --------------------------------------------------------------------------- #
# Publicacao (mp3 -> public_dir -> git push da branch do GitHub Pages)
# --------------------------------------------------------------------------- #

def default_pusher(public_dir: Path, message: str) -> None:
    """Comita e envia public_dir para a branch do GitHub Pages.

    Assume que public_dir JA E um checkout git da branch gh-pages (configurado
    uma vez com `git worktree add data/public gh-pages` — ver README). Nao
    tenta criar a branch sozinho: e uma operacao de setup, nao de execucao
    noturna, e criar branch/remote sozinho na madrugada e mais risco do que
    vale.
    """
    if not (public_dir / ".git").exists():
        raise ConfigError(
            f"{public_dir} não é um checkout git da branch do GitHub Pages.\n"
            "Configure uma vez (ver README, 'Publicação — GitHub Pages'):\n"
            f"  git worktree add {public_dir} gh-pages"
        )

    status = subprocess.run(
        ["git", "-C", str(public_dir), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    if not status.stdout.strip():
        log.info("nothing to publish - %s is already up to date", public_dir)
        return

    subprocess.run(["git", "-C", str(public_dir), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(public_dir), "commit", "-m", message], check=True)
    subprocess.run(["git", "-C", str(public_dir), "push"], check=True)
    log.info("published to GitHub Pages: %s", message)


def publish_episode(
    script: Script,
    audio_path: Path,
    config: PublishConfig,
    public_dir: Path,
    pusher: Pusher = default_pusher,  # injetavel, como nas etapas 1-4
) -> Path:
    """Copia o mp3 para a pasta publicada, regenera o feed e envia ao GitHub Pages."""
    public_dir.mkdir(parents=True, exist_ok=True)

    destino = public_dir / f"{script.episode_date}.mp3"
    shutil.copy2(audio_path, destino)
    _write_meta(script, public_dir)

    feed_path = build_feed(config, public_dir)
    pusher(public_dir, f"Episódio {script.episode_date}: {script.title}")
    return feed_path


def check_publish_setup(config: PublishConfig) -> tuple[bool, str]:
    """Check that the minimum publication settings are filled in."""
    faltando = [
        nome for nome, valor in (
            ("PODCAST_BASE_URL", config.base_url),
            ("PODCAST_AUTHOR", config.author),
            ("PODCAST_EMAIL", config.email),
        ) if not valor
    ]
    if faltando:
        return False, "Not set in .env: " + ", ".join(faltando)
    if not config.base_url.startswith(("http://", "https://")):
        return False, f"PODCAST_BASE_URL must be an absolute URL: {config.base_url!r}"
    return True, f"Publishing to {config.base_url}"
