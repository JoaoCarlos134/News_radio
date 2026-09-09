"""Stage 4 - audio synthesis with local Kokoro TTS. IMPLEMENTED.

REQUIRES the Kokoro model files present on the machine with the GPU. It does
not execute without them (requirements-audio.txt is deliberately excluded from
the development and CI install), but the code and its tests are still written
and run everywhere: segment planning is a pure function, and the tests that
need numpy/pydub skip themselves.

What this stage does:
    Script (Maria's and Pedro's lines) -> a single mp3, in order, with the two
    voices alternating and a short pause between lines.

Flow:
    script -> segment plan (voice + text, already split by sentence)
    -> one Kokoro call per segment -> concatenation with silence between lines
    -> export as mono 96k mp3 with ID3 tags

Decisions worth remembering:
    - The model is loaded ONCE and reused: the initial load dominates total
      runtime.
    - Long lines are split by sentence before synthesis. Kokoro degrades on long
      text (clipping or speeding up), and the defect is hard to notice without
      listening to a whole episode.
    - The pause only goes BETWEEN different speakers or between distinct lines,
      never between the pieces of a single line, which must sound continuous.
    - ffmpeg is checked BEFORE synthesising: discovering it is missing after
      minutes of synthesis would be pure waste.

BEFORE optimising this stage: generate a test segment and LISTEN to it.
CLAUDE.md allows replacing Kokoro with Coqui XTTS v2 or Chatterbox if the pt-BR
quality does not convince.

Note on language: the ID3 tags written below are product metadata shown in the
listener's podcast app, so they stay in Portuguese.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from .config import AudioConfig, ConfigError
from .models import Script

log = logging.getLogger(__name__)

MP3_BITRATE = "96k"

# Kokoro v1.0's language code for Brazilian Portuguese.
KOKORO_LANG = "pt-br"

# Ceiling per TTS call. Above this quality drops, so we split by sentence.
MAX_CHARS_POR_TRECHO = 400


# --------------------------------------------------------------------------- #
# Planning (pure -- runs and is testable without numpy, pydub or Kokoro)
# --------------------------------------------------------------------------- #

def voice_for(speaker: str, config: AudioConfig) -> str:
    """The voice configured for a speaker.

    An unknown speaker is an error, not a silent fallback: the alternative is a
    whole episode in the wrong voice that nobody notices until they listen.
    """
    vozes = {"Maria": config.voice_maria, "Pedro": config.voice_pedro}
    try:
        return vozes[speaker]
    except KeyError:
        raise ValueError(
            f"speaker has no voice configured: {speaker!r} "
            f"(known: {', '.join(vozes)})"
        ) from None


# Sentence end: punctuation followed by a space. The lookbehind keeps the
# punctuation in the preceding segment, which is what the TTS needs to produce
# a closing intonation.
_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+")


def split_for_tts(text: str, max_chars: int = MAX_CHARS_POR_TRECHO) -> list[str]:
    """Split a line into segments the TTS synthesises well.

    Groups whole sentences while they fit the limit -- splitting more than
    necessary introduces artificial breaths mid-line. A single sentence longer
    than the limit is split by word, as a last resort.
    """
    texto = " ".join(text.split())
    if not texto:
        return []
    if len(texto) <= max_chars:
        return [texto]

    trechos: list[str] = []
    atual = ""

    for frase in _FIM_DE_FRASE.split(texto):
        if not frase:
            continue
        if len(frase) > max_chars:
            if atual:
                trechos.append(atual)
                atual = ""
            trechos.extend(_split_by_words(frase, max_chars))
            continue

        candidato = f"{atual} {frase}".strip()
        if len(candidato) <= max_chars:
            atual = candidato
        else:
            trechos.append(atual)
            atual = frase

    if atual:
        trechos.append(atual)
    return trechos


def _split_by_words(frase: str, max_chars: int) -> list[str]:
    """Last resort for an unpunctuated sentence that overruns the limit."""
    trechos: list[str] = []
    atual = ""
    for palavra in frase.split():
        candidato = f"{atual} {palavra}".strip()
        if len(candidato) <= max_chars or not atual:
            atual = candidato
        else:
            trechos.append(atual)
            atual = palavra
    if atual:
        trechos.append(atual)
    return trechos


def plan_segments(script: Script, config: AudioConfig) -> list[tuple[str, str, bool]]:
    """Build the list of segments to synthesise, in episode order.

    Each item is (voice, text, starts_line): `starts_line` marks the first
    segment of each line and is what decides where the pause goes. Pure on
    purpose -- this is where the logic that can be tested without the TTS lives.
    """
    plano: list[tuple[str, str, bool]] = []

    for line in script.lines:
        voz = voice_for(line.speaker, config)
        trechos = split_for_tts(line.text)
        for posicao, trecho in enumerate(trechos):
            plano.append((voz, trecho, posicao == 0))

    if not plano:
        raise ValueError("script has no synthesisable text")
    return plano


# --------------------------------------------------------------------------- #
# Synthesis (requires Kokoro + numpy + pydub)
# --------------------------------------------------------------------------- #

def load_engine(config: AudioConfig):
    """Load Kokoro exactly once."""
    config.require_model_files()
    try:
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise ConfigError(
            "kokoro-onnx is not installed. On the machine with the GPU: "
            "pip install -r requirements-audio.txt"
        ) from exc

    log.info("loading Kokoro from %s", config.model_path)
    return Kokoro(str(config.model_path), str(config.voices_path))


def samples_to_segment(samples, sample_rate: int):  # noqa: ANN001
    """Convert Kokoro's mono float32 output into a pydub AudioSegment."""
    import numpy as np
    from pydub import AudioSegment

    pcm = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2")
    return AudioSegment(
        pcm.tobytes(),
        frame_rate=int(sample_rate),
        sample_width=2,
        channels=1,
    )


