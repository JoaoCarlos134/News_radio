"""Etapa 2 — resumo e triagem com Ollama local. IMPLEMENTADA.

EXIGE GPU para EXECUTAR: so roda na maquina com a RTX 4070, com Ollama no ar e o
modelo baixado. Desenvolver e testar esta etapa funciona nas duas maquinas — os
testes usam o `client` injetado e nao falam com o Ollama.

O que esta etapa faz (e o que NAO faz):
    FAZ  — resume cada noticia bruta em 1-2 frases, descarta irrelevantes e
           duplicatas semanticas, atribui um tema e uma nota de relevancia,
           e extrai os 3-5 temas do dia.
    NAO FAZ — analise. Isso e da etapa 3, com o modelo forte. Aqui e limpeza e
           triagem: barato, local, e reduz o volume que vai para a API paga.

Fluxo:
    coleta -> lotes de BATCH_SIZE itens -> uma chamada ao modelo local por lote
    -> deduplicacao semantica entre lotes -> corte por relevancia
    -> uma chamada final para extrair os temas do dia -> Digest

Robustez (modelo pequeno erra formato; o pipeline roda de madrugada sem ninguem
olhando):
    - lote que falha (timeout, JSON quebrado) e descartado com log, nao derruba
      a etapa. So se TODOS os lotes falharem e que a etapa levanta erro;
    - item cujo objeto nao parseia e ignorado, o resto do lote sobrevive;
    - a extracao de temas e best-effort: sem temas o Digest ainda serve, a
      etapa 3 sabe lidar com `themes` vazio.

Os itens sao referenciados por indice dentro do lote (1, 2, 3...), nao pelo id
real: modelos pequenos truncam e inventam digitos em hashes de 16 caracteres.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import OllamaConfig
from .models import Collection, Digest, NewsItem, SummarizedItem
from .textutils import title_similarity, truncate

log = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """\
Você é um editor de pauta. Para cada notícia numerada abaixo, produza um objeto JSON com:
  item       — o número da notícia, como veio na lista
  summary    — resumo em 1 a 2 frases, em português, com suas próprias palavras
  theme      — o tema em poucas palavras (ex.: "política monetária")
  relevance  — 0 a 10, o quanto isso importa para um ouvinte brasileiro
               interessado em economia e geopolítica

Descarte (não inclua na saída) notícias de esporte, celebridades, polícia local,
e qualquer item que repita outro já presente na lista.

Responda apenas com um objeto JSON no formato {"items": [...]}, sem texto ao redor.
"""

THEMES_PROMPT = """\
Você é um editor de pauta. A lista abaixo traz as notícias já triadas do dia.

Identifique de 3 a 5 temas que resumem o dia. Cada tema deve ter poucas palavras
(ex.: "política monetária", "tensão comercial EUA-China"), agrupar notícias
relacionadas e vir em ordem de importância.

Responda apenas com um objeto JSON no formato {"themes": ["...", "..."]}, sem
texto ao redor.
"""

BATCH_SIZE = 8
MAX_ITEMS_TO_STAGE3 = 15

# Abaixo disto o item nao paga o token que gastaria na etapa 3.
MIN_RELEVANCE = 3

# Teto do resumo devolvido pelo modelo. Ele foi instruido a escrever 1-2 frases;
# quando desobedece e despeja um paragrafo, o custo cai na etapa 3.
MAX_SUMMARY_CHARS = 400

# Acima disto dois itens de lotes diferentes sao a mesma noticia. Mais frouxo
# que o limiar da etapa 1 (0.75) porque aqui os titulos ja passaram por aquele
# filtro: o que sobra e duplicata com manchete bem diferente.
DUPLICATE_TITLE_THRESHOLD = 0.6

# Temperatura baixa: aqui o modelo classifica e resume, nao cria.
TEMPERATURE = 0.2


# --------------------------------------------------------------------------- #
# Cliente do Ollama
# --------------------------------------------------------------------------- #

class OllamaClient:
    """Wrapper minimo sobre POST /api/generate.

    Sem SDK proprio de proposito: `requests` ja e dependencia da etapa 1, e uma
    unica funcao `generate` e o que os testes precisam substituir.
    """

    def __init__(self, config: OllamaConfig) -> None:
        self.config = config

    def generate(self, prompt: str, system: str = "") -> str:
        """Devolve o texto cru da resposta do modelo."""
        import requests

        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": TEMPERATURE},
        }
        if system:
            payload["system"] = system

        response = requests.post(
            f"{self.config.base_url}/api/generate",
            json=payload,
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "")


# --------------------------------------------------------------------------- #
# Parsing tolerante (puro — o grosso dos testes mora aqui)
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(raw: str):
    """Extrai o primeiro objeto/array JSON de uma resposta do modelo local.

    Mesmo com `format: json` o Ollama as vezes devolve o JSON embrulhado em
    cerca de markdown ou precedido de uma frase. Levanta ValueError se nao
    houver JSON aproveitavel.
    """
    if not raw or not raw.strip():
        raise ValueError("resposta vazia do modelo local")

    text = _FENCE.sub("", raw.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Recorte entre o primeiro delimitador de abertura e o ultimo de fechamento.
    for abre, fecha in (("{", "}"), ("[", "]")):
        inicio, fim = text.find(abre), text.rfind(fecha)
        if inicio != -1 and fim > inicio:
            try:
                return json.loads(text[inicio:fim + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"resposta do modelo nao e JSON: {truncate(text, 200)!r}")


def _as_item_list(data) -> list[dict]:
    """Normaliza as formas que o modelo usa para devolver a lista de itens."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for chave in ("items", "noticias", "notícias", "resultados", "data"):
            valor = data.get(chave)
            if isinstance(valor, list):
                return [d for d in valor if isinstance(d, dict)]
        # Objeto unico com cara de item (lote de 1 item).
        if "summary" in data:
            return [data]
    return []


