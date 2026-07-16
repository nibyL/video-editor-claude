"""
Configuration centralisée du logging via Loguru (cf. CDC section 3.1).

Deux sorties :
- Console : niveau INFO, format court et lisible pendant le développement.
- Fichier rotatif : niveau DEBUG, format détaillé, rotation et rétention
  automatiques, dans le dossier logs/ à la racine du projet.

Usage dans le reste de l'application :

    from core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Fusion démarrée")
    logger.error("FFmpeg a échoué: {}", stderr)

Aucune donnée personnelle identifiable ne doit être journalisée
(cf. CDC section 8.3) : ne jamais logger le contenu d'un fichier utilisateur,
et anonymiser les chemins dans les rapports d'erreur partagés.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as _logger

_configured = False


def _log_dir() -> Path:
    """Dossier logs/ à la racine du projet (à côté de ce fichier core/)."""
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir


def configure_logging(debug: bool = False) -> None:
    """Configure Loguru une seule fois pour toute l'application.

    Args:
        debug: si True, affiche aussi le niveau DEBUG en console
               (utile en développement, cf. VIDEO_EDITOR_DEBUG dans .env.example).
    """
    global _configured
    if _configured:
        return

    _logger.remove()  # retire le handler par défaut de Loguru

    console_level = "DEBUG" if debug else "INFO"
    _logger.add(
        sys.stdout,
        level=console_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan> - <level>{message}</level>"
        ),
        colorize=True,
    )

    _logger.add(
        _log_dir() / "video_editor.log",
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )

    _configured = True


def get_logger(name: str | None = None):
    """Retourne le logger configuré, éventuellement lié à un nom de module.

    Args:
        name: généralement __name__ du module appelant. Sert de contexte
              dans les logs (colonne {name} du format ci-dessus).
    """
    if not _configured:
        configure_logging()
    return _logger.bind(module=name) if name else _logger
