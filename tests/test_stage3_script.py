"""Testes da etapa 3 com a API mockada.

Nenhum teste aqui gasta credito nem exige ANTHROPIC_API_KEY. A validacao contra
a API real e manual: `python -m podcast.cli script --from-raw ... --show`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from podcast.config import ScriptConfig
from podcast.models import Digest, SummarizedItem
from podcast.stage3_script import (
    SCRIPT_SCHEMA,
    _supports_adaptive_thinking,
    build_user_prompt,
    generate_script,
    load_script,
    save_script,
)


@pytest.fixture
def digest() -> Digest:
    return Digest(
        generated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        themes=["política monetária", "comércio EUA-China"],
        items=[
            SummarizedItem(
                item_id="a1",
                title="Copom mantém Selic em 15%",
                summary="Decisão unânime; comunicado manteve tom cauteloso.",
                source_name="InfoMoney",
                link="https://ex.com/a",
                theme="política monetária",
                relevance=9,
            ),
            SummarizedItem(
                item_id="b2",
                title="EUA e China anunciam trégua tarifária de 90 dias",
                summary="Acordo suspende novas tarifas enquanto negociam.",
                source_name="BBC News Brasil",
                link="https://ex.com/b",
                theme="comércio internacional",
                relevance=8,
            ),
        ],
    )


class FakeClient:
    """Cliente de API falso. Registra o request e devolve uma resposta pronta."""

    def __init__(self, payload: dict, stop_reason: str = "end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.captured: dict | None = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            stop_reason=self.stop_reason,
            stop_details=None,
        )


RESPOSTA_OK = {
    "title": "Copom segura os juros e EUA e China dão uma trégua",
    "themes": ["política monetária", "comércio internacional"],
    "lines": [
        {"speaker": "Maria", "text": "Bom dia. Hoje o assunto começa em Brasília."},
        {"speaker": "Pedro", "text": "Começa mesmo. O Copom manteve a Selic em quinze por cento."},
        {"speaker": "Maria", "text": "E lá fora, Pedro?"},
        {"speaker": "Pedro", "text": "Trégua de noventa dias entre Estados Unidos e China."},
        {"speaker": "Maria", "text": "É isso por hoje. Até amanhã."},
    ],
}


class TestBuildUserPrompt:
    def test_inclui_titulos_e_resumos(self, digest):
        prompt = build_user_prompt(digest, "2026-08-03", 8)
        assert "Copom mantém Selic em 15%" in prompt
        assert "Decisão unânime" in prompt

    def test_inclui_fontes(self, digest):
        prompt = build_user_prompt(digest, "2026-08-03", 8)
        assert "InfoMoney" in prompt
        assert "BBC News Brasil" in prompt

    def test_inclui_data_e_alvo_de_duracao(self, digest):
        prompt = build_user_prompt(digest, "2026-08-03", 8)
        assert "2026-08-03" in prompt
        assert "8 minutos" in prompt
        assert "1240 palavras" in prompt  # 8 * 155

    def test_inclui_temas_da_triagem(self, digest):
        assert "política monetária" in build_user_prompt(digest, "2026-08-03", 8)

    def test_digest_sem_temas(self, digest):
        digest.themes = []
        assert "não pré-identificados" in build_user_prompt(digest, "2026-08-03", 8)


class TestGenerateScript:
    def test_monta_o_script_a_partir_da_resposta(self, digest):
        client = FakeClient(RESPOSTA_OK)
        script = generate_script(digest, ScriptConfig(api_key="teste"),
                                episode_date="2026-08-03", client=client)

        assert script.title == RESPOSTA_OK["title"]
        assert len(script.lines) == 5
        assert script.lines[0].speaker == "Maria"
        assert script.episode_date == "2026-08-03"
        assert script.word_count > 0

    def test_usa_o_modelo_configurado(self, digest):
        client = FakeClient(RESPOSTA_OK)
        generate_script(digest, ScriptConfig(api_key="t", model="claude-haiku-4-5"),
                        client=client)
        assert client.captured["model"] == "claude-haiku-4-5"

    def test_envia_o_esquema_de_saida_estruturada(self, digest):
        client = FakeClient(RESPOSTA_OK)
        generate_script(digest, ScriptConfig(api_key="t"), client=client)
        formato = client.captured["output_config"]["format"]
        assert formato["type"] == "json_schema"
        assert formato["schema"] is SCRIPT_SCHEMA

    def test_modelo_moderno_recebe_thinking_e_effort(self, digest):
        client = FakeClient(RESPOSTA_OK)
        generate_script(digest, ScriptConfig(api_key="t", model="claude-sonnet-5",
                                             effort="high"), client=client)
        assert client.captured["thinking"] == {"type": "adaptive"}
        assert client.captured["output_config"]["effort"] == "high"

    def test_modelo_antigo_nao_recebe_thinking_nem_effort(self, digest):
        # haiku-4-5 rejeita `thinking: adaptive` e `effort`; trocar o modelo pelo
        # .env nao pode quebrar o request.
        client = FakeClient(RESPOSTA_OK)
        generate_script(digest, ScriptConfig(api_key="t", model="claude-haiku-4-5"),
                        client=client)
        assert "thinking" not in client.captured
        assert "effort" not in client.captured["output_config"]

    def test_digest_vazio_e_erro(self):
        vazio = Digest(generated_at=datetime.now(timezone.utc))
        with pytest.raises(ValueError, match="vazio"):
            generate_script(vazio, ScriptConfig(api_key="t"), client=FakeClient(RESPOSTA_OK))

    def test_recusa_da_api_vira_erro_claro(self, digest):
        client = FakeClient(RESPOSTA_OK, stop_reason="refusal")
        with pytest.raises(RuntimeError, match="recusou"):
            generate_script(digest, ScriptConfig(api_key="t"), client=client)

    def test_truncamento_vira_erro_claro(self, digest):
        client = FakeClient(RESPOSTA_OK, stop_reason="max_tokens")
        with pytest.raises(RuntimeError, match="SCRIPT_MAX_TOKENS"):
            generate_script(digest, ScriptConfig(api_key="t"), client=client)

    def test_json_invalido_vira_erro_claro(self, digest):
        client = FakeClient(RESPOSTA_OK)
        client._create = lambda **kw: SimpleNamespace(  # noqa: ARG005
            content=[SimpleNamespace(type="text", text="isto não é json")],
            stop_reason="end_turn", stop_details=None,
        )
        client.messages = SimpleNamespace(create=client._create)
        with pytest.raises(RuntimeError, match="JSON"):
            generate_script(digest, ScriptConfig(api_key="t"), client=client)

    def test_roteiro_sem_falas_vira_erro(self, digest):
        client = FakeClient({"title": "T", "themes": [], "lines": []})
        with pytest.raises(RuntimeError, match="sem falas"):
            generate_script(digest, ScriptConfig(api_key="t"), client=client)

    def test_locutor_desconhecido_vira_erro(self, digest):
        # A etapa 4 so tem voz para Maria e Pedro; um terceiro nome quebraria o audio.
        client = FakeClient({
            "title": "T", "themes": [],
            "lines": [{"speaker": "Carlos", "text": "Olá."}],
        })
        with pytest.raises(RuntimeError, match="locutor desconhecido"):
            generate_script(digest, ScriptConfig(api_key="t"), client=client)

    def test_falas_vazias_sao_descartadas(self, digest):
        client = FakeClient({
            "title": "T", "themes": [],
            "lines": [
                {"speaker": "Maria", "text": "Boa noite."},
                {"speaker": "Pedro", "text": "   "},
            ],
        })
        script = generate_script(digest, ScriptConfig(api_key="t"), client=client)
        assert len(script.lines) == 1


class TestSupportsAdaptiveThinking:
    @pytest.mark.parametrize("modelo", [
        "claude-sonnet-5", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6",
    ])
    def test_modelos_modernos(self, modelo):
        assert _supports_adaptive_thinking(modelo)

    @pytest.mark.parametrize("modelo", ["claude-haiku-4-5", "claude-sonnet-4-5"])
    def test_modelos_antigos(self, modelo):
        assert not _supports_adaptive_thinking(modelo)


class TestPersistencia:
    def test_grava_e_le_de_volta(self, digest, tmp_path):
        client = FakeClient(RESPOSTA_OK)
        original = generate_script(digest, ScriptConfig(api_key="t"),
                                   episode_date="2026-08-03", client=client)

        caminho = save_script(original, tmp_path)
        assert caminho.name == "2026-08-03.json"

        recarregado = load_script(caminho)
        assert recarregado.title == original.title
        assert len(recarregado.lines) == len(original.lines)
        assert recarregado.lines[0].speaker == "Maria"
