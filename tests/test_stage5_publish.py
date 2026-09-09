"""Testes da etapa 5.

Nenhum teste aqui toca git nem rede: a publicacao de verdade (commit/push) e
injetada, como o cliente/motor das outras etapas. `build_feed` e testado
isoladamente, so com arquivos locais.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest

from podcast.config import ConfigError, PublishConfig
from podcast.models import Script, ScriptLine
from podcast.stage5_publish import (
    MAX_EPISODES_IN_FEED,
    build_feed,
    check_publish_setup,
    default_pusher,
    publish_episode,
)

NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


@pytest.fixture
def publish_config() -> PublishConfig:
    return PublishConfig(
        base_url="https://exemplo.github.io/meu-podcast",
        title="Economia e Geopolítica — Diário",
        author="João",
        email="voce@exemplo.com",
        language="pt-BR",
    )


@pytest.fixture
def script() -> Script:
    return Script(
        generated_at=datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc),
        episode_date="2026-08-04",
        title="Copom, indústria e a trégua tarifária",
        lines=[ScriptLine(speaker="Maria", text="Bom dia.")],
        themes=["política monetária", "comércio internacional"],
    )


def _make_mp3(public_dir, name: str, size: int = 100) -> None:
    (public_dir / name).write_bytes(b"\0" * size)


def _make_meta(public_dir, episode_date: str, **overrides) -> None:
    meta = {
        "episode_date": episode_date,
        "title": f"Episódio {episode_date}",
        "themes": ["tema a", "tema b"],
        "generated_at": f"{episode_date}T08:00:00+00:00",
        **overrides,
    }
    (public_dir / f"{episode_date}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# build_feed
# --------------------------------------------------------------------------- #

class TestBuildFeed:
    def test_sem_base_url_e_erro(self, tmp_path):
        config = PublishConfig(base_url="", author="J", email="j@ex.com")
        with pytest.raises(ConfigError, match="PODCAST_BASE_URL"):
            build_feed(config, tmp_path)

    def test_cria_a_pasta_publicada(self, publish_config, tmp_path):
        destino = tmp_path / "public" / "nova"
        build_feed(publish_config, destino)
        assert destino.exists()

    def test_feed_vazio_sem_mp3(self, publish_config, tmp_path):
        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        assert raiz.find("./channel/item") is None

    def test_um_episodio_por_mp3(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-04.mp3")
        _make_meta(tmp_path, "2026-08-04")
        _make_mp3(tmp_path, "2026-08-03.mp3")
        _make_meta(tmp_path, "2026-08-03")

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        assert len(raiz.findall("./channel/item")) == 2

    def test_mais_recente_primeiro(self, publish_config, tmp_path):
        for data in ("2026-08-01", "2026-08-03", "2026-08-02"):
            _make_mp3(tmp_path, f"{data}.mp3")
            _make_meta(tmp_path, data)

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        titulos = [item.find("title").text for item in raiz.findall("./channel/item")]
        assert titulos == ["Episódio 2026-08-03", "Episódio 2026-08-02", "Episódio 2026-08-01"]

    def test_enclosure_aponta_para_url_absoluta_do_base_url(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-04.mp3", size=12345)
        _make_meta(tmp_path, "2026-08-04")

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        enclosure = raiz.find("./channel/item/enclosure")
        assert enclosure.get("url") == f"{publish_config.base_url}/2026-08-04.mp3"
        assert enclosure.get("length") == "12345"
        assert enclosure.get("type") == "audio/mpeg"

    def test_mp3_sem_sidecar_ainda_entra_no_feed(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-04.mp3")  # sem .json correspondente

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        item = raiz.find("./channel/item")
        assert item is not None
        assert "2026-08-04" in item.find("title").text

    def test_sidecar_com_json_invalido_nao_quebra_o_feed(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-04.mp3")
        (tmp_path / "2026-08-04.json").write_text("{ nao e json valido", encoding="utf-8")

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        item = raiz.find("./channel/item")
        assert item is not None
        assert "2026-08-04" in item.find("title").text

    def test_sidecar_sem_title_nao_quebra_o_feed(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-04.mp3")
        (tmp_path / "2026-08-04.json").write_text(
            json.dumps({"episode_date": "2026-08-04"}), encoding="utf-8",
        )

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        item = raiz.find("./channel/item")
        assert item is not None
        assert "2026-08-04" in item.find("title").text

    def test_um_sidecar_ruim_nao_derruba_os_outros_episodios(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-03.mp3")
        _make_meta(tmp_path, "2026-08-03")
        _make_mp3(tmp_path, "2026-08-04.mp3")
        (tmp_path / "2026-08-04.json").write_text("{ ruim", encoding="utf-8")

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        assert len(raiz.findall("./channel/item")) == 2

    def test_owner_so_entra_com_author_e_email_juntos(self, tmp_path):
        # feedgen exige os dois ou nenhum — so um dos dois preenchidos nao
        # pode virar excecao na hora de montar o feed.
        config = PublishConfig(
            base_url="https://ex.github.io/repo", author="João", email="",
        )
        feed_path = build_feed(config, tmp_path)
        assert feed_path.exists()

    def test_mantem_so_os_mais_recentes_e_apaga_o_resto(self, publish_config, tmp_path):
        from datetime import date, timedelta

        total = MAX_EPISODES_IN_FEED + 3
        datas = [(date(2026, 1, 1) + timedelta(days=n)).isoformat() for n in range(total)]
        for data in datas:
            _make_mp3(tmp_path, f"{data}.mp3")
            _make_meta(tmp_path, data)

        build_feed(publish_config, tmp_path)

        mp3s_restantes = list(tmp_path.glob("*.mp3"))
        jsons_restantes = list(tmp_path.glob("*.json"))
        assert len(mp3s_restantes) == MAX_EPISODES_IN_FEED
        assert len(jsons_restantes) == MAX_EPISODES_IN_FEED
        # os mais antigos foram os apagados; os mais recentes permanecem
        assert not (tmp_path / f"{datas[0]}.mp3").exists()
        assert (tmp_path / f"{datas[-1]}.mp3").exists()

    def test_temas_aparecem_na_descricao(self, publish_config, tmp_path):
        _make_mp3(tmp_path, "2026-08-04.mp3")
        _make_meta(tmp_path, "2026-08-04", themes=["Selic", "Ibovespa"])

        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        descricao = raiz.find("./channel/item/description").text
        assert "Selic" in descricao and "Ibovespa" in descricao

    def test_metadados_do_canal(self, publish_config, tmp_path):
        feed_path = build_feed(publish_config, tmp_path)
        raiz = ET.parse(feed_path).getroot()
        assert raiz.find("./channel/title").text == publish_config.title
        assert raiz.find("./channel/language").text == publish_config.language
        assert raiz.find("./channel/itunes:author", NS).text == publish_config.author


# --------------------------------------------------------------------------- #
# publish_episode
# --------------------------------------------------------------------------- #

class TestPublishEpisode:
    def test_copia_o_mp3_com_o_nome_do_episodio(self, script, publish_config, tmp_path):
        origem = tmp_path / "origem.mp3"
        origem.write_bytes(b"audio de verdade")
        public_dir = tmp_path / "public"

        chamadas = []
        publish_episode(
            script, origem, publish_config, public_dir,
            pusher=lambda pd, msg: chamadas.append((pd, msg)),
        )

        destino = public_dir / "2026-08-04.mp3"
        assert destino.read_bytes() == b"audio de verdade"
        assert origem.exists()  # copia, nao move — o arquivo original permanece

    def test_grava_o_sidecar_com_titulo_e_temas(self, script, publish_config, tmp_path):
        origem = tmp_path / "origem.mp3"
        origem.write_bytes(b"x")
        public_dir = tmp_path / "public"

        publish_episode(script, origem, publish_config, public_dir, pusher=lambda pd, msg: None)

        meta = json.loads((public_dir / "2026-08-04.json").read_text(encoding="utf-8"))
        assert meta["title"] == script.title
        assert meta["themes"] == script.themes

    def test_regenera_o_feed(self, script, publish_config, tmp_path):
        origem = tmp_path / "origem.mp3"
        origem.write_bytes(b"x")
        public_dir = tmp_path / "public"

        feed_path = publish_episode(
            script, origem, publish_config, public_dir, pusher=lambda pd, msg: None,
        )

        assert feed_path == public_dir / "feed.xml"
        assert feed_path.exists()

    def test_chama_o_pusher_com_a_pasta_e_uma_mensagem_com_a_data(
        self, script, publish_config, tmp_path,
    ):
        origem = tmp_path / "origem.mp3"
        origem.write_bytes(b"x")
        public_dir = tmp_path / "public"

        chamadas = []
        publish_episode(
            script, origem, publish_config, public_dir,
            pusher=lambda pd, msg: chamadas.append((pd, msg)),
        )

        assert len(chamadas) == 1
        pasta, mensagem = chamadas[0]
        assert pasta == public_dir
        assert "2026-08-04" in mensagem


# --------------------------------------------------------------------------- #
# default_pusher
# --------------------------------------------------------------------------- #

class TestDefaultPusher:
    def test_sem_checkout_git_e_erro(self, tmp_path):
        # tmp_path nao tem .git — nao pode ser uma branch do GitHub Pages.
        with pytest.raises(ConfigError, match="checkout git"):
            default_pusher(tmp_path, "mensagem")


# --------------------------------------------------------------------------- #
# check_publish_setup
# --------------------------------------------------------------------------- #

class TestCheckPublishSetup:
    def test_tudo_preenchido(self, publish_config):
        ok, msg = check_publish_setup(publish_config)
        assert ok
        assert publish_config.base_url in msg

    def test_variaveis_faltando(self):
        ok, msg = check_publish_setup(PublishConfig())
        assert not ok
        assert "PODCAST_BASE_URL" in msg

    def test_base_url_relativa_e_erro(self):
        config = PublishConfig(base_url="exemplo.github.io/meu-podcast", author="J", email="j@ex.com")
        ok, msg = check_publish_setup(config)
        assert not ok
        assert "absolute" in msg
