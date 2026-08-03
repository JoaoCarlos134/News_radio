"""Normalizacao de texto e URL. Funcoes puras — faceis de testar sem rede."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        # Nao queremos o conteudo textual destas tags no resumo.
        self._skip_depth = 0

    _SKIP_TAGS = {"script", "style"}

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in ("p", "br", "div", "li"):
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(raw: str | None) -> str:
    """Remove tags HTML e normaliza espacos.

    Resumos de RSS quase sempre vem com HTML (links, <p>, entidades). O modelo
    local nao ganha nada com isso e as tags gastam tokens.
    """
    if not raw:
        return ""
    parser = _TagStripper()
    try:
        parser.feed(raw)
        parser.close()
        text = parser.text()
    except Exception:  # HTML muito quebrado — cai para regex simples
        text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text)
    # Normaliza espacos, incluindo NBSP e afins.
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, max_chars: int) -> str:
    """Corta em limite de palavra, sem cortar no meio de uma."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return f"{cut}…"


# Parametros de rastreamento que mudam a URL sem mudar o artigo.
_TRACKING_PARAMS = re.compile(
    r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|xtor$|ref$|origem$|__twitter)",
    re.IGNORECASE,
)


def canonical_url(url: str) -> str:
    """URL comparavel: sem fragmento, sem parametros de rastreamento.

    Usada tanto para gerar o id estavel do item quanto para deduplicar o mesmo
    artigo chegando por feeds diferentes do mesmo veiculo.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        urlencode(query),
        "",  # fragmento descartado
    ))


def normalize_title(title: str) -> str:
    """Titulo reduzido a uma forma comparavel: sem acento, sem pontuacao, minusculo."""
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Palavras curtas/comuns que nao ajudam a distinguir manchetes.
_STOPWORDS = frozenset("""
a ao aos as com como da das de do dos e em entre na nas no nos o os ou para
pela pelas pelo pelos por que se sem sob sobre um uma uns umas apos ate
""".split())


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(
        w for w in normalize_title(title).split()
        if len(w) > 2 and w not in _STOPWORDS
    )


def title_similarity(a: str, b: str) -> float:
    """Jaccard entre os conjuntos de palavras significativas de dois titulos.

    Detecta a mesma noticia publicada com manchetes ligeiramente diferentes por
    veiculos distintos, que e o caso comum de duplicata neste pipeline.
    """
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def stable_id(*parts: str) -> str:
    """Id determinista e curto. Determinista importa: o cache de 'ja visto'
    entre execucoes depende de o mesmo artigo gerar sempre o mesmo id."""
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
