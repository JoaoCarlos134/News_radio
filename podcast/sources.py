"""Stage 1 RSS source registry.

Every URL below was verified by actually fetching it and counting items, last
on 2026-09-09. Feeds change without notice -- run
`python -m podcast.cli sources --check` before committing any change here.

The pipeline uses only the headline and the summary the feed itself publishes,
never the full article text (see CLAUDE.md, copyright constraint). That is why
paywalled outlets are acceptable sources: we consume only what the feed serves
openly.

NOTE on TLS errors: behind a proxy that inspects TLS, `sources --check` can
report CERTIFICATE_VERIFY_FAILED for feeds that are perfectly healthy. That is
the network, not the feed. Confirm from an unfiltered connection before
disabling any source on TLS evidence alone.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedSource:
    key: str            # stable identifier, used in logs and in the seen-cache
    name: str           # human-readable outlet name, cited in the script
    url: str
    categories: tuple[str, ...] = ()
    enabled: bool = True


FEED_SOURCES: tuple[FeedSource, ...] = (
    # --- Economy and markets ------------------------------------------------
    FeedSource(
        # InfoMoney's per-category feeds (/mercados/feed/, /economia/feed/)
        # return 200 with a valid but EMPTY RSS channel. Only the main feed
        # carries items. Verified 2026-08-03, still true 2026-09-09.
        key="infomoney",
        name="InfoMoney",
        url="https://www.infomoney.com.br/feed/",
        categories=("economia", "mercado", "brasil"),
    ),
    FeedSource(
        key="estadao_economia",
        name="Estadão",
        url="https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/?outputType=xml",
        categories=("economia", "brasil"),
    ),
    FeedSource(
        key="folha_mercado",
        name="Folha de S.Paulo",
        url="https://feeds.folha.uol.com.br/mercado/rss091.xml",
        categories=("economia", "mercado", "brasil"),
    ),
    FeedSource(
        key="g1_economia",
        name="g1",
        url="https://g1.globo.com/rss/g1/economia/",
        categories=("economia", "brasil"),
    ),
    FeedSource(
        key="exame",
        name="Exame",
        url="https://exame.com/feed/",
        categories=("economia", "mercado", "negocios"),
    ),
    FeedSource(
        key="investing_brasil",
        name="Investing.com",
        url="https://br.investing.com/rss/news.rss",
        categories=("mercado", "economia"),
    ),

    # --- Geopolitics and international ---------------------------------------
    FeedSource(
        key="bbc_brasil",
        name="BBC News Brasil",
        url="https://feeds.bbci.co.uk/portuguese/rss.xml",
        categories=("geopolitica", "mundo", "economia"),
    ),
    FeedSource(
        key="estadao_internacional",
        name="Estadão",
        url="https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/internacional/?outputType=xml",
        categories=("geopolitica", "mundo"),
    ),
    FeedSource(
        key="folha_mundo",
        name="Folha de S.Paulo",
        url="https://feeds.folha.uol.com.br/mundo/rss091.xml",
        categories=("geopolitica", "mundo"),
    ),
    FeedSource(
        key="g1_mundo",
        name="g1",
        url="https://g1.globo.com/rss/g1/mundo/",
        categories=("geopolitica", "mundo"),
    ),

    # --- Official / institutional sources -------------------------------------
    # Agencia Brasil (EBC) is the official source that survived verification. It
    # covers IBGE and Central Bank releases, which partly compensates for the
    # three institutional feeds disabled below.
    FeedSource(
        key="agencia_brasil_economia",
        name="Agência Brasil",
        url="https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
        categories=("economia", "brasil", "oficial"),
    ),
    FeedSource(
        key="agencia_brasil_internacional",
        name="Agência Brasil",
        url="https://agenciabrasil.ebc.com.br/rss/internacional/feed.xml",
        categories=("geopolitica", "mundo", "oficial"),
    ),

    # --- Disabled: verified and not usable ------------------------------------
    # Kept in the registry rather than deleted, so nobody retries them blind in
    # six months. Each one records why. Rechecked 2026-09-09; all three still
    # fail in exactly the way described.
    FeedSource(
        # The Central Bank publishes no RSS. /api/servico/sitebcb/noticias
        # returns 200 but serves JSON, not RSS, so it parses to 0 items;
        # /api/feed/sitebcb/noticias returns 400. Consuming it would need a
        # dedicated JSON->NewsItem adapter in stage 1.
        key="bcb_noticias",
        name="Banco Central do Brasil",
        url="https://www.bcb.gov.br/api/servico/sitebcb/noticias",
        categories=("economia", "brasil", "oficial"),
        enabled=False,
    ),
    FeedSource(
        # Behind a Cloudflare challenge: responds 403 with "Just a moment...".
        # Not consumable by an ordinary RSS reader.
        key="ibge_noticias",
        name="IBGE",
        url="https://agenciadenoticias.ibge.gov.br/agencia-noticias/2012-agencia-de-noticias/noticias.rss",
        categories=("economia", "brasil", "oficial", "dados"),
        enabled=False,
    ),
    FeedSource(
        # TLS handshake fails: SSLV3_ALERT_HANDSHAKE_FAILURE, on https and http.
        # Retested 2026-09-09 from an unfiltered connection and it fails
        # identically, so this is the server's TLS configuration, not a network
        # in the way. Nothing to do at this end.
        key="fgv_ibre",
        name="FGV IBRE",
        url="https://portalibre.fgv.br/rss/noticias",
        categories=("economia", "brasil", "analise"),
        enabled=False,
    ),
)


def enabled_sources() -> tuple[FeedSource, ...]:
    return tuple(s for s in FEED_SOURCES if s.enabled)


def source_by_key(key: str) -> FeedSource | None:
    return next((s for s in FEED_SOURCES if s.key == key), None)
