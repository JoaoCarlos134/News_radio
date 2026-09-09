"""Stage 5 - publishing the private RSS feed to GitHub Pages. IMPLEMENTED.

Needs no GPU. It comes last because it only makes sense once stage 4 has
produced a real mp3.

Flow:
    episode mp3 -> copied into public_dir alongside a .json sidecar holding the
    metadata (title, themes, date) -> feed.xml rebuilt from scratch out of
    whatever is in public_dir -> git commit/push of the GitHub Pages branch.

Decisions worth remembering:
    - The feed is rebuilt in full on every run (never an incremental append) by
      scanning the mp3s in public_dir. If the feed is corrupted, or an mp3 is
      deleted by hand, the next run repairs it by itself.
    - Each mp3 carries a `<date>.json` sidecar with the episode metadata
      (title, themes, generation date). Without it the feed would have to
      reconstruct the title and themes from the filename. An mp3 with no
      sidecar (published outside the pipeline) still enters the feed with a
      generic title -- it never breaks the rebuild.
    - Only the most recent MAX_EPISODES_IN_FEED stay in the feed; older mp3s
      and sidecars are deleted from public_dir. GitHub Pages is not the place
      to archive months of episodes, and nobody listens to one from three
      months ago.
    - `build_feed` (which assembles the XML) is separate from publishing itself
      (git commit/push), so the feed can be tested without git or a network.
      Publishing is injected (`pusher`), like the client and engine of the
      other stages.
    - `public_dir` must already be a git checkout of the `gh-pages` branch,
      configured once rather than per run (see README, "Publication - GitHub
      Pages"). The default pusher will not create the branch itself: that is a
      manual setup step, not something to attempt at five in the morning.

Privacy: the feed is "private" only through the obscurity of its URL -- anyone
holding the link can listen. Publish nothing sensitive, and never submit the URL
to a podcast directory.

Note on language: the strings that end up *inside* the feed stay in Portuguese.
They are the podcast's own content, read by a Brazilian audience in the iOS
Podcasts app, and translating them would corrupt the product to tidy the source.
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
# Per-episode metadata (json sidecar next to the mp3)
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
        "title": f"Episódio de {mp3_path.stem}",  # feed content: stays pt-BR
        "themes": [],
        "generated_at": None,
    }


def _read_meta(mp3_path: Path) -> dict:
    """Read an episode's json sidecar; never fails on a missing or bad one.

    An mp3 with no `.json` (published outside the pipeline, or from a run that
    predates this feature) or with a corrupt or incomplete sidecar (interrupted
    write, full disk, hand editing) still has to enter the feed -- with a
    generic title, rather than breaking the entire rebuild.
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
# Feed construction (local I/O -- no git, no network; testable in isolation)
# --------------------------------------------------------------------------- #

def _prune_old_episodes(mp3_files: list[Path]) -> list[Path]:
    """Keep only the MAX_EPISODES_IN_FEED newest; delete the rest, mp3 + sidecar.

    Filenames are YYYY-MM-DD.mp3, so sorting by name is sorting by date --
    newest first.
    """
    mp3_files = sorted(mp3_files, key=lambda p: p.stem, reverse=True)
    manter, descartar = mp3_files[:MAX_EPISODES_IN_FEED], mp3_files[MAX_EPISODES_IN_FEED:]
    for antigo in descartar:
        log.info("removing old episode from feed: %s", antigo.name)
        antigo.unlink(missing_ok=True)
        _meta_path(antigo).unlink(missing_ok=True)
    return manter


def build_feed(config: PublishConfig, public_dir: Path) -> Path:
    """Rebuild feed.xml from the mp3s in public_dir."""
    from feedgen.feed import FeedGenerator

    if not config.base_url:
        raise ConfigError(
            "PODCAST_BASE_URL is not set - cannot build absolute feed URLs."
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
    if config.author and config.email:  # feedgen wants both or neither
        fg.podcast.itunes_owner(name=config.author, email=config.email)
    fg.podcast.itunes_explicit("no")

    for mp3_path in mp3_files:  # already ordered newest to oldest
        meta = _read_meta(mp3_path)
        url = f"{config.base_url}/{mp3_path.name}"
        temas = meta.get("themes") or []

        fe = fg.add_entry(order="append")  # feedgen prepends by default
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
# Publication (mp3 -> public_dir -> git push of the GitHub Pages branch)
# --------------------------------------------------------------------------- #

def default_pusher(public_dir: Path, message: str) -> None:
    """Commit public_dir and push it to the GitHub Pages branch.

    Assumes public_dir IS ALREADY a git checkout of the pages branch, set up
    once with `git worktree add data/public gh-pages` (see README). It will not
    create the branch itself: that is a setup operation, not a nightly one, and
    creating a branch and remote unattended at 5 a.m. is more risk than it is
    worth.
    """
    if not (public_dir / ".git").exists():
        raise ConfigError(
            f"{public_dir} is not a git checkout of the GitHub Pages branch.\n"
            "Set it up once (see README, 'Publication - GitHub Pages'):\n"
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
    pusher: Pusher = default_pusher,  # injected, as in stages 1-4
) -> Path:
    """Copy the mp3 into the published folder, rebuild the feed, push it."""
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
