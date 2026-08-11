"""Etapa 3 — sintese do roteiro via API paga. IMPLEMENTADA.

Nao depende de GPU: e a unica etapa paga e a unica que pode ser testada de
verdade na maquina de desenvolvimento (basta ANTHROPIC_API_KEY).

Recebe o Digest da etapa 2 e devolve um Script: dialogo entre Maria e Pedro.
E aqui que entra a analise de verdade — o modelo local so fez triagem.

Copyright: o prompt exige sintese em linguagem propria a partir dos resumos.
Reproduzir trecho de artigo original e proibido (ver CLAUDE.md).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import ScriptConfig
from .models import Digest, Script, ScriptLine, SummarizedItem

log = logging.getLogger(__name__)

# Ritmo de leitura do Kokoro em PT-BR, usado para converter minutos em palavras.
#
# MEDIDO num episodio inteiro de verdade: 3227 palavras em 75 falas (94 trechos
# depois da quebra por frase) deram 18,74 min de mp3 — 172 palavras/min.
#
# O numero cai conforme a amostra cresce, e por isso a medicao boa e a do
# episodio completo:
#     bloco unico de texto ......... 202 pal/min  (sem pausa, sem entonacao final)
#     dialogo de 11 falas .......... 177 pal/min
#     episodio de 75 falas ......... 172 pal/min  <- este
# Cada fala adiciona pausa e uma cadencia de fim de frase; quanto mais falas,
# mais lento o conjunto. Com 195 o teto de 30 min entregava 33 min reais.
#
# Depende de KOKORO_SPEED e de KOKORO_GAP_MS — mexeu neles, remeça, e remeça
# num episodio completo, nao numa amostra curta.
WORDS_PER_MINUTE = 172

SYSTEM_PROMPT = """\
Você escreve o roteiro de um podcast diário brasileiro de economia e geopolítica, \
em formato de diálogo entre dois apresentadores: Maria e Pedro.

Como o programa soa:
- Maria conduz: abre o episódio, introduz cada tema, faz as perguntas que o \
ouvinte faria e fecha o programa.
- Pedro analisa: traz contexto, números, causa e consequência. É ele quem explica \
por que a notícia importa.
- Eles conversam de verdade — discordam, complementam, retomam o que o outro disse. \
Não são dois locutores lendo blocos alternados.
- Tom: um analista experiente explicando para um amigo inteligente que não é do \
mercado. Direto, sem jargão gratuito, sem entusiasmo publicitário.

Regras de conteúdo:
- Cubra de 4 a 6 temas, em profundidade. Não tente cobrir tudo o que aconteceu; \
um tema bem explicado vale mais que seis manchetes lidas.
- O eixo do programa é a economia brasileira: juros (Selic e Copom), bolsa \
(Ibovespa e as empresas que a movem), câmbio, inflação e atividade. Notícia \
internacional entra quando tem efeito sobre esse eixo — não como bloco separado.
- Conecte os temas quando houver ligação genuína. Não force.
- Números importam: cite os que estiverem nos resumos. Nunca invente número, data, \
nome ou declaração que não esteja no material fornecido.
- Se o material for insuficiente para afirmar algo, trate como incerto no próprio \
roteiro ("ainda não está claro se...").

Didática — o ouvinte não é do mercado:
- Todo termo técnico é explicado na primeira vez que aparece no episódio, em uma \
frase, dentro da conversa. Não é um glossário à parte: é o Pedro explicando \
porque a Maria perguntou. Vale para Selic, Copom, Ibovespa, IPCA, Boletim Focus, \
ponto-base, curva de juros, e qualquer sigla ou jargão.
- Maria é quem puxa a explicação. Ela pergunta o que o ouvinte perguntaria: \
"o que é isso na prática?", "por que isso mexe no meu bolso?", "isso é muito ou \
pouco?". Ela não finge saber para o programa andar mais rápido.
- Depois de explicar o termo, mostre o mecanismo: o que causa o quê, e em quanto \
tempo o efeito aparece. "Juro alto encarece crédito, crédito caro segura consumo, \
consumo fraco derruba preço — e isso leva meses, não semanas."
- Ordem de grandeza importa mais que o número exato. Diga se um dado é grande ou \
pequeno, e comparado com o quê: "meio ponto percentual parece pouco, mas numa \
dívida do tamanho da brasileira são dezenas de bilhões por ano".
- Analogia que envolve conta é conta: só use se ela fechar. "Um vírgula oito por \
cento de um mês" não vira "dois dias de trabalho" — vira meio dia. Se não tiver \
certeza da aritmética, descreva o efeito em palavras em vez de inventar a \
equivalência.
- Não repita a mesma explicação em dois temas diferentes. Explicou uma vez, pode \
usar o termo à vontade depois.

