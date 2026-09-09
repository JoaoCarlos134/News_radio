"""Text and URL normalisation. Pure functions -- easy to test without a network.

The stopword list and the boilerplate patterns below are Portuguese-language
data matched against Portuguese feeds, and stay in Portuguese.
"""

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
        # We do not want these tags' text content in the summary.
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
    """Strip HTML tags and normalise whitespace.

    RSS summaries almost always arrive with HTML (links, <p>, entities). The
    local model gains nothing from it and the tags cost tokens.
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
    # Normalise whitespace, including NBSP and friends.
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


# Invisible characters that several CMSs inject into text. They cost tokens and
# can disrupt both title comparison and the TTS reading.
_INVISIVEIS = str.maketrans("", "", "​‌‍⁠﻿­")

# Boilerplate footers observed in the real feeds (collection of 2026-08-03).
_BOILERPLATE = (
    # WordPress: "The post <title> appeared first on InfoMoney."
    re.compile(r"\s*The post\b.*?\bappeared first on\b.*$", re.IGNORECASE | re.DOTALL),
    # Folha: "Leia mais (08/03/2026 - 09h53)"
    re.compile(r"\s*Leia mais\s*\([^)]*\)\s*$", re.IGNORECASE),
    # Generic "continue reading" calls at the end of a summary
    re.compile(r"\s*(Continue lendo|Leia a matéria completa|Saiba mais)\s*[.…]*\s*$",
               re.IGNORECASE),
)


def clean_summary(text: str) -> str:
    """Strip CMS boilerplate and invisible characters from a summary.

    The summary goes straight into stage 3's prompt; a footer repeated across
    dozens of items is paid tokens with no analytical value.
    """
    if not text:
        return ""
    text = text.translate(_INVISIVEIS)
    for padrao in _BOILERPLATE:
        text = padrao.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, max_chars: int) -> str:
    """Cut at a word boundary, never mid-word."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return f"{cut}…"


# Tracking parameters that change the URL without changing the article.
_TRACKING_PARAMS = re.compile(
    r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|xtor$|ref$|origem$|__twitter)",
    re.IGNORECASE,
)


def unwrap_redirect(url: str) -> str:
    """Extract the real URL from a redirect wrapper.

    Folha publishes links like
        https://redir.folha.com.br/redir/online/mercado/rss091/*https://...
    Without unwrapping, two Folha feeds pointing at the same article through
    different redirect paths escape URL deduplication.
    """
    marcador = url.rfind("*http")
    return url[marcador + 1:] if marcador != -1 else url


def canonical_url(url: str) -> str:
    """A comparable URL: no redirect wrapper, no fragment, no tracking.

    Used both to generate the item's stable id and to deduplicate the same
    article arriving through different feeds of one outlet.
    """
    if not url:
        return ""
    parts = urlsplit(unwrap_redirect(url.strip()))
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
    """A title reduced to a comparable form: unaccented, unpunctuated, lowercase."""
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Short/common Portuguese words that do not help distinguish headlines.
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
    """Jaccard similarity between two titles' sets of significant words.

    Detects the same story published under slightly different headlines by
    different outlets, which is the common duplicate case in this pipeline.
    """
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def stable_id(*parts: str) -> str:
    """A short, deterministic id. Deterministic matters: the cross-run
    seen-cache depends on one article always producing the same id."""
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
