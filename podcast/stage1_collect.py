"""Etapa 1 — coleta RSS. IMPLEMENTADA.

Nao depende de GPU nem de API paga: roda e e testavel em qualquer maquina.

Fluxo:
    baixa cada feed -> parseia -> normaliza -> filtra pela janela de tempo
    -> descarta itens ja vistos em execucoes anteriores -> deduplica
    -> ordena por recencia -> grava JSON

Erro em um feed nunca derruba a coleta: e registrado em `Collection.errors` e o
resto continua. Um veiculo fora do ar nao pode custar o episodio do dia.
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
    "Mozilla/5.0 (compatible; PodcastDiarioBot/0.1; uso pessoal, nao comercial)"
)

# Teto do resumo por item. Manchete + resumo curto e o suficiente para a etapa 2
# triar, e mantem o contexto do modelo local dentro dos 12GB de VRAM.
MAX_SUMMARY_CHARS = 600

# Acima disto dois titulos sao considerados a mesma noticia.
DUPLICATE_TITLE_THRESHOLD = 0.75


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

def fetch_feed(source: FeedSource, timeout: int) -> str:
    """Baixa o XML cru de um feed. Levanta requests.RequestException em falha."""
    response = requests.get(
        source.url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    response.raise_for_status()
    # feedparser lida melhor com bytes: respeita o encoding declarado no XML,
    # que nem sempre bate com o header HTTP.
    return response.content


# --------------------------------------------------------------------------- #
# Parsing (puro — sem rede, testado com fixtures locais)
# --------------------------------------------------------------------------- #

def _entry_datetime(entry) -> datetime | None:  # noqa: ANN001
    """Data de publicacao em UTC. feedparser ja converte para struct_time UTC."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _entry_summary(entry) -> str:  # noqa: ANN001
    """Resumo publicado pelo feed, limpo de HTML.

    Preferimos `summary` a `content`: `content` costuma trazer o artigo inteiro,
    que nao devemos reproduzir (ver CLAUDE.md, restricao de copyright).
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
    # dict.fromkeys preserva ordem e remove repetidos
    return tuple(dict.fromkeys(source.categories + from_feed))


def parse_feed(source: FeedSource, raw: bytes | str) -> list[NewsItem]:
    """Converte o XML de um feed em NewsItem normalizados.

    Funcao pura: recebe bytes, devolve itens. E o ponto de teste da etapa.
    Entradas sem titulo ou sem link sao descartadas — nao ha o que sintetizar.
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
                # O id vem da URL canonica: o mesmo artigo em dois feeds do mesmo
                # veiculo colapsa em um id so, e ele sobrevive entre execucoes.
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
# Filtros e deduplicacao (puros)
# --------------------------------------------------------------------------- #

def filter_by_window(
    items: list[NewsItem],
    window_hours: int,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Mantem apenas itens dentro da janela.

    Itens sem data sao mantidos: varios feeds oficiais (BCB, IBGE) omitem
    pubDate, e descarta-los perderia justamente as fontes primarias. O cache de
    'ja visto' evita que virem repeticao no dia seguinte.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    # Tolerancia para feeds com relogio adiantado.
    horizon = now + timedelta(hours=6)
    return [
        item for item in items
        if item.published_at is None or cutoff <= item.published_at <= horizon
    ]


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Remove duplicatas por id/URL e por titulo semelhante.

    Mantem a primeira ocorrencia. Como os itens chegam ordenados por recencia,
    a versao preservada e a mais recente.
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
    """Mais recentes primeiro; itens sem data vao para o fim."""
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(items, key=lambda i: i.published_at or epoch, reverse=True)


# --------------------------------------------------------------------------- #
# Cache de itens ja processados
# --------------------------------------------------------------------------- #

class SeenStore:
    """Registra ids ja usados em episodios anteriores.

    Sem isso, uma noticia publicada as 23h entra no episodio de hoje e de novo
    no de amanha, porque continua dentro da janela de 24h.
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
                # Cache corrompido nao pode impedir a coleta: recomeca vazio.
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
# Orquestracao
# --------------------------------------------------------------------------- #

def collect(
    config: CollectConfig,
    sources: tuple[FeedSource, ...] | None = None,
    seen: SeenStore | None = None,
    now: datetime | None = None,
    fetcher=fetch_feed,  # noqa: ANN001 — injetavel nos testes
) -> Collection:
    """Executa a etapa 1 completa.

    `fetcher` e injetavel para que os testes rodem sem rede.
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
        except Exception as exc:  # rede, DNS, HTTP 4xx/5xx, timeout...
            log.warning("feed %s falhou: %s", source.key, exc)
            errors.append(FeedError(source_key=source.key, message=str(exc)))
            stats[source.key] = 0
            continue

        try:
            items = parse_feed(source, raw)
        except Exception as exc:  # XML irrecuperavel
            log.warning("feed %s nao pode ser parseado: %s", source.key, exc)
            errors.append(FeedError(source_key=source.key, message=f"parse: {exc}"))
            stats[source.key] = 0
            continue

        items = filter_by_window(items, config.window_hours, now=now)
        items = sort_items(items)[: config.max_items_per_feed]

        stats[source.key] = len(items)
        all_items.extend(items)
        log.info(
            "feed %s: %d itens em %.1fs", source.key, len(items),
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
    """Grava o resultado em out_dir/YYYY-MM-DD.json e devolve o caminho."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{collection.collected_at.date().isoformat()}.json"
    path.write_text(
        json.dumps(collection.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_collection(path: Path) -> Collection:
    return Collection.from_dict(json.loads(path.read_text(encoding="utf-8")))