Regras de forma (o texto vira áudio por TTS — ninguém vai ler isto):
- Escreva números por extenso quando a leitura exigir: "treze e vinte e cinco por \
cento", "um vírgula dois por cento", "dois mil e vinte e seis".
- Sem markdown, sem marcadores, sem títulos, sem emoji, sem texto entre parênteses \
de direção de cena.
- Frases faladas, não escritas. Cada fala tem de 1 a 5 frases.
- O episódio é gerado de madrugada e ouvido de manhã: abra cumprimentando com \
"bom dia", nunca "boa tarde" ou "boa noite".
- Comece com Maria e ALTERNE a cada fala: Maria, Pedro, Maria, Pedro. Nunca duas \
falas seguidas do mesmo locutor — se um deles tem mais a dizer, é o outro que \
puxa a continuação com uma pergunta.
- Termine com uma fala de despedida.
- Respeite o tamanho pedido, que vem na mensagem do usuário com o orçamento de \
palavras por tema. Roteiro curto demais é o erro mais comum aqui: não encerre o \
episódio enquanto não tiver desenvolvido cada tema no tamanho combinado. Se \
sentir que está acabando cedo, é porque faltou aprofundar — volte e explique o \
mecanismo, traga o contexto histórico do dado, ou discuta o efeito prático sobre \
o ouvinte.
- Se precisar escolher, prefira quatro temas bem desenvolvidos a seis correndo.

