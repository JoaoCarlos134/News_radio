"""Etapa 4 — sintese de audio com Kokoro TTS local. ESQUELETO (a implementar).

EXIGE os arquivos do modelo Kokoro baixados no PC de destino. Nao roda na
maquina de desenvolvimento (as dependencias estao em requirements-audio.txt e
nao sao instaladas aqui de proposito).

O que esta etapa faz:
    Script (falas de Maria e Pedro) -> um mp3 unico, na ordem certa, com as
    duas vozes alternando e uma pequena pausa entre falas.

Como usar o kokoro-onnx:
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(str(config.model_path), str(config.voices_path))
    samples, sample_rate = kokoro.create(texto, voice="pf_dora", speed=1.0, lang="pt-br")
    # `samples` e um numpy array float32 mono.

    Vozes PT-BR do Kokoro v1.0: pf_dora (feminina), pm_alex e pm_santa
    (masculinas). Configuradas em KOKORO_VOICE_MARIA / KOKORO_VOICE_PEDRO.

Estrategia sugerida de implementacao:
    1. Carregar o modelo UMA vez e reaproveitar entre as falas — a carga inicial
       domina o tempo total.
    2. Gerar cada fala separadamente, com a voz do locutor.
    3. Converter cada trecho para um AudioSegment do pydub e concatenar,
       inserindo KOKORO_GAP_MS de silencio entre falas.
    4. Exportar em mp3 (bitrate 96k mono ja e mais que suficiente para voz;
       mantem o arquivo pequeno para o feed).
    5. Falas muito longas podem estourar o contexto do TTS — quebre por frase
       (ponto final) antes de sintetizar, se acontecer.

    Exportar mp3 exige ffmpeg no PATH. Cheque isso antes de gastar minutos de
    sintese: `shutil.which("ffmpeg")`.

ANTES de implementar a fundo: gerar um trecho de teste e OUVIR. O CLAUDE.md
prevê trocar o Kokoro por Coqui XTTS v2 ou Chatterbox se a qualidade em PT-BR
não convencer. Não vale otimizar esta etapa antes dessa decisão.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .config import AudioConfig
from .models import Script

log = logging.getLogger(__name__)

MP3_BITRATE = "96k"


def synthesize(script: Script, config: AudioConfig, out_dir: Path) -> Path:
    """Converte o roteiro em um mp3 unico. A IMPLEMENTAR."""
    raise NotImplementedError(
        "Etapa 4 ainda não implementada. Requer o modelo Kokoro baixado no PC "
        "de destino (ver README, seção 'Setup no PC de destino')."
    )


def check_audio_setup(config: AudioConfig) -> tuple[bool, str]:
    """Confere pre-requisitos da etapa 4 sem sintetizar nada.

    Implementado desde ja: e o comando de diagnostico a rodar no PC de destino.
    """
    problemas: list[str] = []

    for rotulo, caminho in (("modelo", config.model_path), ("vozes", config.voices_path)):
        if not caminho.exists():
            problemas.append(f"arquivo de {rotulo} não encontrado: {caminho}")

    if shutil.which("ffmpeg") is None:
        problemas.append("ffmpeg não está no PATH (necessário para exportar mp3)")

    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        problemas.append(
            "pacote kokoro-onnx não instalado (pip install -r requirements-audio.txt)"
        )

    if problemas:
        return False, "Etapa 4 não está pronta:\n  - " + "\n  - ".join(problemas)
    return True, "Kokoro OK: modelo, vozes e ffmpeg encontrados."
