from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.config import CollectConfig
from podcast.sources import FeedSource

FIXTURES = Path(__file__).parent / "fixtures"

# Instante de referencia dos testes. As fixtures tem datas fixas em torno dele,
# entao os testes de janela nao envelhecem.
NOW = datetime(2026, 8, 3, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def collect_config() -> CollectConfig:
    return CollectConfig(
        window_hours=24,
        timeout=5,
        max_items_per_feed=25,
        seen_retention_days=7,
    )


@pytest.fixture
def infomoney_source() -> FeedSource:
    return FeedSource(
        key="infomoney_mercados",
        name="InfoMoney",
        url="https://exemplo.invalido/infomoney.xml",
        categories=("economia", "mercado"),
    )


@pytest.fixture
def agencia_source() -> FeedSource:
    return FeedSource(
        key="agencia_brasil_economia",
        name="Agência Brasil",
        url="https://exemplo.invalido/agencia.xml",
        categories=("economia", "oficial"),
    )


@pytest.fixture
def fake_fetcher():
    """Devolve o XML de fixture correspondente a chave da fonte, sem rede."""
    arquivos = {
        "infomoney_mercados": FIXTURES / "infomoney.xml",
        "agencia_brasil_economia": FIXTURES / "agencia_brasil.xml",
    }

    def fetch(source, timeout):  # noqa: ANN001
        caminho = arquivos.get(source.key)
        if caminho is None:
            raise ConnectionError(f"feed indisponível: {source.key}")
        return caminho.read_bytes()

    return fetch
