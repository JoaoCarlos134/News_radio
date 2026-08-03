"""Etapa 5 — publicacao do feed RSS privado. ESQUELETO (a implementar).

Nao exige GPU. Fica por ultimo porque so faz sentido depois que a etapa 4
produzir mp3 de verdade.

O que esta etapa faz:
    mp3 do episodio -> entrada em um feed RSS de podcast -> arquivos prontos
    para subir (GitHub Pages ou Cloudflare R2 free tier) -> assinar no app
    Podcasts do iOS.

Estrategia sugerida de implementacao:
    1. Manter o mp3 e o feed.xml na mesma pasta publicada (config.public_dir).
    2. Reconstruir o feed inteiro a cada execucao, varrendo os mp3 existentes.
       E mais simples e mais robusto que fazer append incremental — se o feed
       corromper, a proxima execucao conserta sozinha.
    3. Usar `feedgen` com a extensao de podcast:
           from feedgen.feed import FeedGenerator
           fg = FeedGenerator()
           fg.load_extension("podcast")
       Cada item precisa de <enclosure> com url, tamanho em bytes e
       type="audio/mpeg" — sem isso o app Podcasts nao baixa o arquivo.
    4. As URLs no feed precisam ser absolutas, a partir de PODCAST_BASE_URL.
       Caminho relativo funciona no navegador e falha no app do iOS.
    5. Guardar so os ultimos ~30 episodios no feed e apagar os mp3 antigos:
       GitHub Pages tem limite de repositorio, e ninguem vai ouvir o de 3 meses
       atras.
    6. Publicacao em si: `git add/commit/push` na branch do GitHub Pages, ou
       upload via API do R2. Deixar isso como funcao separada de `build_feed`,
       para poder gerar o feed sem publicar durante os testes.

Privacidade: o feed e "privado" apenas por obscuridade da URL — qualquer um com
o link consegue ouvir. Nao inclua nada sensivel, e nao submeta a URL a diretorios
de podcast.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PublishConfig
from .models import Script

log = logging.getLogger(__name__)

MAX_EPISODES_IN_FEED = 30


def build_feed(config: PublishConfig, public_dir: Path) -> Path:
    """Reconstroi feed.xml a partir dos mp3 em public_dir. A IMPLEMENTAR."""
    raise NotImplementedError("Etapa 5 ainda não implementada.")


def publish_episode(
    script: Script,
    audio_path: Path,
    config: PublishConfig,
    public_dir: Path,
) -> Path:
    """Move o mp3 para a pasta publicada e regenera o feed. A IMPLEMENTAR."""
    raise NotImplementedError("Etapa 5 ainda não implementada.")


def check_publish_setup(config: PublishConfig) -> tuple[bool, str]:
    """Confere se a configuracao minima de publicacao esta preenchida."""
    faltando = [
        nome for nome, valor in (
            ("PODCAST_BASE_URL", config.base_url),
            ("PODCAST_AUTHOR", config.author),
            ("PODCAST_EMAIL", config.email),
        ) if not valor
    ]
    if faltando:
        return False, "Variáveis não preenchidas no .env: " + ", ".join(faltando)
    if not config.base_url.startswith(("http://", "https://")):
        return False, f"PODCAST_BASE_URL deve ser uma URL absoluta: {config.base_url!r}"
    return True, f"Publicação configurada para {config.base_url}"
