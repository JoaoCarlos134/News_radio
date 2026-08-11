"""Testes da etapa 2 com o Ollama mockado.

Nenhum teste aqui exige GPU, Ollama no ar ou modelo baixado — o `client` e
injetado. E isso que mantem a etapa 2 desenvolvivel na maquina sem GPU. A
validacao contra o modelo real e manual, na maquina com a RTX 4070:
`python -m podcast.cli summarize -v`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from podcast.config import OllamaConfig
from podcast.models import Collection, NewsItem, SummarizedItem
from podcast.stage2_summarize import (
    BATCH_SIZE,
    MAX_ITEMS_TO_STAGE3,
    build_batch_prompt,
    dedupe_and_rank,
    extract_json,
    load_digest,
    parse_batch_response,
    parse_themes_response,
    save_digest,
    summarize,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

ASSUNTOS = (
    "Selic", "Ibovespa", "dólar", "inflação", "petróleo", "tarifas", "safra",
    "desemprego", "PIB", "Copom", "Tesouro", "criptomoedas", "energia elétrica",
    "juros americanos", "China", "zona do euro", "minério de ferro",
    "combustíveis", "varejo", "indústria", "serviços", "crédito", "bancos",
    "aviação", "saneamento", "telecomunicações", "seguros", "imóveis",
    "agronegócio", "logística",
)


def titulo_distinto(n: int) -> str:
    """Titulo que nao colide com os outros na deduplicacao por similaridade.

    Importa: com manchetes quase iguais os testes passariam a medir o dedupe em
    vez do que pretendem medir.
    """
    return f"{ASSUNTOS[(n - 1) % len(ASSUNTOS)]} em foco: recorte {n:03d}"


def noticia(n: int, titulo: str = "", fonte: str = "InfoMoney") -> NewsItem:
    return NewsItem(
        id=f"id{n:02d}",
        source_key="infomoney_mercados",
        source_name=fonte,
        title=titulo or titulo_distinto(n),
        summary=f"Resumo bruto da notícia {n}, como veio no feed.",
        link=f"https://exemplo.invalido/{n}",
        published_at=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def colecao() -> Collection:
    return Collection(
        collected_at=datetime(2026, 8, 3, 22, tzinfo=timezone.utc),
        window_hours=24,
        items=[noticia(n) for n in range(1, 4)],
    )


@pytest.fixture
def ollama_config() -> OllamaConfig:
    return OllamaConfig(model="qwen2.5:14b-instruct-q4_K_M", timeout=5)


class FakeOllama:
    """Modelo local falso: devolve respostas pre-programadas, em ordem.

    Registra os prompts recebidos para que os testes verifiquem o que foi
    enviado. Quando as respostas acabam, repete a ultima — assim um teste de
    N lotes nao precisa listar N respostas iguais.
    """

    def __init__(self, *respostas: str):
        self.respostas = list(respostas)
        self.chamadas: list[tuple[str, str]] = []

    def generate(self, prompt: str, system: str = "") -> str:
        self.chamadas.append((prompt, system))
        indice = min(len(self.chamadas) - 1, len(self.respostas) - 1)
        resposta = self.respostas[indice]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def resposta_de_itens(*itens: dict) -> str:
    return json.dumps({"items": list(itens)}, ensure_ascii=False)


def item(numero: int, relevance: int = 8, theme: str = "economia") -> dict:
    return {
        "item": numero,
        "summary": f"Resumo triado do item {numero}.",
        "theme": theme,
        "relevance": relevance,
    }


RESPOSTA_TEMAS = json.dumps({"themes": ["política monetária", "câmbio"]})


# --------------------------------------------------------------------------- #
# extract_json — o modelo local erra formato com frequencia
# --------------------------------------------------------------------------- #

class TestExtractJson:
    def test_json_limpo(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_array_no_topo(self):
        assert extract_json('[{"a": 1}]') == [{"a": 1}]

    def test_cerca_de_markdown(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_cerca_sem_rotulo_de_linguagem(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_texto_antes_e_depois(self):
        raw = 'Claro! Aqui está:\n{"a": 1}\nEspero ter ajudado.'
        assert extract_json(raw) == {"a": 1}

    def test_resposta_vazia_e_erro(self):
        with pytest.raises(ValueError, match="vazia"):
            extract_json("   ")

    def test_sem_json_e_erro(self):
        with pytest.raises(ValueError, match="não é JSON|nao e JSON"):
            extract_json("desculpe, não consegui")


# --------------------------------------------------------------------------- #
# parse_batch_response
# --------------------------------------------------------------------------- #

class TestParseBatchResponse:
    def test_mapeia_pelo_numero_do_item(self):
        lote = [noticia(1), noticia(2)]
        itens = parse_batch_response(resposta_de_itens(item(2)), lote)

        assert len(itens) == 1
        assert itens[0].item_id == "id02"
        assert itens[0].summary == "Resumo triado do item 2."
        assert itens[0].relevance == 8

    def test_titulo_fonte_e_link_vem_do_original(self):
        # Blindagem contra alucinacao: o modelo nao dita fonte nem URL.
        lote = [noticia(1, fonte="Agência Brasil")]
        alucinado = {
            "item": 1,
            "summary": "Resumo.",
            "title": "Título inventado pelo modelo",
            "source_name": "Reuters",
            "link": "https://inventado.invalido/x",
        }
        (resultado,) = parse_batch_response(resposta_de_itens(alucinado), lote)

        assert resultado.title == lote[0].title
        assert resultado.source_name == "Agência Brasil"
        assert resultado.link == lote[0].link

    def test_itens_descartados_pelo_modelo_somem(self):
        lote = [noticia(n) for n in range(1, 5)]
        itens = parse_batch_response(resposta_de_itens(item(1), item(3)), lote)
        assert [i.item_id for i in itens] == ["id01", "id03"]

    def test_array_no_topo_tambem_serve(self):
        lote = [noticia(1)]
        raw = json.dumps([item(1)])
        assert len(parse_batch_response(raw, lote)) == 1

    def test_chave_alternativa_item_id(self):
        lote = [noticia(1)]
        raw = resposta_de_itens({"item_id": 1, "summary": "Resumo."})
        assert len(parse_batch_response(raw, lote)) == 1

    def test_numero_fora_do_lote_e_ignorado(self):
        lote = [noticia(1), noticia(2)]
        itens = parse_batch_response(resposta_de_itens(item(1), item(9)), lote)
        assert [i.item_id for i in itens] == ["id01"]

    def test_numero_repetido_nao_duplica(self):
        lote = [noticia(1), noticia(2)]
        itens = parse_batch_response(resposta_de_itens(item(1), item(1)), lote)
        assert len(itens) == 1

    def test_item_sem_resumo_e_ignorado(self):
        lote = [noticia(1), noticia(2)]
        raw = resposta_de_itens({"item": 1, "summary": "  "}, item(2))
        assert [i.item_id for i in parse_batch_response(raw, lote)] == ["id02"]

    def test_item_quebrado_nao_derruba_o_lote(self):
        lote = [noticia(1), noticia(2)]
        raw = json.dumps({"items": ["texto solto", item(2)]})
        assert [i.item_id for i in parse_batch_response(raw, lote)] == ["id02"]

    def test_resumo_longo_e_truncado(self):
        lote = [noticia(1)]
        raw = resposta_de_itens({"item": 1, "summary": "palavra " * 200})
        (resultado,) = parse_batch_response(raw, lote)
        assert len(resultado.summary) <= 401  # 400 + reticencia

    @pytest.mark.parametrize("bruto,esperado", [
        (8, 8),
        ("8", 8),
        ("8/10", 8),
        (8.6, 9),
        ("nota 7,4", 7),
        (99, 10),
        (-3, 0),
        (None, 0),
        ("alta", 0),
        (True, 0),
    ])
    def test_relevancia_tolera_o_que_o_modelo_devolve(self, bruto, esperado):
        lote = [noticia(1)]
        raw = resposta_de_itens({"item": 1, "summary": "R.", "relevance": bruto})
        assert parse_batch_response(raw, lote)[0].relevance == esperado


# --------------------------------------------------------------------------- #
# parse_themes_response
# --------------------------------------------------------------------------- #

class TestParseThemesResponse:
    def test_objeto_com_themes(self):
        assert parse_themes_response(RESPOSTA_TEMAS) == ["política monetária", "câmbio"]

    def test_chave_em_portugues(self):
        raw = json.dumps({"temas": ["juros"]})
        assert parse_themes_response(raw) == ["juros"]

    def test_array_no_topo(self):
        assert parse_themes_response('["juros", "câmbio"]') == ["juros", "câmbio"]

    def test_lista_de_objetos(self):
        raw = json.dumps({"themes": [{"theme": "juros"}, {"theme": "câmbio"}]})
        assert parse_themes_response(raw) == ["juros", "câmbio"]

    def test_repetidos_saem(self):
        raw = json.dumps({"themes": ["Juros", "juros", "câmbio"]})
        assert parse_themes_response(raw) == ["Juros", "câmbio"]

    def test_corta_em_cinco(self):
        raw = json.dumps({"themes": [f"tema {n}" for n in range(10)]})
        assert len(parse_themes_response(raw)) == 5

    def test_resposta_invalida_vira_lista_vazia(self):
        # Temas sao opcionais: a etapa 3 lida com digest sem tema.
        assert parse_themes_response("não sei") == []


# --------------------------------------------------------------------------- #
# dedupe_and_rank
# --------------------------------------------------------------------------- #

def resumido(n: int, relevance: int, titulo: str = "") -> SummarizedItem:
    return SummarizedItem(
        item_id=f"id{n:02d}",
        title=titulo or titulo_distinto(n),
        summary=f"Resumo {n}.",
        source_name="InfoMoney",
        link=f"https://exemplo.invalido/{n}",
        relevance=relevance,
    )


class TestDedupeAndRank:
    def test_ordena_por_relevancia(self):
        itens = [resumido(1, 4), resumido(2, 9), resumido(3, 6)]
        assert [i.relevance for i in dedupe_and_rank(itens)] == [9, 6, 4]

    def test_corta_no_limite(self):
        itens = [resumido(n, 10 - (n % 3)) for n in range(1, 30)]
        assert len(dedupe_and_rank(itens)) == MAX_ITEMS_TO_STAGE3

    def test_descarta_abaixo_da_relevancia_minima(self):
        itens = [resumido(1, 9), resumido(2, 1)]
        assert [i.item_id for i in dedupe_and_rank(itens)] == ["id01"]

    def test_duplicata_entre_lotes_sai(self):
        a = resumido(1, 5, "Copom mantém a Selic em quinze por cento")
        b = resumido(2, 9, "Copom mantém Selic em quinze por cento, decisão unânime")
        mantidos = dedupe_and_rank([a, b])
        # Sobrevive a de maior relevancia, nao a primeira da lista.
        assert [i.item_id for i in mantidos] == ["id02"]

    def test_noticias_diferentes_ficam(self):
        a = resumido(1, 8, "Copom mantém a Selic em quinze por cento")
        b = resumido(2, 8, "Petrobras anuncia novo plano de investimento em refino")
        assert len(dedupe_and_rank([a, b])) == 2


# --------------------------------------------------------------------------- #
# build_batch_prompt
# --------------------------------------------------------------------------- #

class TestBuildBatchPrompt:
    def test_numera_a_partir_de_um(self):
        prompt = build_batch_prompt([noticia(1), noticia(2)])
        assert "[1]" in prompt and "[2]" in prompt

    def test_inclui_titulo_fonte_e_resumo(self):
        prompt = build_batch_prompt([noticia(1, fonte="Agência Brasil")])
        assert noticia(1).title in prompt
        assert "Agência Brasil" in prompt
        assert "Resumo bruto da notícia 1" in prompt

    def test_nao_vaza_o_id_interno(self):
        # Hash de 16 caracteres confunde modelo pequeno; a referencia e o numero.
        assert "id01" not in build_batch_prompt([noticia(1)])


# --------------------------------------------------------------------------- #
# summarize — orquestracao
# --------------------------------------------------------------------------- #

class TestSummarize:
    def test_fluxo_completo(self, colecao, ollama_config):
        client = FakeOllama(resposta_de_itens(item(1), item(2), item(3)), RESPOSTA_TEMAS)
        digest = summarize(colecao, ollama_config, client=client)

        assert len(digest.items) == 3
        assert digest.themes == ["política monetária", "câmbio"]
        assert digest.items[0].summary.startswith("Resumo triado")

    def test_uma_chamada_por_lote_mais_a_de_temas(self, ollama_config):
        colecao = Collection(
            collected_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            window_hours=24,
            items=[noticia(n) for n in range(1, BATCH_SIZE * 2 + 1)],
        )
        client = FakeOllama(resposta_de_itens(item(1)), resposta_de_itens(item(1)),
                            RESPOSTA_TEMAS)
        summarize(colecao, ollama_config, client=client)
        assert len(client.chamadas) == 3

    def test_lote_que_falha_nao_derruba_a_etapa(self, ollama_config):
        colecao = Collection(
            collected_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            window_hours=24,
            items=[noticia(n) for n in range(1, BATCH_SIZE + 3)],
        )
        client = FakeOllama(TimeoutError("modelo demorou demais"),
                            resposta_de_itens(item(1), item(2)),
                            RESPOSTA_TEMAS)
        digest = summarize(colecao, ollama_config, client=client)
        assert len(digest.items) == 2

    def test_lote_com_json_quebrado_nao_derruba_a_etapa(self, ollama_config):
        colecao = Collection(
            collected_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
            window_hours=24,
            items=[noticia(n) for n in range(1, BATCH_SIZE + 3)],
        )
        client = FakeOllama("isto não é json", resposta_de_itens(item(1)),
                            RESPOSTA_TEMAS)
        assert len(summarize(colecao, ollama_config, client=client).items) == 1

    def test_todos_os_lotes_falhando_e_erro(self, colecao, ollama_config):
        client = FakeOllama(ConnectionError("ollama fora do ar"))
        with pytest.raises(RuntimeError, match="lotes falharam"):
            summarize(colecao, ollama_config, client=client)

    def test_triagem_que_zera_tudo_e_erro(self, colecao, ollama_config):
        # Aprovar zero noticia significa prompt ou corte mal calibrado — nao
        # pode passar batido e gerar um episodio vazio.
        client = FakeOllama(resposta_de_itens())
        with pytest.raises(RuntimeError, match="nenhuma notícia"):
            summarize(colecao, ollama_config, client=client)

    def test_falha_nos_temas_nao_derruba_a_etapa(self, colecao, ollama_config):
        client = FakeOllama(resposta_de_itens(item(1)), ConnectionError("caiu"))
        digest = summarize(colecao, ollama_config, client=client)
        assert digest.themes == []
        assert len(digest.items) == 1

    def test_coleta_vazia_e_erro(self, ollama_config):
        vazia = Collection(collected_at=datetime.now(timezone.utc), window_hours=24)
        with pytest.raises(ValueError, match="vazia"):
            summarize(vazia, ollama_config, client=FakeOllama(""))

    def test_prompt_de_sistema_e_o_da_triagem(self, colecao, ollama_config):
        client = FakeOllama(resposta_de_itens(item(1)), RESPOSTA_TEMAS)
        summarize(colecao, ollama_config, client=client)
        _, system_do_lote = client.chamadas[0]
        assert "editor de pauta" in system_do_lote
        assert "relevance" in system_do_lote


# --------------------------------------------------------------------------- #
# Persistencia
# --------------------------------------------------------------------------- #

class TestPersistencia:
    def test_grava_e_le_de_volta(self, colecao, ollama_config, tmp_path):
        client = FakeOllama(resposta_de_itens(item(1), item(2)), RESPOSTA_TEMAS)
        original = summarize(colecao, ollama_config, client=client)

        caminho = save_digest(original, tmp_path)
        assert caminho.name == f"{original.generated_at.date().isoformat()}.json"

        recarregado = load_digest(caminho)
        assert recarregado.themes == original.themes
        assert [i.item_id for i in recarregado.items] == [i.item_id for i in original.items]
        assert recarregado.items[0].relevance == original.items[0].relevance
