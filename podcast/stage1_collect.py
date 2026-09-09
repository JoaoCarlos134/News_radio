"""Stage 1 - RSS collection. IMPLEMENTED.

Needs neither a GPU nor the paid API: it runs and is testable anywhere.

Flow:
    fetch each feed -> parse -> normalise -> filter by the time window
    -> drop items already seen in previous runs -> deduplicate
    -> sort by recency -> write JSON

An error in one feed never takes the collection down: it is recorded in
`Collection.errors` and the rest continues. One outlet being offline cannot cost
the day's episode.

Copyright: this stage requests the feed URL and nothing else. There is no
scraper here, and adding one would change the project's legal position rather
than merely its data. See _entry_summary.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

from .config import CollectConfig
from .models import Collection, FeedError, NewsItem
from .sources import FeedSource, enabled_sources
from .textutils import (
    canonical_url,
    clean_summary,
    stable_id,
    strip_html,
    title_similarity,
    truncate,
)

log = logging.getLogger(__name__)

# Alguns veiculos bloqueiam user-agents de biblioteca.
USER_AGENT = (
    "Mozilla/5.0 (compatible; PodcastDiarioBot/0.1; personal use, non-commercial)"
)

# Ceiling on each item's summary. A headline plus a short summary is enough for
# stage 2 to triage on, and it keeps the local model's context inside 12 GB of
# VRAM. It is also the mechanical half of the copyright guarantee.
MAX_SUMMARY_CHARS = 600

# Above this, two titles are considered the same story.
DUPLICATE_TITLE_THRESHOLD = 0.75


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

def fetch_feed(source: FeedSource, timeout: int) -> str:
    """Fetch a feed's raw XML. Raises requests.RequestException on failure."""
    response = requests.get(
        source.url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    response.raise_for_status()
    # feedparser copes better with bytes: it honours the encoding declared in
    # the XML, which does not always match the HTTP header.
    return response.content


# --------------------------------------------------------------------------- #
# Parsing (pure -- no network, tested against local fixtures)
# --------------------------------------------------------------------------- #

def _entry_datetime(entry) -> datetime | None:  # noqa: ANN001
    """Publication date in UTC. feedparser already converts to a UTC struct_time."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _entry_summary(entry) -> str:  # noqa: ANN001
    """The summary the feed publishes, stripped of HTML.

    We prefer `summary` over `content`: `content` usually carries the whole
    article, which we must not reproduce (see CLAUDE.md, copyright constraint).
    This choice, plus the character cap above, is what makes the copyright claim
    structural rather than aspirational.
    """
    raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    return truncate(clean_summary(strip_html(raw)), MAX_SUMMARY_CHARS)


def _entry_categories(entry, source: FeedSource) -> tuple[str, ...]:  # noqa: ANN001
    tags = getattr(entry, "tags", None) or []
    from_feed = tuple(
        t.get("term", "").strip().lower()
        for t in tags
        if isinstance(t, dict) and t.get("term")
    )
    # dict.fromkeys preserves order and removes repeats
    return tuple(dict.fromkeys(source.categories + from_feed))


def parse_feed(source: FeedSource, raw: bytes | str) -> list[NewsItem]:
    """Convert a feed's XML into normalised NewsItem objects.

    A pure function: bytes in, items out. This is the stage's test seam.
    Entries with no title or no link are dropped -- there is nothing to
    synthesise from them.
    """
    parsed = feedparser.parse(raw)
    items: list[NewsItem] = []

    for entry in parsed.entries:
        title = strip_html(getattr(entry, "title", ""))
        link = (getattr(entry, "link", "") or "").strip()
        if not title or not link:
            continue

        canonical = canonical_url(link)
        items.append(
            NewsItem(
                # The id comes from the canonical URL: the same article in two
                # feeds of one outlet collapses to a single id, and that id
                # survives across runs.
                id=stable_id(canonical or title),
                source_key=source.key,
                source_name=source.name,
                title=title,
                summary=_entry_summary(entry),
                link=canonical or link,
                published_at=_entry_datetime(entry),
                categories=_entry_categories(entry, source),
            )
        )

    return items


# --------------------------------------------------------------------------- #
# Filters and deduplication (pure)
# --------------------------------------------------------------------------- #

def filter_by_window(
    items: list[NewsItem],
    window_hours: int,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Keep only items inside the window.

    Undated items are kept: several official feeds (BCB, IBGE) omit pubDate, and
    discarding them would lose exactly the primary sources. The seen-cache is
    what stops them recurring the next day.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    # Tolerance for feeds whose clock runs fast.
    horizon = now + timedelta(hours=6)
    return [
        item for item in items
        if item.published_at is None or cutoff <= item.published_at <= horizon
    ]


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Remove duplicates by id/URL and by similar title.

    Keeps the first occurrence. Since items arrive sorted by recency, the
    version preserved is the most recent one.
    """
    kept: list[NewsItem] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    for item in items:
        canonical = canonical_url(item.link)
        if item.id in seen_ids or (canonical and canonical in seen_urls):
            continue
        if any(title_similarity(item.title, k.title) >= DUPLICATE_TITLE_THRESHOLD
               for k in kept):
            continue

        kept.append(item)
        seen_ids.add(item.id)
        if canonical:
            seen_urls.add(canonical)

    return kept


def sort_items(items: list[NewsItem]) -> list[NewsItem]:
    """Most recent first; undated items go to the end."""
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(items, key=lambda i: i.published_at or epoch, reverse=True)


# --------------------------------------------------------------------------- #
# Cache of already-processed items
# --------------------------------------------------------------------------- #

class SeenStore:
    """Records ids already used in previous episodes.

    Without this, a story published at 23:00 enters today's episode and
    tomorrow's as well, because it is still inside the 24-hour window.
    """

    def __init__(self, path: Path, retention_days: int = 7) -> None:
        self.path = path
        self.retention_days = retention_days
        self._seen: dict[str, str] = {}

    def load(self) -> SeenStore:
        if self.path.exists():
            try:
                self._seen = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                # A corrupt cache must not block collection: restart empty.
                log.warning("cache 'ja visto' ilegivel (%s), recomecando vazio", exc)
                self._seen = {}
        self._prune()
        return self

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        self._seen = {
            item_id: ts for item_id, ts in self._seen.items()
            if _safe_parse(ts) and _safe_parse(ts) >= cutoff  # type: ignore[operator]
        }

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._seen

    def filter_new(self, items: list[NewsItem]) -> list[NewsItem]:
        return [i for i in items if i.id not in self._seen]

    def mark(self, items: list[NewsItem]) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        for item in items:
            self._seen[item.id] = stamp

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._seen, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _safe_parse(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def collect(
    config: CollectConfig,
    sources: tuple[FeedSource, ...] | None = None,
    seen: SeenStore | None = None,
    now: datetime | None = None,
    fetcher=fetch_feed,  # noqa: ANN001 - injected in tests
) -> Collection:
    """Run the whole of stage 1.

    `fetcher` is injected so the tests run without a network.
    """
    sources = sources if sources is not None else enabled_sources()
    now = now or datetime.now(timezone.utc)

    all_items: list[NewsItem] = []
    errors: list[FeedError] = []
    stats: dict[str, int] = {}

    for source in sources:
        started = time.monotonic()
        try:
            raw = fetcher(source, config.timeout)
        except Exception as exc:  # network, DNS, HTTP 4xx/5xx, timeout...
            log.warning("feed %s failed: %s", source.key, exc)
            errors.append(FeedError(source_key=source.key, message=str(exc)))
            stats[source.key] = 0
            continue

        try:
            items = parse_feed(source, raw)
        except Exception as exc:  # unrecoverable XML
            log.warning("feed %s could not be parsed: %s", source.key, exc)
            errors.append(FeedError(source_key=source.key, message=f"parse: {exc}"))
            stats[source.key] = 0
            continue

        items = filter_by_window(items, config.window_hours, now=now)
        items = sort_items(items)[: config.max_items_per_feed]

        stats[source.key] = len(items)
        all_items.extend(items)
        log.info(
            "feed %s: %d items in %.1fs", source.key, len(items),
            time.monotonic() - started,
        )

    all_items = sort_items(all_items)
    if seen is not None:
        before = len(all_items)
        all_items = seen.filter_new(all_items)
        stats["_ja_vistos_descartados"] = before - len(all_items)

    before_dedup = len(all_items)
    all_items = deduplicate(all_items)
    stats["_duplicatas_descartadas"] = before_dedup - len(all_items)
    stats["_total"] = len(all_items)

    return Collection(
        collected_at=now,
        window_hours=config.window_hours,
        items=all_items,
        errors=errors,
        stats=stats,
    )


def save_collection(collection: Collection, out_dir: Path) -> Path:
    """Write the result to out_dir/YYYY-MM-DD.json and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{collection.collected_at.date().isoformat()}.json"
    path.write_text(
        json.dumps(collection.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_collection(path: Path) -> Collection:
    return Collection.from_dict(json.loads(path.read_text(encoding="utf-8")))