def _coerce_index(value, tamanho_do_lote: int) -> int | None:
    """Converte a referencia do item para indice 0-based, ou None se invalida."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        digitos = re.search(r"\d+", value)
        if not digitos:
            return None
        value = digitos.group()
    try:
        numero = int(value)
    except (TypeError, ValueError):
        return None
    return numero - 1 if 1 <= numero <= tamanho_do_lote else None


def _coerce_relevance(value) -> int:
    """Nota 0-10. Modelo que devolve "8/10", 8.5 ou lixo nao pode quebrar o lote."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, str):
        encontrado = re.search(r"\d+(?:[.,]\d+)?", value)
        if not encontrado:
            return 0
        value = encontrado.group().replace(",", ".")
    try:
        numero = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, numero))


def parse_batch_response(raw: str, batch: list[NewsItem]) -> list[SummarizedItem]:
    """Converte a resposta de um lote em SummarizedItem.

    Titulo, fonte e link vem sempre do item original — nunca do que o modelo
    escreveu. Assim uma alucinacao de fonte ou de URL nao chega a etapa 3.
    """
    itens: list[SummarizedItem] = []
    ja_usados: set[int] = set()

    for entrada in _as_item_list(extract_json(raw)):
        indice = _coerce_index(
            entrada.get("item", entrada.get("item_id", entrada.get("id"))),
            len(batch),
        )
        if indice is None or indice in ja_usados:
            continue

        resumo = str(entrada.get("summary") or "").strip()
        if not resumo:
            continue

        original = batch[indice]
        ja_usados.add(indice)
        itens.append(
            SummarizedItem(
                item_id=original.id,
                title=original.title,
                summary=truncate(resumo, MAX_SUMMARY_CHARS),
                source_name=original.source_name,
                link=original.link,
                theme=str(entrada.get("theme") or "").strip(),
                relevance=_coerce_relevance(entrada.get("relevance")),
            )
        )

    return itens


def parse_themes_response(raw: str) -> list[str]:
    """Extrai a lista de temas do dia. Best-effort: erro vira lista vazia."""
    try:
        data = extract_json(raw)
    except ValueError as exc:
        log.warning("temas do dia ignorados: %s", exc)
        return []

    if isinstance(data, dict):
        for chave in ("themes", "temas"):
            if isinstance(data.get(chave), list):
                data = data[chave]
                break
    if not isinstance(data, list):
        return []

    temas: list[str] = []
    for tema in data:
        if isinstance(tema, dict):  # as vezes vem {"theme": "..."}
            tema = tema.get("theme") or tema.get("tema") or ""
        tema = str(tema).strip()
        if tema and tema.lower() not in {t.lower() for t in temas}:
            temas.append(tema)
    return temas[:5]


# --------------------------------------------------------------------------- #
# Selecao (puro)
# --------------------------------------------------------------------------- #

def dedupe_and_rank(
    items: list[SummarizedItem],
    limit: int = MAX_ITEMS_TO_STAGE3,
    min_relevance: int = MIN_RELEVANCE,
) -> list[SummarizedItem]:
    """Ordena por relevancia, remove duplicatas entre lotes e corta no limite.

    A ordem importa: ordenar antes de deduplicar garante que, entre duas versoes
    da mesma noticia, a que sobrevive e a de maior relevancia.
    """
    candidatos = sorted(
        (i for i in items if i.relevance >= min_relevance),
        key=lambda i: i.relevance,
        reverse=True,
    )

    mantidos: list[SummarizedItem] = []
    for item in candidatos:
        if any(title_similarity(item.title, m.title) >= DUPLICATE_TITLE_THRESHOLD
               for m in mantidos):
            continue
        mantidos.append(item)
        if len(mantidos) >= limit:
            break

    return mantidos


