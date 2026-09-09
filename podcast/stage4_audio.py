"""Etapa 4 — sintese de audio com Kokoro TTS local. IMPLEMENTADA.

EXIGE os arquivos do modelo Kokoro baixados na maquina com a RTX 4070. Na
maquina sem GPU nao executa (requirements-audio.txt nao e instalado la de
proposito), mas o codigo e os testes se escrevem nas duas: o planejamento das
falas e funcao pura, e os testes que precisam de numpy/pydub se auto-pulam.

O que esta etapa faz:
    Script (falas de Maria e Pedro) -> um mp3 unico, na ordem certa, com as
    duas vozes alternando e uma pequena pausa entre falas.

Fluxo:
    roteiro -> plano de trechos (voz + texto, ja quebrado por frase)
    -> uma chamada ao Kokoro por trecho -> concatenacao com silencio entre
    falas -> export mp3 mono 96k com tags ID3

Decisoes que valem lembrar:
    - O modelo e carregado UMA vez e reaproveitado: a carga inicial domina o
      tempo total.
    - Falas longas sao quebradas por frase antes de sintetizar. O Kokoro degrada
      (corta ou acelera) em textos longos, e o defeito e dificil de perceber sem
      ouvir o episodio inteiro.
    - A pausa entre falas so entra ENTRE locutores diferentes ou entre falas
      distintas — nao entre os pedacos de uma mesma fala, que devem soar
      continuos.
    - ffmpeg e conferido ANTES de sintetizar: descobrir que falta depois de
      minutos de sintese seria desperdicio puro.

ANTES de otimizar esta etapa: gerar um trecho de teste e OUVIR. O CLAUDE.md
prevê trocar o Kokoro por Coqui XTTS v2 ou Chatterbox se a qualidade em PT-BR
não convencer.
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

# Codigo de idioma do Kokoro v1.0 para portugues brasileiro.
KOKORO_LANG = "pt-br"

# Teto por chamada ao TTS. Acima disto a qualidade cai; quebramos por frase.
MAX_CHARS_POR_TRECHO = 400


# --------------------------------------------------------------------------- #
# Planejamento (puro — roda e e testavel sem numpy, pydub ou Kokoro)
# --------------------------------------------------------------------------- #

def voice_for(speaker: str, config: AudioConfig) -> str:
    """Voz configurada para o locutor.

    Locutor desconhecido e erro, nao um fallback silencioso: seria um episodio
    inteiro na voz errada sem ninguem perceber ate ouvir.
    """
    vozes = {"Maria": config.voice_maria, "Pedro": config.voice_pedro}
    try:
        return vozes[speaker]
    except KeyError:
        raise ValueError(
            f"locutor sem voz configurada: {speaker!r} "
            f"(conhecidos: {', '.join(vozes)})"
        ) from None


# Fim de frase: pontuacao seguida de espaco. O lookbehind mantem a pontuacao no
# trecho anterior, que e o que o TTS precisa para fazer a entonacao de fim.
_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+")


def split_for_tts(text: str, max_chars: int = MAX_CHARS_POR_TRECHO) -> list[str]:
    """Quebra uma fala em trechos que o TTS sintetiza bem.

    Agrupa frases inteiras enquanto couberem no limite — quebrar mais do que o
    necessario introduz respiros artificiais no meio da fala. Frase unica maior
    que o limite e quebrada por palavra, como ultimo recurso.
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
    """Ultimo recurso para uma frase sem pontuacao que estoura o limite."""
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
    """Monta a lista de trechos a sintetizar, na ordem do episodio.

    Cada item e (voz, texto, comeca_fala): `comeca_fala` marca o primeiro trecho
    de cada fala e e o que decide onde entra a pausa. Funcao pura de proposito —
    e aqui que mora a logica que da para testar sem o TTS.
    """
    plano: list[tuple[str, str, bool]] = []

    for line in script.lines:
        voz = voice_for(line.speaker, config)
        trechos = split_for_tts(line.text)
        for posicao, trecho in enumerate(trechos):
            plano.append((voz, trecho, posicao == 0))

    if not plano:
        raise ValueError("roteiro sem texto sintetizavel")
    return plano


# --------------------------------------------------------------------------- #
# Sintese (exige Kokoro + numpy + pydub)
# --------------------------------------------------------------------------- #

def load_engine(config: AudioConfig):
    """Carrega o Kokoro uma unica vez."""
    config.require_model_files()
    try:
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise ConfigError(
            "pacote kokoro-onnx não instalado. Na máquina com a RTX 4070: "
            "pip install -r requirements-audio.txt"
        ) from exc

    log.info("carregando Kokoro de %s", config.model_path)
    return Kokoro(str(config.model_path), str(config.voices_path))


def samples_to_segment(samples, sample_rate: int):  # noqa: ANN001
    """Converte o float32 mono do Kokoro em AudioSegment do pydub."""
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
    engine=None,  # noqa: ANN001 — injetavel, como nas etapas 1, 2 e 3
) -> Path:
    """Converte o roteiro em um mp3 unico e devolve o caminho."""
    plano = plan_segments(script, config)

    # Antes de qualquer sintese: sem ffmpeg o export falharia no fim.
    if shutil.which("ffmpeg") is None:
        raise ConfigError(
            "ffmpeg não está no PATH — o pydub precisa dele para exportar mp3.\n"
            "No Windows: winget install Gyan.FFmpeg (e abra um terminal novo)."
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

        log.debug("trecho %d/%d (%s): %d caracteres", numero, len(plano), voz, len(texto))

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
        "áudio de %.1f min gerado em %.1f s (%d trechos): %s",
        len(episodio) / 60000, time.monotonic() - inicio, len(plano), path,
    )
    return path


def check_audio_setup(config: AudioConfig) -> tuple[bool, str]:
    """Check stage 4 prerequisites without synthesising anything.

    Run this on the GPU machine before spending minutes on synthesis.
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
