"""Pipeline do podcast diario de economia e geopolitica.

Etapas (ver CLAUDE.md):
    1. coleta   — RSS via feedparser              (grátis, roda em qualquer maquina)
    2. resumo   — Ollama local na RTX 4070        (grátis, exige GPU)
    3. roteiro  — API paga (Anthropic)            (pago, roda em qualquer maquina)
    4. audio    — Kokoro TTS local + pydub        (grátis, exige o modelo baixado)
    5. entrega  — feed RSS privado                (grátis)
"""

__version__ = "0.1.0"