def build_batch_prompt(batch: list[NewsItem]) -> str:
    """Monta o bloco de noticias numeradas enviado ao modelo local."""
    blocos = []
    for numero, item in enumerate(batch, start=1):
        linhas = [f"[{numero}] {item.title}", f"    fonte: {item.source_name}"]
        if item.summary:
            linhas.append(f"    resumo: {item.summary}")
        blocos.append("\n".join(linhas))
    return "Notícias:\n\n" + "\n\n".join(blocos)


def build_themes_prompt(items: list[SummarizedItem]) -> str:
    blocos = [
        f"- {item.title}" + (f" ({item.theme})" if item.theme else "")
        for item in items
    ]
    return "Notícias triadas de hoje:\n\n" + "\n".join(blocos)


# --------------------------------------------------------------------------- #
# Orquestracao
# --------------------------------------------------------------------------- #

def summarize(
    collection: Collection,
    config: OllamaConfig,
    client=None,  # noqa: ANN001 — injetavel, como nas etapas 1 e 3
) -> Digest:
    """Resume e tria a coleta bruta com o modelo local."""
    if not collection.items:
        raise ValueError("coleta vazia: nada para resumir")

    client = client or OllamaClient(config)

    lotes = [
        collection.items[i:i + BATCH_SIZE]
        for i in range(0, len(collection.items), BATCH_SIZE)
    ]
    log.info(
        "triagem local de %d notícias em %d lote(s) com %s",
        len(collection.items), len(lotes), config.model,
    )

    resumidos: list[SummarizedItem] = []
    falhas = 0

    for numero, lote in enumerate(lotes, start=1):
        inicio = time.monotonic()
        try:
            raw = client.generate(build_batch_prompt(lote), system=SUMMARIZE_PROMPT)
            itens = parse_batch_response(raw, lote)
        except Exception as exc:  # timeout, HTTP, JSON irrecuperavel...
            falhas += 1
            log.warning("lote %d/%d descartado: %s", numero, len(lotes), exc)
            continue

        resumidos.extend(itens)
        log.info(
            "lote %d/%d: %d de %d itens mantidos em %.1fs",
            numero, len(lotes), len(itens), len(lote), time.monotonic() - inicio,
        )

    if falhas == len(lotes):
        raise RuntimeError(
            f"todos os {len(lotes)} lotes falharam na triagem local. "
            "Verifique o Ollama com `python -m podcast.cli doctor`."
        )

    selecionados = dedupe_and_rank(resumidos)
    if not selecionados:
        raise RuntimeError(
            "a triagem local não aprovou nenhuma notícia "
            f"(de {len(collection.items)} coletadas). Revise o prompt da etapa 2 "
            "ou a relevância mínima."
        )

    temas = extract_themes(selecionados, client)
    log.info(
        "triagem concluída: %d itens, temas: %s",
        len(selecionados), ", ".join(temas) or "(nenhum)",
    )

    return Digest(
        generated_at=datetime.now(timezone.utc),
        themes=temas,
        items=selecionados,
    )


def extract_themes(items: list[SummarizedItem], client) -> list[str]:  # noqa: ANN001
    """Segunda chamada: os 3-5 temas do dia. Falha aqui nao aborta a etapa."""
    try:
        raw = client.generate(build_themes_prompt(items), system=THEMES_PROMPT)
    except Exception as exc:
        log.warning("extração de temas falhou: %s", exc)
        return []
    return parse_themes_response(raw)


def check_ollama(config: OllamaConfig) -> tuple[bool, str]:
    """Verifica se o Ollama esta no ar e se o modelo configurado esta baixado.

    E o primeiro comando a rodar na maquina com a GPU para confirmar o setup, e
    nao depende do resto da etapa 2.
    """
    import requests

    try:
        response = requests.get(f"{config.base_url}/api/tags", timeout=5)
        response.raise_for_status()
    except Exception as exc:
        return False, (
            f"Ollama não respondeu em {config.base_url}: {exc}\n"
            "Verifique se o serviço está rodando (`ollama serve`)."
        )

    modelos = [m.get("name", "") for m in response.json().get("models", [])]
    if not any(m == config.model or m.startswith(f"{config.model}:") for m in modelos):
        return False, (
            f"Ollama no ar, mas o modelo {config.model!r} não está baixado.\n"
            f"Modelos disponíveis: {', '.join(modelos) or '(nenhum)'}\n"
            f"Baixe com: ollama pull {config.model}"
        )

    return True, f"Ollama OK em {config.base_url}, modelo {config.model} disponível."


def save_digest(digest: Digest, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{digest.generated_at.date().isoformat()}.json"
    path.write_text(
        json.dumps(digest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_digest(path: Path) -> Digest:
    return Digest.from_dict(json.loads(path.read_text(encoding="utf-8")))
