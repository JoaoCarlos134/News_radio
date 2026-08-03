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
WORDS_PER_MINUTE = 155

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
- Cubra de 2 a 4 temas, em profundidade. Não tente cobrir tudo o que aconteceu; \
um tema bem explicado vale mais que seis manchetes lidas.
- Priorize o que muda decisões: juros, câmbio, inflação, atividade, e os eventos \
geopolíticos com efeito econômico real.
- Conecte os temas quando houver ligação genuína. Não force.
- Números importam: cite os que estiverem nos resumos. Nunca invente número, data, \
nome ou declaração que não esteja no material fornecido.
- Se o material for insuficiente para afirmar algo, trate como incerto no próprio \
roteiro ("ainda não está claro se...").

Regras de forma (o texto vira áudio por TTS — ninguém vai ler isto):
- Escreva números por extenso quando a leitura exigir: "treze e vinte e cinco por \
cento", "um vírgula dois por cento", "dois mil e vinte e seis".
- Sem markdown, sem marcadores, sem títulos, sem emoji, sem texto entre parênteses \
de direção de cena.
- Frases faladas, não escritas. Cada fala tem de 1 a 5 frases.
- Comece com Maria, alterne, e termine com uma fala de despedida.

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


def build_user_prompt(digest: Digest, episode_date: str, target_minutes: int) -> str:
    """Monta o prompt com os resumos filtrados da etapa 2."""
    target_words = target_minutes * WORDS_PER_MINUTE

    blocos = []
    for n, item in enumerate(digest.items, start=1):
        linhas = [f"[{n}] {item.title}", f"    fonte: {item.source_name}"]
        if item.theme:
            linhas.append(f"    tema: {item.theme}")
        if item.summary:
            linhas.append(f"    resumo: {item.summary}")
        blocos.append("\n".join(linhas))

    temas = ", ".join(digest.themes) if digest.themes else "(não pré-identificados)"

    return (
        f"Data do episódio: {episode_date}\n"
        f"Duração alvo: {target_minutes} minutos, ou seja cerca de "
        f"{target_words} palavras no total do roteiro.\n"
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
            "content": build_user_prompt(digest, episode_date, config.target_minutes),
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

    return parse_script_response(response, episode_date, config.model)


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