def synthesize(
    script: Script,
    config: AudioConfig,
    out_dir: Path,
    engine=None,  # noqa: ANN001 - injected, as in stages 1, 2 and 3
) -> Path:
    """Turn the script into a single mp3 and return its path."""
    plano = plan_segments(script, config)

    # Before any synthesis: without ffmpeg the export would fail at the end.
    if shutil.which("ffmpeg") is None:
        raise ConfigError(
            "ffmpeg is not on PATH - pydub needs it to export mp3.\n"
            "On Windows: winget install Gyan.FFmpeg (then open a new terminal)."
        )

    from pydub import AudioSegment

    engine = engine or load_engine(config)

    inicio = time.monotonic()
    episodio = AudioSegment.empty()
    pausa = None

    for numero, (voz, texto, comeca_fala) in enumerate(plano, start=1):
        samples, sample_rate = engine.create(
            texto, voice=voz, speed=config.speed, lang=KOKORO_LANG,
        )
        trecho = samples_to_segment(samples, sample_rate)

        if comeca_fala and len(episodio):
            if pausa is None:
                pausa = AudioSegment.silent(
                    duration=config.gap_ms, frame_rate=trecho.frame_rate,
                )
            episodio += pausa
        episodio += trecho

        log.debug("segment %d/%d (%s): %d characters", numero, len(plano), voz, len(texto))

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{script.episode_date}.mp3"
    episodio.export(
        path,
        format="mp3",
        bitrate=MP3_BITRATE,
        tags={
            "title": script.title,
            "artist": "Maria e Pedro",
            "album": "Economia e Geopolítica — Diário",
            "date": script.episode_date,
        },
    )

    log.info(
        "%.1f min of audio generated in %.1f s (%d segments): %s",
        len(episodio) / 60000, time.monotonic() - inicio, len(plano), path,
    )
    return path


def check_audio_setup(config: AudioConfig) -> tuple[bool, str]:
    """Check stage 4 prerequisites without synthesising anything.

    Run this before spending minutes on synthesis.
    """
    problemas: list[str] = []

    for rotulo, caminho in (("model", config.model_path), ("voices", config.voices_path)):
        if not caminho.exists():
            problemas.append(f"{rotulo} file not found: {caminho}")

    if shutil.which("ffmpeg") is None:
        problemas.append("ffmpeg is not on PATH (required to export mp3)")

    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        problemas.append(
            "kokoro-onnx not installed (pip install -r requirements-audio.txt)"
        )

    if problemas:
        return False, "Stage 4 is not ready:\n  - " + "\n  - ".join(problemas)
    return True, "Kokoro OK: model, voices and ffmpeg found."
