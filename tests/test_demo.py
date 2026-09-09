"""Testes do comando `demo`.

O ponto do demo e ser a unica coisa que um clone novo consegue rodar: sem chave,
sem GPU, sem rede. O teste existe para que isso continue verdade — se alguem
fizer o demo depender de credencial ou de servico externo, aqui quebra.
"""

from __future__ import annotations

import argparse
from xml.etree import ElementTree as ET

from podcast.cli import cmd_demo
from podcast.config import load_config


def _config_isolado(monkeypatch, tmp_path):
    """Config apontando para um data_dir temporario, sem .env da maquina."""
    for chave in ("ANTHROPIC_API_KEY", "PODCAST_BASE_URL", "KOKORO_VOICE_MARIA",
                  "KOKORO_VOICE_PEDRO"):
        monkeypatch.delenv(chave, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return load_config(env_file=tmp_path / "nao-existe.env")


class TestDemo:
    def test_roda_sem_chave_sem_gpu_sem_rede(self, monkeypatch, tmp_path):
        config = _config_isolado(monkeypatch, tmp_path)
        assert config.script.api_key == ""  # o demo nao pode depender disso

        assert cmd_demo(config, argparse.Namespace()) == 0

    def test_gera_um_feed_rss_de_verdade(self, monkeypatch, tmp_path):
        config = _config_isolado(monkeypatch, tmp_path)
        cmd_demo(config, argparse.Namespace())

        feed = tmp_path / "demo" / "feed.xml"
        assert feed.exists()

        raiz = ET.parse(feed).getroot()
        itens = raiz.findall("./channel/item")
        assert len(itens) == 1
        # Sem <enclosure> absoluto o app Podcasts nao baixa o arquivo — e o
        # detalhe que o demo precisa provar que a etapa 5 acerta.
        enclosure = itens[0].find("enclosure")
        assert enclosure is not None
        assert enclosure.get("url", "").startswith("https://")
        assert enclosure.get("type") == "audio/mpeg"

    def test_copia_o_mp3_para_a_pasta_publicada(self, monkeypatch, tmp_path):
        config = _config_isolado(monkeypatch, tmp_path)
        cmd_demo(config, argparse.Namespace())

        mp3 = list((tmp_path / "demo").glob("*.mp3"))
        assert len(mp3) == 1
        assert mp3[0].stat().st_size > 0

    def test_nao_publica_nada(self, monkeypatch, tmp_path):
        """O pusher do demo e um no-op: nenhuma pasta git aparece."""
        config = _config_isolado(monkeypatch, tmp_path)
        cmd_demo(config, argparse.Namespace())

        assert not (tmp_path / "demo" / ".git").exists()
