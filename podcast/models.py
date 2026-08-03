"""Modelos de dados trocados entre as etapas do pipeline.

Cada etapa le um JSON produzido pela anterior e escreve o proprio. Isso permite
rodar/testar etapas isoladamente e inspecionar o resultado intermediario — util
porque as etapas 2 e 4 so rodam no PC com GPU.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Etapa 1 — coleta
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NewsItem:
    """Uma noticia bruta vinda de um feed RSS.

    `summary` e sempre o resumo/manchete publicado pelo proprio feed — nunca o
    texto integral do artigo. O pipeline sintetiza a partir daqui (ver CLAUDE.md,
    restricao de copyright).
    """

    id: str
    source_key: str
    source_name: str
    title: str
    summary: str
    link: str
    published_at: datetime | None = None
    categories: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_at"] = _iso(self.published_at)
        data["categories"] = list(self.categories)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewsItem:
        return cls(
            id=data["id"],
            source_key=data["source_key"],
            source_name=data["source_name"],
            title=data["title"],
            summary=data.get("summary", ""),
            link=data["link"],
            published_at=_parse_iso(data.get("published_at")),
            categories=tuple(data.get("categories") or ()),
        )


@dataclass(frozen=True)
class FeedError:
    """Falha em um feed especifico. Nunca aborta a coleta inteira."""

    source_key: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Collection:
    """Resultado completo da etapa 1."""

    collected_at: datetime
    window_hours: int
    items: list[NewsItem] = field(default_factory=list)
    errors: list[FeedError] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": _iso(self.collected_at),
            "window_hours": self.window_hours,
            "stats": self.stats,
            "errors": [e.to_dict() for e in self.errors],
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Collection:
        return cls(
            collected_at=_parse_iso(data["collected_at"]) or datetime.now(timezone.utc),
            window_hours=data.get("window_hours", 24),
            items=[NewsItem.from_dict(i) for i in data.get("items", [])],
            errors=[FeedError(**e) for e in data.get("errors", [])],
            stats=data.get("stats", {}),
        )


# --------------------------------------------------------------------------- #
# Etapa 2 — resumo/triagem local (Ollama)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SummarizedItem:
    """Noticia depois da limpeza/triagem do modelo local."""

    item_id: str
    title: str
    summary: str
    source_name: str
    link: str
    theme: str = ""
    relevance: int = 0  # 0-10, atribuido pelo modelo local

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SummarizedItem:
        return cls(**data)


@dataclass
class Digest:
    """Resultado da etapa 2: itens filtrados + temas do dia."""

    generated_at: datetime
    themes: list[str] = field(default_factory=list)
    items: list[SummarizedItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": _iso(self.generated_at),
            "themes": self.themes,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Digest:
        return cls(
            generated_at=_parse_iso(data["generated_at"]) or datetime.now(timezone.utc),
            themes=list(data.get("themes", [])),
            items=[SummarizedItem.from_dict(i) for i in data.get("items", [])],
        )


# --------------------------------------------------------------------------- #
# Etapa 3 — roteiro
# --------------------------------------------------------------------------- #

SPEAKERS = ("Maria", "Pedro")


@dataclass(frozen=True)
class ScriptLine:
    """Uma fala do dialogo. `speaker` deve estar em SPEAKERS."""

    speaker: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Script:
    """Roteiro completo do episodio."""

    generated_at: datetime
    episode_date: str  # YYYY-MM-DD, no fuso do usuario
    title: str
    lines: list[ScriptLine] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    model: str = ""

    @property
    def word_count(self) -> int:
        return sum(len(line.text.split()) for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": _iso(self.generated_at),
            "episode_date": self.episode_date,
            "title": self.title,
            "themes": self.themes,
            "model": self.model,
            "word_count": self.word_count,
            "lines": [line.to_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Script:
        return cls(
            generated_at=_parse_iso(data["generated_at"]) or datetime.now(timezone.utc),
            episode_date=data["episode_date"],
            title=data["title"],
            lines=[ScriptLine(**line) for line in data.get("lines", [])],
            themes=list(data.get("themes", [])),
            model=data.get("model", ""),
        )
