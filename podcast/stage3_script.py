"""Stage 3 - script synthesis via the paid API. IMPLEMENTED.

Needs no GPU. It is the only stage that costs money and the only one that can
be exercised for real anywhere, given just an ANTHROPIC_API_KEY.

Takes stage 2's Digest and returns a Script: a dialogue between Maria and
Pedro. This is where the actual analysis happens -- the local model only did
triage.

Copyright: the prompt requires synthesis in the model's own words from the
summaries. Reproducing any span of an original article is forbidden (see
CLAUDE.md). Note that this is a prompt instruction, not a verified invariant;
the mechanical half of the guarantee lives in stage 1, which never fetches
article bodies at all.

Note on language: SYSTEM_PROMPT, the SCRIPT_SCHEMA descriptions and the string
built by build_user_prompt are prompt text for a Portuguese-language podcast.
They stay in Portuguese; translating them would change the product.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import ScriptConfig
from .models import Digest, Script, ScriptLine, SummarizedItem

log = logging.getLogger(__name__)

# Kokoro's pt-BR reading pace, used to convert minutes into words.
#
# MEASURED on a complete real episode: 3227 words across 75 lines (94 segments
# after sentence splitting) produced 18.74 min of mp3 -- 172 words/min.
#
# The figure falls as the sample grows, which is why only the full-episode
# measurement counts:
#     one continuous block ......... 202 wpm  (no pauses, no closing intonation)
#     11-line dialogue ............. 177 wpm
#     75-line episode .............. 172 wpm  <- this one
# Every line adds a pause and an end-of-sentence cadence, so the more lines, the
# slower the whole. At 195 the "30 min" ceiling was delivering a real 33 min.
#
# Depends on KOKORO_SPEED and KOKORO_GAP_MS -- change either and re-measure, on
# a complete episode rather than a short sample.
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
    """Models 4.6+ use adaptive thinking and the `effort` parameter.

    Older models (haiku-4-5, for instance) reject those fields, so choosing the
    model through an env var has to adjust the request too.
    """
    modern = ("claude-opus-5", "claude-opus-4-6", "claude-opus-4-7",
              "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6",
              "claude-fable-5")
    return any(model.startswith(prefix) for prefix in modern)


# Output schema: guarantees the response arrives as structured lines, with no
# free text to parse back.
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
    """Spoken duration -> word count, at Kokoro's measured pace."""
    return minutes * WORDS_PER_MINUTE


def estimate_minutes(word_count: int) -> float:
    """Word count -> estimated spoken duration."""
    return word_count / WORDS_PER_MINUTE


def build_user_prompt(
    digest: Digest,
    episode_date: str,
    target_minutes: int,
    max_minutes: int = 30,
) -> str:
    """Build the prompt from stage 2's filtered summaries."""
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

    # Explicit decomposition: asking for the total alone makes the model
    # deliver about half. Given a per-theme budget it can check its own length
    # while writing, instead of estimating four thousand words in its head.
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
    client=None,  # noqa: ANN001 - injected in tests
) -> Script:
    """Call the paid API and return the structured script.

    `client` is injected so the tests run with no key and no network.
    """
    if not digest.items:
        raise ValueError("empty digest: nothing to turn into a script")

    episode_date = episode_date or datetime.now(timezone.utc).date().isoformat()

    if client is None:
        import anthropic  # late import: only stage 3 needs the package

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

    log.info("generating script with %s (%d items)", config.model, len(digest.items))
    response = client.messages.create(**request)

    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError(
            "the API refused to generate the script "
            f"(stop_reason=refusal, details={getattr(response, 'stop_details', None)})"
        )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            "script truncated: raise SCRIPT_MAX_TOKENS "
            f"(currently: {config.max_tokens})"
        )

    script = parse_script_response(response, episode_date, config.model)

    # The model controls length by word count, which is approximate. A slightly
    # long episode does not justify losing the overnight run -- but it has to
    # show up in the log, because that is the signal the prompt needs adjusting.
    # Stage 4 logs the real duration afterwards.
    duracao = estimate_minutes(script.word_count)
    if duracao > config.max_minutes:
        log.warning(
            "script estimated at %.1f min, above the %d min ceiling "
            "(%d words). The episode will be generated anyway; if this repeats, "
            "lower SCRIPT_TARGET_MINUTES or MAX_ITEMS_TO_STAGE3.",
            duracao, config.max_minutes, script.word_count,
        )
    else:
        log.info("script of %d words (~%.1f min)", script.word_count, duracao)

    return script


def parse_script_response(response, episode_date: str, model: str) -> Script:  # noqa: ANN001
    """Extract the Script from the JSON payload the API returned."""
    text = next(
        (block.text for block in response.content if getattr(block, "type", None) == "text"),
        None,
    )
    if not text:
        raise RuntimeError("API response has no text block")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API response is not valid JSON: {exc}") from exc

    lines = [
        ScriptLine(speaker=line["speaker"], text=line["text"].strip())
        for line in data.get("lines", [])
        if line.get("text", "").strip()
    ]
    if not lines:
        raise RuntimeError("the API returned a script with no lines")

    invalidos = {line.speaker for line in lines} - {"Maria", "Pedro"}
    if invalidos:
        raise RuntimeError(f"lines with unknown speaker: {sorted(invalidos)}")

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
    """Development shortcut: build a Digest straight from stage 1's output.

    Lets stage 3 be exercised without stage 2's Ollama. Quality is worse, with
    no triage and no theme extraction -- this is a testing tool, not the
    production path.
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
