"""Registro das fontes RSS da etapa 1.

Todas as URLs abaixo foram verificadas em 2026-08-03 (contagem de itens obtida
de fato). Feeds mudam sem aviso — rode `python -m podcast.cli sources --check`
antes de commitar qualquer mudanca aqui.

O pipeline usa apenas manchete + resumo publicado no proprio feed, nunca o texto
integral do artigo (ver CLAUDE.md, restricao de copyright). Por isso feeds de
veiculos com paywall sao aceitaveis: so consumimos o que o feed publica aberto.

NOTA sobre erros de TLS em rede corporativa: em maquinas atras de um proxy com
inspecao TLS, `sources --check` pode acusar CERTIFICATE_VERIFY_FAILED em varios
feeds que estao perfeitamente no ar. Isso e da rede, nao do feed. Verifique numa
conexao domestica antes de desabilitar qualquer fonte.
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
        # Os feeds por categoria do InfoMoney (/mercados/feed/, /economia/feed/)
        # respondem 200 com um canal RSS valido porem VAZIO. Só o feed principal
        # traz itens. Verificado em 2026-08-03.
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

    # --- Geopolitica e internacional ----------------------------------------
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

    # --- Fontes oficiais / institucionais -----------------------------------
    # A Agência Brasil (EBC) e a fonte oficial que sobreviveu a verificacao. Ela
    # cobre divulgacoes do IBGE e do Banco Central, o que compensa em parte os
    # tres feeds institucionais desabilitados abaixo.
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

    # --- Desabilitadas: verificadas e nao utilizaveis hoje -------------------
    # Mantidas no registro (em vez de apagadas) para nao serem re-tentadas as
    # cegas no futuro. Cada uma diz o motivo.
    FeedSource(
        # O Banco Central nao publica RSS. O endpoint /api/servico/sitebcb/noticias
        # responde 200 mas devolve JSON, nao RSS; /api/feed/sitebcb/noticias da 400.
        # Consumir exigiria um adaptador JSON->NewsItem proprio na etapa 1.
        key="bcb_noticias",
        name="Banco Central do Brasil",
        url="https://www.bcb.gov.br/api/servico/sitebcb/noticias",
        categories=("economia", "brasil", "oficial"),
        enabled=False,
    ),
    FeedSource(
        # Protegido por desafio do Cloudflare: responde 403 com "Just a moment...".
        # Nao ha como consumir com um leitor RSS comum.
        key="ibge_noticias",
        name="IBGE",
        url="https://agenciadenoticias.ibge.gov.br/agencia-noticias/2012-agencia-de-noticias/noticias.rss",
        categories=("economia", "brasil", "oficial", "dados"),
        enabled=False,
    ),
    FeedSource(
        # Handshake TLS falha e a conexao e resetada, em https e http.
        # Pode ser bloqueio do proxy corporativo — revalidar no PC de casa.
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
