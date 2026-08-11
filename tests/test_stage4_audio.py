"""Testes da etapa 4.

O planejamento das falas e funcao pura e roda em qualquer maquina. Os testes de
sintese propriamente dita precisam de numpy e pydub (requirements-audio.txt) e
se auto-pulam onde eles nao estao instalados — que e exatamente a maquina sem
GPU. Nenhum teste aqui carrega o modelo Kokoro: o motor e injetado.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from podcast.config import AudioConfig
from podcast.models import Script, ScriptLine
from podcast.stage4_audio import (
    KOKORO_LANG,
    MAX_CHARS_POR_TRECHO,
    plan_segments,
    split_for_tts,
    synthesize,
    voice_for,
)


@pytest.fixture
def audio_config(tmp_path) -> AudioConfig:
    return AudioConfig(
        model_path=tmp_path / "kokoro.onnx",
        voices_path=tmp_path / "voices.bin",
        voice_maria="pf_dora",
        voice_pedro="pm_alex",
        speed=1.0,
        gap_ms=350,
    )


@pytest.fixture
def script() -> Script:
    return Script(
        generated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        episode_date="2026-08-04",
        title="Copom, indústria e a trégua tarifária",
        lines=[
            ScriptLine(speaker="Maria", text="Bom dia. Hoje começamos em Brasília."),
            ScriptLine(speaker="Pedro", text="O Copom manteve a Selic em quinze por cento."),
            ScriptLine(speaker="Maria", text="É isso por hoje. Até amanhã."),
        ],
        themes=["política monetária"],
    )


# --------------------------------------------------------------------------- #
# voice_for
# --------------------------------------------------------------------------- #

class TestVoiceFor:
    def test_maria_e_pedro(self, audio_config):
        assert voice_for("Maria", audio_config) == "pf_dora"
        assert voice_for("Pedro", audio_config) == "pm_alex"

    def test_locutor_desconhecido_e_erro(self, audio_config):
        # Fallback silencioso geraria um episodio inteiro na voz errada.
        with pytest.raises(ValueError, match="sem voz configurada"):
            voice_for("Carlos", audio_config)


# --------------------------------------------------------------------------- #
# split_for_tts
# --------------------------------------------------------------------------- #

class TestSplitForTts:
    def test_fala_curta_nao_e_quebrada(self):
        assert split_for_tts("Bom dia. Tudo certo?") == ["Bom dia. Tudo certo?"]

    def test_texto_vazio_vira_lista_vazia(self):
        assert split_for_tts("   ") == []

    def test_normaliza_espacos(self):
        assert split_for_tts("Bom  dia.\n Tudo certo?") == ["Bom dia. Tudo certo?"]

    def test_respeita_o_limite(self):
        texto = " ".join(["Uma frase de tamanho razoável aqui."] * 40)
        for trecho in split_for_tts(texto):
            assert len(trecho) <= MAX_CHARS_POR_TRECHO

    def test_agrupa_frases_ate_o_limite(self):
        # Quebrar mais que o necessario poria respiro artificial no meio da fala.
        texto = " ".join(["Frase curta."] * 20)
        trechos = split_for_tts(texto, max_chars=100)
        assert len(trechos) < 20
        assert all(len(t) <= 100 for t in trechos)

    def test_pontuacao_fica_no_trecho_anterior(self):
        trechos = split_for_tts("Primeira frase aqui. Segunda frase aqui.", max_chars=25)
        assert trechos[0].endswith(".")

    def test_frase_unica_gigante_e_quebrada_por_palavra(self):
        texto = "palavra " * 200  # sem nenhuma pontuacao
        trechos = split_for_tts(texto)
        assert len(trechos) > 1
        assert all(len(t) <= MAX_CHARS_POR_TRECHO for t in trechos)

    def test_nada_se_perde_na_quebra(self):
        texto = " ".join(f"Frase número {n} do teste." for n in range(30))
        assert " ".join(split_for_tts(texto, max_chars=80)) == texto


# --------------------------------------------------------------------------- #
# plan_segments
# --------------------------------------------------------------------------- #

class TestPlanSegments:
    def test_uma_entrada_por_fala_curta(self, script, audio_config):
        plano = plan_segments(script, audio_config)
        assert len(plano) == 3

    def test_alterna_as_vozes_na_ordem_do_roteiro(self, script, audio_config):
        vozes = [voz for voz, _, _ in plan_segments(script, audio_config)]
        assert vozes == ["pf_dora", "pm_alex", "pf_dora"]

    def test_fala_longa_vira_varios_trechos_da_mesma_voz(self, audio_config):
        longa = Script(
            generated_at=datetime.now(timezone.utc),
            episode_date="2026-08-04",
            title="T",
            lines=[ScriptLine(speaker="Pedro", text="Uma frase completa aqui. " * 40)],
        )
        plano = plan_segments(longa, audio_config)
        assert len(plano) > 1
        assert {voz for voz, _, _ in plano} == {"pm_alex"}

    def test_so_o_primeiro_trecho_da_fala_marca_pausa(self, audio_config):
        # Pausa dentro de uma mesma fala soaria como hesitacao do locutor.
        longa = Script(
            generated_at=datetime.now(timezone.utc),
            episode_date="2026-08-04",
            title="T",
            lines=[ScriptLine(speaker="Pedro", text="Uma frase completa aqui. " * 40)],
        )
        marcas = [comeca for _, _, comeca in plan_segments(longa, audio_config)]
        assert marcas[0] is True
        assert not any(marcas[1:])

    def test_roteiro_sem_texto_e_erro(self, audio_config):
        vazio = Script(
            generated_at=datetime.now(timezone.utc),
            episode_date="2026-08-04",
            title="T",
            lines=[ScriptLine(speaker="Maria", text="   ")],
        )
        with pytest.raises(ValueError, match="sem texto"):
            plan_segments(vazio, audio_config)

    def test_locutor_desconhecido_falha_antes_de_sintetizar(self, audio_config):
        ruim = Script(
            generated_at=datetime.now(timezone.utc),
            episode_date="2026-08-04",
            title="T",
            lines=[ScriptLine(speaker="Carlos", text="Olá.")],
        )
        with pytest.raises(ValueError, match="sem voz configurada"):
            plan_segments(ruim, audio_config)


# --------------------------------------------------------------------------- #
# synthesize — exige numpy/pydub/ffmpeg
# --------------------------------------------------------------------------- #

np = pytest.importorskip("numpy", reason="requirements-audio.txt não instalado")
pydub = pytest.importorskip("pydub", reason="requirements-audio.txt não instalado")

SAMPLE_RATE = 24000


class FakeKokoro:
    """Motor de TTS falso: devolve silencio proporcional ao tamanho do texto.

    Registra cada chamada para que os testes verifiquem voz, velocidade e
    idioma sem carregar o modelo de verdade.
    """

    def __init__(self) -> None:
        self.chamadas: list[dict] = []

    def create(self, text, voice, speed=1.0, lang="pt-br"):  # noqa: ANN001
        self.chamadas.append(
            {"text": text, "voice": voice, "speed": speed, "lang": lang}
        )
        # 20 ms de audio por caractere — so para o resultado ter duracao plausivel.
        return np.zeros(int(SAMPLE_RATE * 0.02 * len(text)), dtype="float32"), SAMPLE_RATE


ffmpeg_necessario = pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None,
    reason="ffmpeg não está no PATH",
)


class TestSynthesize:
    def test_uma_chamada_por_trecho_com_a_voz_certa(self, script, audio_config, tmp_path):
        engine = FakeKokoro()
        try:
            synthesize(script, audio_config, tmp_path, engine=engine)
        except Exception:
            pass  # sem ffmpeg o export falha; as chamadas ao TTS ja aconteceram
        assert [c["voice"] for c in engine.chamadas] == ["pf_dora", "pm_alex", "pf_dora"]

    @ffmpeg_necessario
    def test_gera_o_mp3_com_o_nome_do_episodio(self, script, audio_config, tmp_path):
        caminho = synthesize(script, audio_config, tmp_path, engine=FakeKokoro())
        assert caminho.name == "2026-08-04.mp3"
        assert caminho.stat().st_size > 0

    @ffmpeg_necessario
    def test_cria_a_pasta_de_saida(self, script, audio_config, tmp_path):
        destino = tmp_path / "audio" / "novo"
        assert synthesize(script, audio_config, destino, engine=FakeKokoro()).exists()

    @ffmpeg_necessario
    def test_duracao_inclui_as_pausas_entre_falas(self, script, audio_config, tmp_path):
        from pydub import AudioSegment

        caminho = synthesize(script, audio_config, tmp_path, engine=FakeKokoro())
        gerado = AudioSegment.from_file(caminho)

        fala = sum(len(line.text) for line in script.lines) * 20  # ms do FakeKokoro
        pausas = 2 * audio_config.gap_ms  # 3 falas -> 2 intervalos
        assert len(gerado) == pytest.approx(fala + pausas, rel=0.1)

    def test_velocidade_e_idioma_vao_para_o_motor(self, script, tmp_path):
        config = AudioConfig(
            model_path=tmp_path / "m.onnx", voices_path=tmp_path / "v.bin", speed=1.15,
        )
        engine = FakeKokoro()
        try:
            synthesize(script, config, tmp_path, engine=engine)
        except Exception:
            pass
        assert {c["speed"] for c in engine.chamadas} == {1.15}
        assert {c["lang"] for c in engine.chamadas} == {KOKORO_LANG}

    def test_locutor_invalido_falha_sem_chamar_o_motor(self, audio_config, tmp_path):
        ruim = Script(
            generated_at=datetime.now(timezone.utc),
            episode_date="2026-08-04",
            title="T",
            lines=[ScriptLine(speaker="Carlos", text="Olá.")],
        )
        engine = FakeKokoro()
        with pytest.raises(ValueError):
            synthesize(ruim, audio_config, tmp_path, engine=engine)
        assert engine.chamadas == []
