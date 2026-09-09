"""Stage 2 - summarisation and triage with local Ollama. IMPLEMENTED.

REQUIRES a GPU to EXECUTE: it runs only where Ollama is up and the model is
pulled. Developing and testing this stage works anywhere -- the tests use the
injected `client` and never speak to Ollama.

What this stage does (and does NOT do):
    DOES  - summarise each raw item in 1-2 sentences, discard irrelevant and
            semantically duplicate ones, assign a theme and a relevance score,
            and extract the day's 3-5 themes.
    DOES NOT - analysis. That is stage 3's job, with the strong model. This is
            cleanup and triage: cheap, local, and it cuts the volume that
            reaches the paid API.

Flow:
    collection -> batches of BATCH_SIZE items -> one local-model call per batch
    -> semantic deduplication across batches -> relevance cutoff
    -> one final call to extract the day's themes -> Digest

Robustness (a small model gets the format wrong, and the pipeline runs overnight
with nobody watching):
    - a failed batch (timeout, broken JSON) is dropped with a log rather than
      taking the stage down. Only if EVERY batch fails does the stage raise;
    - an item whose object will not parse is skipped, and the rest of the batch
      survives;
    - theme extraction is best-effort: with no themes the Digest is still
      usable, and stage 3 handles an empty `themes`.

Items are referenced by their index within the batch (1, 2, 3...), never by
their real id: small models truncate and invent digits in 16-character hashes.

Note on language: SUMMARIZE_PROMPT and THEMES_PROMPT stay in Portuguese. They
instruct a model whose output has to be Portuguese, and so do the alternative
JSON key names accepted in _as_item_list.
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

# Below this an item does not repay the tokens it would cost in stage 3.
MIN_RELEVANCE = 3

# Ceiling on the summary the model returns. It was told to write 1-2 sentences;
# when it disobeys and dumps a paragraph, stage 3 pays for it.
MAX_SUMMARY_CHARS = 400

# Above this, two items from different batches are the same story. Looser than
# stage 1's threshold (0.75) because these titles already passed that filter:
# what survives here are duplicates whose headlines differ considerably.
DUPLICATE_TITLE_THRESHOLD = 0.6

# Low temperature: here the model classifies and summarises, it does not create.
TEMPERATURE = 0.2


# --------------------------------------------------------------------------- #
# Ollama client
# --------------------------------------------------------------------------- #

class OllamaClient:
    """Minimal wrapper over POST /api/generate.

    No dedicated SDK on purpose: `requests` is already a stage 1 dependency, and
    a single `generate` function is all the tests need to substitute.
    """

    def __init__(self, config: OllamaConfig) -> None:
        self.config = config

    def generate(self, prompt: str, system: str = "") -> str:
        """Return the raw text of the model's response."""
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
# Tolerant parsing (pure -- most of the test suite lives here)
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(raw: str):
    """Extract the first JSON object/array from a local-model response.

    Even with `format: json`, Ollama sometimes wraps the JSON in a markdown
    fence or prefixes it with a sentence. Raises ValueError if there is no
    usable JSON.
    """
    if not raw or not raw.strip():
        raise ValueError("empty response from the local model")

    text = _FENCE.sub("", raw.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Slice between the first opening delimiter and the last closing one.
    for abre, fecha in (("{", "}"), ("[", "]")):
        inicio, fim = text.find(abre), text.rfind(fecha)
        if inicio != -1 and fim > inicio:
            try:
                return json.loads(text[inicio:fim + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"local model response is not JSON: {truncate(text, 200)!r}")


def _as_item_list(data) -> list[dict]:
    """Normalise the shapes the model uses to return the item list."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for chave in ("items", "noticias", "notícias", "resultados", "data"):
            valor = data.get(chave)
            if isinstance(valor, list):
                return [d for d in valor if isinstance(d, dict)]
        # A single object that looks like an item (a batch of one).
        if "summary" in data:
            return [data]
    return []


def _coerce_index(value, tamanho_do_lote: int) -> int | None:
    """Convert the item reference to a 0-based index, or None if invalid."""
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
    """Score 0-10. A model returning "8/10", 8.5 or junk must not break the batch."""
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
    """Convert a batch response into SummarizedItem objects.

    Title, source and link always come from the original item, never from what
    the model wrote back. That way a hallucinated source or URL never reaches
    stage 3.
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
    """Extract the day's theme list. Best-effort: an error becomes an empty list."""
    try:
        data = extract_json(raw)
    except ValueError as exc:
        log.warning("ignoring the day's themes: %s", exc)
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
# Selection (pure)
# --------------------------------------------------------------------------- #

def dedupe_and_rank(
    items: list[SummarizedItem],
    limit: int = MAX_ITEMS_TO_STAGE3,
    min_relevance: int = MIN_RELEVANCE,
) -> list[SummarizedItem]:
    """Sort by relevance, drop cross-batch duplicates, cut at the limit.

    The order matters: sorting before deduplicating guarantees that, between two
    versions of the same story, the survivor is the more relevant one.
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
    """Build the numbered item block sent to the local model."""
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
# Orchestration
# --------------------------------------------------------------------------- #

def summarize(
    collection: Collection,
    config: OllamaConfig,
    client=None,  # noqa: ANN001 — injetavel, como nas etapas 1 e 3
) -> Digest:
    """Summarise and triage the raw collection with the local model."""
    if not collection.items:
        raise ValueError("empty collection: nothing to summarise")

    client = client or OllamaClient(config)

    lotes = [
        collection.items[i:i + BATCH_SIZE]
        for i in range(0, len(collection.items), BATCH_SIZE)
    ]
    log.info(
        "local triage of %d items in %d batch(es) with %s",
        len(collection.items), len(lotes), config.model,
    )

    resumidos: list[SummarizedItem] = []
    falhas = 0

    for numero, lote in enumerate(lotes, start=1):
        inicio = time.monotonic()
        try:
            raw = client.generate(build_batch_prompt(lote), system=SUMMARIZE_PROMPT)
            itens = parse_batch_response(raw, lote)
        except Exception as exc:  # timeout, HTTP, unrecoverable JSON...
            falhas += 1
            log.warning("batch %d/%d dropped: %s", numero, len(lotes), exc)
            continue

        resumidos.extend(itens)
        log.info(
            "batch %d/%d: kept %d of %d items in %.1fs",
            numero, len(lotes), len(itens), len(lote), time.monotonic() - inicio,
        )

    if falhas == len(lotes):
        raise RuntimeError(
            f"all {len(lotes)} batches failed in local triage. "
            "Check Ollama with `python -m podcast.cli doctor`."
        )

    selecionados = dedupe_and_rank(resumidos)
    if not selecionados:
        raise RuntimeError(
            "local triage approved no items "
            f"(out of {len(collection.items)} collected). Review the stage 2 "
            "prompt or the minimum relevance."
        )

    temas = extract_themes(selecionados, client)
    log.info(
        "triage complete: %d items, themes: %s",
        len(selecionados), ", ".join(temas) or "(none)",
    )

    return Digest(
        generated_at=datetime.now(timezone.utc),
        themes=temas,
        items=selecionados,
    )


def extract_themes(items: list[SummarizedItem], client) -> list[str]:  # noqa: ANN001
    """Second call: the day's 3-5 themes. A failure here does not abort the stage."""
    try:
        raw = client.generate(build_themes_prompt(items), system=THEMES_PROMPT)
    except Exception as exc:
        log.warning("theme extraction failed: %s", exc)
        return []
    return parse_themes_response(raw)


def check_ollama(config: OllamaConfig) -> tuple[bool, str]:
    """Check that Ollama is up and the configured model is pulled.

    First thing to run on the GPU machine to confirm the setup; does not depend
    on the rest of stage 2.
    """
    import requests

    try:
        response = requests.get(f"{config.base_url}/api/tags", timeout=5)
        response.raise_for_status()
    except Exception as exc:
        return False, (
            f"Ollama did not respond at {config.base_url}: {exc}\n"
            "Check the service is running (`ollama serve`)."
        )

    modelos = [m.get("name", "") for m in response.json().get("models", [])]
    if not any(m == config.model or m.startswith(f"{config.model}:") for m in modelos):
        return False, (
            f"Ollama is up, but model {config.model!r} is not pulled.\n"
            f"Available models: {', '.join(modelos) or '(none)'}\n"
            f"Pull it with: ollama pull {config.model}"
        )

    return True, f"Ollama OK at {config.base_url}, model {config.model} available."


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
