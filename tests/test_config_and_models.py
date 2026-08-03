"""Testes de configuracao e serializacao.

Cobrem duas garantias do projeto: (1) nenhum caminho absoluto desta maquina
vaza para o codigo; (2) o JSON entre etapas sobrevive a ida e volta.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.config import PROJECT_ROOT, ConfigError, load_config
from podcast.models import (
    Collection,
    Digest,
    FeedError,
    NewsItem,
    Script,
    ScriptLine,
    SummarizedItem,
)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Isola os testes do .env real e das variaveis da maquina."""
    for chave in (
        "DATA_DIR", "ANTHROPIC_API_KEY", "SCRIPT_MODEL", "SCRIPT_MAX_TOKENS",
        "FEED_WINDOW_HOURS", "OLLAMA_BASE_URL", "KOKORO_MODEL_PATH",
        "PODCAST_BASE_URL", "SCRIPT_EFFORT",
    ):
        monkeypatch.delenv(chave, raising=False)


class TestLoadConfig:
    def test_padroes(self, tmp_path):
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.collect.window_hours == 24
        assert config.script.model == "claude-sonnet-5"
        assert config.ollama.base_url == "http://localhost:11434"

    def test_le_variaveis_de_ambiente(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FEED_WINDOW_HOURS", "12")
        monkeypatch.setenv("SCRIPT_MODEL", "claude-haiku-4-5")
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.collect.window_hours == 12
        assert config.script.model == "claude-haiku-4-5"

    def test_caminho_relativo_resolve_a_partir_da_raiz_do_repo(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", "./dados-teste")
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.data_dir == (PROJECT_ROOT / "dados-teste").resolve()

    def test_caminho_absoluto_e_respeitado(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "abs"))
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.data_dir == tmp_path / "abs"

    def test_valor_nao_numerico_da_erro_util(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FEED_WINDOW_HOURS", "vinte e quatro")
        with pytest.raises(ConfigError, match="FEED_WINDOW_HOURS"):
            load_config(env_file=tmp_path / "nao-existe.env")

    def test_sem_chave_de_api_ainda_carrega(self, tmp_path):
        # A etapa 1 tem de rodar sem nenhuma credencial configurada.
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.script.api_key == ""

    def test_chave_de_api_ausente_so_falha_ao_ser_exigida(self, tmp_path):
        config = load_config(env_file=tmp_path / "nao-existe.env")
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
            config.script.require_api_key()

    def test_subpastas_derivam_de_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.raw_dir == tmp_path / "raw"
        assert config.scripts_dir == tmp_path / "scripts"
        assert config.seen_store_path == tmp_path / "seen.json"

    def test_variavel_vazia_cai_no_padrao(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SCRIPT_EFFORT", "   ")
        config = load_config(env_file=tmp_path / "nao-existe.env")
        assert config.script.effort == "high"


class TestSerializacao:
    def test_news_item_ida_e_volta(self):
        original = NewsItem(
            id="abc", source_key="k", source_name="Fonte", title="Título com acento",
            summary="Resumo", link="https://ex.com/a",
            published_at=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
            categories=("economia", "brasil"),
        )
        volta = NewsItem.from_dict(original.to_dict())
        assert volta == original

    def test_news_item_sem_data(self):
        original = NewsItem("a", "k", "F", "T", "", "https://ex.com/a", None)
        assert NewsItem.from_dict(original.to_dict()).published_at is None

    def test_collection_ida_e_volta(self):
        original = Collection(
            collected_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            window_hours=24,
            items=[NewsItem("a", "k", "F", "T", "S", "https://ex.com/a")],
            errors=[FeedError("fonte_x", "timeout")],
            stats={"_total": 1},
        )
        volta = Collection.from_dict(original.to_dict())
        assert len(volta.items) == 1
        assert volta.errors[0].source_key == "fonte_x"
        assert volta.stats["_total"] == 1

    def test_digest_ida_e_volta(self):
        original = Digest(
            generated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            themes=["juros"],
            items=[SummarizedItem("a", "T", "S", "Fonte", "https://ex.com/a", "juros", 8)],
        )
        volta = Digest.from_dict(original.to_dict())
        assert volta.themes == ["juros"]
        assert volta.items[0].relevance == 8

    def test_script_ida_e_volta(self):
        original = Script(
            generated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            episode_date="2026-08-03",
            title="Título",
            lines=[ScriptLine("Maria", "Bom dia."), ScriptLine("Pedro", "Bom dia, Maria.")],
            themes=["juros"],
            model="claude-sonnet-5",
        )
        volta = Script.from_dict(original.to_dict())
        assert volta.title == "Título"
        assert len(volta.lines) == 2
        assert volta.model == "claude-sonnet-5"

    def test_contagem_de_palavras(self):
        script = Script(
            generated_at=datetime.now(timezone.utc),
            episode_date="2026-08-03", title="T",
            lines=[ScriptLine("Maria", "uma duas tres"), ScriptLine("Pedro", "quatro cinco")],
        )
        assert script.word_count == 5


class TestSources:
    def test_todas_as_chaves_sao_unicas(self):
        from podcast.sources import FEED_SOURCES

        chaves = [s.key for s in FEED_SOURCES]
        assert len(chaves) == len(set(chaves))

    def test_todas_as_urls_sao_absolutas(self):
        from podcast.sources import FEED_SOURCES

        assert all(s.url.startswith("https://") for s in FEED_SOURCES)

    def test_busca_por_chave(self):
        from podcast.sources import source_by_key

        assert source_by_key("infomoney_mercados") is not None
        assert source_by_key("nao_existe") is None


class TestSemCaminhosAbsolutosNoCodigo:
    """Garante o requisito do projeto: clonar no PC de destino tem de funcionar
    sem editar codigo. Caminho absoluto desta maquina no fonte quebraria isso."""

    def test_nenhum_caminho_de_maquina_no_pacote(self):
        pacote = PROJECT_ROOT / "podcast"
        suspeitos = ("C:\\Users", "/home/", "/Users/", "OneDrive")

        for arquivo in pacote.rglob("*.py"):
            conteudo = arquivo.read_text(encoding="utf-8")
            for padrao in suspeitos:
                assert padrao not in conteudo, (
                    f"{arquivo.name} contém o caminho absoluto {padrao!r}"
                )