Copyright — obrigatório:
Você recebe apenas manchetes e resumos curtos. Sintetize e analise com suas \
próprias palavras. Nunca reproduza frases dos resumos originais literalmente.\
"""


def _supports_adaptive_thinking(model: str) -> bool:
    """Modelos 4.6+ usam thinking adaptativo e o parametro `effort`.

    Modelos mais antigos (ex.: haiku-4-5) rejeitam esses campos, entao a escolha
    do modelo por env var precisa ajustar o request.
    """
    modern = ("claude-opus-5", "claude-opus-4-6", "claude-opus-4-7",
              "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6",
              "claude-fable-5")
    return any(model.startswith(prefix) for prefix in modern)


# Esquema de saida: garante que a resposta ja venha como falas estruturadas,
# sem precisar parsear texto livre.
SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Título do episódio, até 70 caracteres, sem a data.",
        },
        "themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Os 2 a 4 temas cobertos, um por item, em poucas palavras.",
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["Maria", "Pedro"]},
                    "text": {"type": "string"},
                },
                "required": ["speaker", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "themes", "lines"],
    "additionalProperties": False,
}


def minutes_to_words(minutes: int) -> int:
    """Duracao falada -> numero de palavras, no ritmo medido do Kokoro."""
    return minutes * WORDS_PER_MINUTE


def estimate_minutes(word_count: int) -> float:
    """Numero de palavras -> duracao falada estimada."""
    return word_count / WORDS_PER_MINUTE


def build_user_prompt(
    digest: Digest,
    episode_date: str,
    target_minutes: int,
    max_minutes: int = 30,
) -> str:
    """Monta o prompt com os resumos filtrados da etapa 2."""
    target_words = minutes_to_words(target_minutes)
    max_words = minutes_to_words(max_minutes)

    blocos = []
    for n, item in enumerate(digest.items, start=1):
        linhas = [f"[{n}] {item.title}", f"    fonte: {item.source_name}"]
        if item.theme:
            linhas.append(f"    tema: {item.theme}")
        if item.summary:
            linhas.append(f"    resumo: {item.summary}")
        blocos.append("\n".join(linhas))

    temas = ", ".join(digest.themes) if digest.themes else "(não pré-identificados)"

    # Decomposicao explicita: pedir so o total faz o modelo entregar metade.
    # Com o orcamento por tema ele tem como conferir o proprio tamanho enquanto
    # escreve, em vez de estimar 4 mil palavras de cabeca.
    palavras_por_tema = target_words // 5

    return (
        f"Data do episódio: {episode_date}\n\n"
        f"TAMANHO — leia com atenção, é onde roteiros costumam falhar:\n"
        f"O episódio tem {target_minutes} minutos, ou seja {target_words} palavras. "
        f"Esse número é o alvo E o mínimo: um roteiro de {target_words // 2} "
        "palavras é metade do programa e não serve.\n"
        f"Como chegar lá: com 5 temas, cada tema precisa de cerca de "
        f"{palavras_por_tema} palavras — algo como 12 a 16 falas por tema, não 6. "
        "Cada tema é um bloco completo: abre, explica o termo, mostra o mecanismo, "
        "traz os números, discute o efeito sobre o ouvinte e fecha antes do "
        "próximo. Some as falas de abertura e despedida por cima disso.\n"
        f"Teto absoluto: {max_minutes} minutos ({max_words} palavras). Se o "
        "material render mais que isso, corte um tema inteiro — nunca encurte as "
        "explicações dos temas que ficarem.\n\n"
        f"Temas pré-identificados na triagem: {temas}\n\n"
        "Notícias disponíveis (manchete + resumo publicado pelo veículo):\n\n"
        + "\n\n".join(blocos)
        + "\n\nEscreva o roteiro do episódio de hoje."
    )


def generate_script(
    digest: Digest,
    config: ScriptConfig,
    episode_date: str | None = None,
    client=None,  # noqa: ANN001 — injetavel nos testes
) -> Script:
    """Chama a API paga e devolve o roteiro estruturado.

    `client` e injetavel para que os testes rodem sem chave e sem rede.
    """
    if not digest.items:
        raise ValueError("digest vazio: nada para transformar em roteiro")

    episode_date = episode_date or datetime.now(timezone.utc).date().isoformat()

    if client is None:
        import anthropic  # import tardio: so quem roda a etapa 3 precisa do pacote

        client = anthropic.Anthropic(api_key=config.require_api_key())

    request: dict = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": build_user_prompt(
                digest, episode_date, config.target_minutes, config.max_minutes,
            ),
        }],
        "output_config": {"format": {"type": "json_schema", "schema": SCRIPT_SCHEMA}},
    }
    if _supports_adaptive_thinking(config.model):
        request["thinking"] = {"type": "adaptive"}
        request["output_config"]["effort"] = config.effort

    log.info("gerando roteiro com %s (%d notícias)", config.model, len(digest.items))
    response = client.messages.create(**request)

    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError(
            "a API recusou a geração do roteiro "
            f"(stop_reason=refusal, detalhes={getattr(response, 'stop_details', None)})"
        )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "roteiro truncado: aumente SCRIPT_MAX_TOKENS "
            f"(atual: {config.max_tokens})"
        )

    script = parse_script_response(response, episode_date, config.model)

    # O modelo controla o tamanho por contagem de palavras, que e aproximada.
    # Um episodio um pouco longo nao justifica perder a execucao da madrugada —
    # mas tem de aparecer no log, porque e o sinal de que o prompt precisa de
    # ajuste. A etapa 4 loga a duracao real depois.
    duracao = estimate_minutes(script.word_count)
    if duracao > config.max_minutes:
        log.warning(
            "roteiro estimado em %.1f min, acima do teto de %d min "
            "(%d palavras). Episódio será gerado assim mesmo; se repetir, "
            "reduza SCRIPT_TARGET_MINUTES ou MAX_ITEMS_TO_STAGE3.",
            duracao, config.max_minutes, script.word_count,
        )
    else:
        log.info("roteiro de %d palavras (~%.1f min)", script.word_count, duracao)

    return script


def parse_script_response(response, episode_date: str, model: str) -> Script:  # noqa: ANN001
    """Extrai o Script do payload JSON devolvido pela API."""
    text = next(
        (block.text for block in response.content if getattr(block, "type", None) == "text"),
        None,
    )
    if not text:
        raise RuntimeError("resposta da API sem bloco de texto")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"resposta da API não é JSON válido: {exc}") from exc

    lines = [
        ScriptLine(speaker=line["speaker"], text=line["text"].strip())
        for line in data.get("lines", [])
        if line.get("text", "").strip()
    ]
    if not lines:
        raise RuntimeError("a API devolveu um roteiro sem falas")

    invalidos = {line.speaker for line in lines} - {"Maria", "Pedro"}
    if invalidos:
        raise RuntimeError(f"falas com locutor desconhecido: {sorted(invalidos)}")

    return Script(
        generated_at=datetime.now(timezone.utc),
        episode_date=episode_date,
        title=data.get("title", f"Episódio de {episode_date}").strip(),
        lines=lines,
        themes=list(data.get("themes", [])),
        model=model,
    )


def save_script(script: Script, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{script.episode_date}.json"
    path.write_text(
        json.dumps(script.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_script(path: Path) -> Script:
    return Script.from_dict(json.loads(path.read_text(encoding="utf-8")))


def digest_from_collection_file(path: Path) -> Digest:
    """Atalho de desenvolvimento: monta um Digest direto da saida da etapa 1.

    Permite exercitar a etapa 3 nesta maquina sem o Ollama da etapa 2. A
    qualidade e pior (sem triagem nem extracao de temas) — e ferramenta de teste,
    nao caminho de producao.
    """
    from .stage1_collect import load_collection

    collection = load_collection(path)
    return Digest(
        generated_at=datetime.now(timezone.utc),
        themes=[],
        items=[
            SummarizedItem(
                item_id=item.id,
                title=item.title,
                summary=item.summary,
                source_name=item.source_name,
                link=item.link,
            )
            for item in collection.items
        ],
    )
