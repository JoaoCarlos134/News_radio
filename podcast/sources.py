"""Registro das fontes RSS da etapa 1.

Fontes validadas no CLAUDE.md. Todas sao feeds publicos e gratuitos; o pipeline
usa apenas manchete + resumo publicado no proprio feed, nunca o texto integral
do artigo.

Para adicionar/remover uma fonte, edite FEED_SOURCES. Use `python -m podcast.cli
sources --check` para verificar quais estao no ar antes de commitar mudancas.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedSource:
    key: str            # identificador estavel, usado em logs e no cache
    name: str           # nome legivel do veiculo, citado no roteiro
    url: str
    categories: tuple[str, ...] = ()
    enabled: bool = True


FEED_SOURCES: tuple[FeedSource, ...] = (
    # --- Economia e mercado -------------------------------------------------
    FeedSource(
        key="infomoney_mercados",
        name="InfoMoney",
        url="https://www.infomoney.com.br/mercados/feed/",
        categories=("economia", "mercado"),
    ),
    FeedSource(
        key="infomoney_economia",
        name="InfoMoney",
        url="https://www.infomoney.com.br/economia/feed/",
        categories=("economia", "brasil"),
    ),
    FeedSource(
        key="infomoney_politica",
        name="InfoMoney",
        url="https://www.infomoney.com.br/politica/feed/",
        categories=("politica", "brasil"),
    ),
    FeedSource(
        key="estadao_economia",
        name="Estadão",
        url="https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/?outputType=xml",
        categories=("economia", "brasil"),
    ),
    FeedSource(
        key="estadao_internacional",
        name="Estadão",
        url="https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/internacional/?outputType=xml",
        categories=("geopolitica", "mundo"),
    ),
    FeedSource(
        key="bbc_brasil",
        name="BBC News Brasil",
        url="https://feeds.bbci.co.uk/portuguese/rss.xml",
        categories=("geopolitica", "economia", "mundo"),
    ),
    FeedSource(
        key="investing_brasil",
        name="Investing.com",
        url="https://br.investing.com/rss/news.rss",
        categories=("mercado", "economia"),
    ),

    # --- Fontes oficiais / institucionais -----------------------------------
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
    FeedSource(
        key="bcb_noticias",
        name="Banco Central do Brasil",
        url="https://www.bcb.gov.br/api/feed/sitebcb/noticias",
        categories=("economia", "brasil", "oficial"),
    ),
    FeedSource(
        key="ibge_noticias",
        name="IBGE",
        url="https://agenciadenoticias.ibge.gov.br/agencia-noticias/2012-agencia-de-noticias/noticias.rss",
        categories=("economia", "brasil", "oficial", "dados"),
    ),
    FeedSource(
        key="fgv_ibre",
        name="FGV IBRE",
        url="https://portalibre.fgv.br/rss/noticias",
        categories=("economia", "brasil", "analise"),
    ),
)


def enabled_sources() -> tuple[FeedSource, ...]:
    return tuple(s for s in FEED_SOURCES if s.enabled)


def source_by_key(key: str) -> FeedSource | None:
    return next((s for s in FEED_SOURCES if s.key == key), None)
