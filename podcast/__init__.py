"""Daily economy and geopolitics podcast pipeline.

Stages (see CLAUDE.md):
    1. collect    - RSS via feedparser        (free, runs anywhere)
    2. summarise  - local Ollama              (free, needs a GPU)
    3. script     - paid API (Anthropic)      (PAID, runs anywhere)
    4. audio      - local Kokoro TTS + pydub  (free, needs the model files)
    5. publish    - private RSS feed          (free, runs anywhere)
"""

__version__ = "0.1.0"
