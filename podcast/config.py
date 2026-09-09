"""Central configuration, read from environment variables (.env).

Project rule: no absolute path from any machine may leak into the code. Every
file path is resolved relative to the repository root, so that cloning the repo
anywhere works without editing anything but the .env. TestSemCaminhosAbsolutosNoCodigo
fails the build if that rule is broken.
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


# Repository root = the directory containing this package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve(value: str) -> Path:
    """Resolve a path from .env against the repo root, if it is relative."""
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
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _env_float(key: str, default: float) -> float:
    raw = _env(key, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


class ConfigError(RuntimeError):
    """Missing or invalid configuration."""


@dataclass(frozen=True)
class CollectConfig:
    """Stage 1 - RSS collection."""

    window_hours: int = 24
    timeout: int = 20
    max_items_per_feed: int = 25
    seen_retention_days: int = 7


@dataclass(frozen=True)
class OllamaConfig:
    """Stage 2 - local summarise/triage. Needs Ollama running on the host."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:14b-instruct-q4_K_M"
    timeout: int = 180


@dataclass(frozen=True)
class ScriptConfig:
    """Stage 3 - script synthesis via the paid API."""

    api_key: str = ""
    model: str = "claude-sonnet-5"
    max_tokens: int = 16000
    effort: str = "high"
    target_minutes: int = 22
    # Hard ceiling on episode duration. The target sits below it deliberately:
    # the model misses the length in both directions, and overshooting is worse.
    max_minutes: int = 30

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill "
                "in the key (https://console.anthropic.com/settings/keys)."
            )
        return self.api_key


@dataclass(frozen=True)
class AudioConfig:
    """Stage 4 - local TTS with Kokoro."""

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
                "Kokoro model files not found:\n  "
                f"{listados}\n"
                "Download them into models/ (see README, 'Kokoro TTS')."
            )


@dataclass(frozen=True)
class PublishConfig:
    """Stage 5 - private RSS feed."""

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

    # Subdirectories derived from data_dir. Created on demand, not at import.
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
        """The published folder (mp3 + feed.xml) on GitHub Pages / R2."""
        return self.data_dir / "public"

    @property
    def seen_store_path(self) -> Path:
        return self.data_dir / "seen.json"


def load_config(env_file: Path | None = None) -> Config:
    """Read the .env, if present, and assemble the configuration.

    Does not raise on a missing API key: each stage validates what it needs, so
    that stage 1 runs with no credentials configured at all.
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
            max_tokens=_env_int("SCRIPT_MAX_TOKENS", 16000),
            effort=_env("SCRIPT_EFFORT", "high"),
            target_minutes=_env_int("SCRIPT_TARGET_MINUTES", 22),
            max_minutes=_env_int("SCRIPT_MAX_MINUTES", 30),
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
