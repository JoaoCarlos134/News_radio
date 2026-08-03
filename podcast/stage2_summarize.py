"""Etapa 2 — resumo e triagem com Ollama local. ESQUELETO (a implementar).

EXIGE GPU. So roda no PC de destino (RTX 4070) com Ollama instalado e o modelo
baixado. Nao tente executar na maquina de desenvolvimento.

O que esta etapa faz (e o que NAO faz):
    FAZ  — resume cada noticia bruta em 1-2 frases, descarta irrelevantes e
           duplicatas semanticas, atribui um tema e uma nota de relevancia,
           e extrai os 3-5 temas do dia.
    NAO FAZ — analise. Isso e da etapa 3, com o modelo forte. Aqui e limpeza e
           triagem: barato, local, e reduz o volume que vai para a API paga.

Como falar com o Ollama:
    HTTP em OLLAMA_BASE_URL (padrao http://localhost:11434), endpoint
    POST /api/generate ou /api/chat. Sem SDK proprio — `requests` basta, o que
    mantem a dependencia leve e o codigo testavel com mock.

    Use `"format": "json"` no payload para forcar saida JSON, e
    `"stream": False` para receber a resposta de uma vez.

Estrategia sugerida de implementacao:
    1. Processar em lotes (~8 itens por chamada) em vez de um a um: 40 chamadas
       sequenciais a um 14B custam varios minutos.
    2. Uma segunda chamada, sobre os itens ja resumidos, para extrair os temas
       do dia.
    3. Ordenar por relevancia e cortar em ~15 itens antes da etapa 3 — o custo
       da API paga e proporcional ao que entra.
    4. Toda chamada ao modelo local deve ter timeout (OLLAMA_TIMEOUT) e
       tolerar JSON malformado: modelos pequenos erram o formato as vezes.
       Item que nao parseia deve ser descartado, nao derrubar o lote.

Ao implementar, escreva testes com o `client` injetado (mesmo padrao das etapas
1 e 3) para que rodem sem GPU nesta maquina.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import OllamaConfig
from .models import Collection, Digest

log = logging.getLogger(__name__)

# Rascunho do prompt. Refinar contra saidas reais no PC de destino.
SUMMARIZE_PROMPT = """\
Você é um editor de pauta. Para cada notícia abaixo, produza um objeto JSON com:
  item_id    — o id informado
  summary    — resumo em 1 a 2 frases, em português, com suas próprias palavras
  theme      — o tema em poucas palavras (ex.: "política monetária")
  relevance  — 0 a 10, o quanto isso importa para um ouvinte brasileiro
               interessado em economia e geopolítica

Descarte (não inclua na saída) notícias de esporte, celebridades, polícia local,
e qualquer item que repita outro já presente na lista.

Responda apenas com um array JSON, sem texto ao redor.
"""

BATCH_SIZE = 8
MAX_ITEMS_TO_STAGE3 = 15


def summarize(
    collection: Collection,
    config: OllamaConfig,
    client=None,  # noqa: ANN001 — injetavel, como nas etapas 1 e 3
) -> Digest:
    """Resume e tria a coleta bruta. A IMPLEMENTAR."""
    raise NotImplementedError(
        "Etapa 2 ainda não implementada. Requer Ollama rodando no PC de destino "
        "(ver README, seção 'Setup no PC de destino')."
    )


def check_ollama(config: OllamaConfig) -> tuple[bool, str]:
    """Verifica se o Ollama esta no ar e se o modelo configurado esta baixado.

    Implementado desde ja: e o primeiro comando a rodar no PC de destino para
    confirmar o setup, e nao depende do resto da etapa 2.
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
