"""Configuracao central, lida de variaveis de ambiente (.env).

Regra do projeto: nenhum caminho absoluto desta maquina de desenvolvimento pode
vazar para o codigo. Tudo que e caminho de arquivo e resolvido em relacao a raiz
do repositorio, de modo que clonar o repo no PC de destino funcione sem editar
nada alem do .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv e opcional em tempo de import (util em CI minimo)
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


# Raiz do repositorio = pasta que contem este pacote.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve(value: str) -> Path:
    """Resolve um caminho do .env em relacao a raiz do repo (se for relativo)."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _env(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} deve ser um inteiro, recebi {raw!r}") from exc


def _env_float(key: str, default: float) -> float:
    raw = _env(key, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} deve ser um numero, recebi {raw!r}") from exc


class ConfigError(RuntimeError):
    """Configuracao ausente ou invalida."""


@dataclass(frozen=True)
class CollectConfig:
    """Etapa 1 — coleta RSS."""

    window_hours: int = 24
    timeout: int = 20
    max_items_per_feed: int = 25
    seen_retention_days: int = 7


@dataclass(frozen=True)
class OllamaConfig:
    """Etapa 2 — resumo/triagem local. Exige Ollama rodando no PC de destino."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:14b-instruct-q4_K_M"
    timeout: int = 180


@dataclass(frozen=True)
class ScriptConfig:
    """Etapa 3 — sintese do roteiro via API paga."""

    api_key: str = ""
    model: str = "claude-sonnet-5"
    max_tokens: int = 8000
    effort: str = "high"
    target_minutes: int = 8

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY nao definida. Copie .env.example para .env e "
                "preencha a chave (https://console.anthropic.com/settings/keys)."
            )
        return self.api_key


@dataclass(frozen=True)
class AudioConfig:
    """Etapa 4 — TTS local com Kokoro."""

    model_path: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "kokoro-v1.0.onnx")
    voices_path: Path = field(default_factory=lambda: PROJECT_ROOT / "models" / "voices-v1.0.bin")
    voice_maria: str = "pf_dora"
    voice_pedro: str = "pm_alex"
    speed: float = 1.0
    gap_ms: int = 350

    def require_model_files(self) -> None:
        faltando = [p for p in (self.model_path, self.voices_path) if not p.exists()]
        if faltando:
            listados = "\n  ".join(str(p) for p in faltando)
            raise ConfigError(
                "Arquivos do modelo Kokoro nao encontrados:\n  "
                f"{listados}\n"
                "Baixe-os no PC de destino (ver README, secao 'Setup no PC de destino')."
            )


@dataclass(frozen=True)
class PublishConfig:
    """Etapa 5 — feed RSS privado."""

    base_url: str = ""
    title: str = "Economia e Geopolitica — Diario"
    author: str = ""
    email: str = ""
    language: str = "pt-BR"


@dataclass(frozen=True)
class Config:
    data_dir: Path
    timezone: str
    collect: CollectConfig
    ollama: OllamaConfig
    script: ScriptConfig
    audio: AudioConfig
    publish: PublishConfig

    # Subpastas derivadas de data_dir. Criadas sob demanda, nao no import.
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def summaries_dir(self) -> Path:
        return self.data_dir / "summaries"

    @property
    def scripts_dir(self) -> Path:
        return self.data_dir / "scripts"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def public_dir(self) -> Path:
        """Pasta publicada (mp3 + feed.xml) no GitHub Pages / R2."""
        return self.data_dir / "public"

    @property
    def seen_store_path(self) -> Path:
        return self.data_dir / "seen.json"


def load_config(env_file: Path | None = None) -> Config:
    """Le o .env (se existir) e monta a configuracao.

    Nao levanta erro por chave de API ausente: cada etapa valida o que precisa,
    para que a etapa 1 rode sem nenhuma credencial configurada.
    """
    load_dotenv(env_file or (PROJECT_ROOT / ".env"), override=False)

    return Config(
        data_dir=_resolve(_env("DATA_DIR", "./data")),
        timezone=_env("PODCAST_TZ", "America/Sao_Paulo"),
        collect=CollectConfig(
            window_hours=_env_int("FEED_WINDOW_HOURS", 24),
            timeout=_env_int("FEED_TIMEOUT", 20),
            max_items_per_feed=_env_int("MAX_ITEMS_PER_FEED", 25),
            seen_retention_days=_env_int("SEEN_RETENTION_DAYS", 7),
        ),
        ollama=OllamaConfig(
            base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model=_env("OLLAMA_MODEL", "qwen2.5:14b-instruct-q4_K_M"),
            timeout=_env_int("OLLAMA_TIMEOUT", 180),
        ),
        script=ScriptConfig(
            api_key=_env("ANTHROPIC_API_KEY", ""),
            model=_env("SCRIPT_MODEL", "claude-sonnet-5"),
            max_tokens=_env_int("SCRIPT_MAX_TOKENS", 8000),
            effort=_env("SCRIPT_EFFORT", "high"),
            target_minutes=_env_int("SCRIPT_TARGET_MINUTES", 8),
        ),
        audio=AudioConfig(
            model_path=_resolve(_env("KOKORO_MODEL_PATH", "./models/kokoro-v1.0.onnx")),
            voices_path=_resolve(_env("KOKORO_VOICES_PATH", "./models/voices-v1.0.bin")),
            voice_maria=_env("KOKORO_VOICE_MARIA", "pf_dora"),
            voice_pedro=_env("KOKORO_VOICE_PEDRO", "pm_alex"),
            speed=_env_float("KOKORO_SPEED", 1.0),
            gap_ms=_env_int("KOKORO_GAP_MS", 350),
        ),
        publish=PublishConfig(
            base_url=_env("PODCAST_BASE_URL", "").rstrip("/"),
            title=_env("PODCAST_TITLE", "Economia e Geopolitica — Diario"),
            author=_env("PODCAST_AUTHOR", ""),
            email=_env("PODCAST_EMAIL", ""),
            language=_env("PODCAST_LANGUAGE", "pt-BR"),
        ),
    )
