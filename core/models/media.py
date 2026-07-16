"""
Modèle de données pour un média importé.

Fondation du domaine — aucune dépendance externe (ni FFmpeg, ni MoviePy,
ni Qt). Correspond à l'entité Média définie au CDC section 6.1.

Ce module ne fait AUCUNE validation de fichier (c'est le rôle de
infrastructure/file_validator.py) : un Media peut être construit avec
des métadonnées encore inconnues (None), remplies au fur et à mesure
du pipeline d'import.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class MediaType(str, Enum):
    """Cf. CDC 6.1, entité Média, attribut `type`."""

    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class MediaStatus(str, Enum):
    """Cf. CDC 6.1, entité Média, attribut `status`."""

    AVAILABLE = "available"
    MISSING = "missing"
    CONVERTING = "converting"
    REJECTED = "rejected"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Media:
    """Représente un média importé dans le projet.

    Les champs obligatoires au CDC (id, project_id, type, original_path,
    file_size_bytes, status, imported_at) sont non-optionnels ici.
    Les métadonnées techniques (durée, résolution, codec...) sont
    Optional car elles ne sont connues qu'après extraction par
    l'adaptateur FFmpeg/ffprobe (itération 0, à venir).
    """

    project_id: str
    original_path: Path
    type: MediaType
    file_size_bytes: int

    id: str = field(default_factory=_new_id)
    status: MediaStatus = MediaStatus.AVAILABLE
    imported_at: str = field(default_factory=_now_iso)

    # Chemins dérivés, générés par l'application (jamais fournis à la main)
    proxy_path: Optional[Path] = None
    thumbnail_path: Optional[Path] = None

    # Métadonnées extraites à l'import (cf. ffprobe, itération 0)
    duration_ms: Optional[int] = None
    resolution_w: Optional[int] = None
    resolution_h: Optional[int] = None
    fps: Optional[float] = None
    codec_video: Optional[str] = None
    codec_audio: Optional[str] = None
    bitrate_kbps: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.original_path, Path):
            self.original_path = Path(self.original_path)
        if self.proxy_path is not None and not isinstance(self.proxy_path, Path):
            self.proxy_path = Path(self.proxy_path)
        if self.thumbnail_path is not None and not isinstance(self.thumbnail_path, Path):
            self.thumbnail_path = Path(self.thumbnail_path)
        if self.file_size_bytes < 0:
            raise ValueError("file_size_bytes ne peut pas être négatif")

    def is_available(self) -> bool:
        """Vérifie que le fichier source existe toujours sur le disque.

        Ne modifie pas `status` : c'est à l'appelant (use case) de décider
        de marquer le média MISSING et de proposer un re-lien, cf. CDC 8.2.
        """
        return self.original_path.exists()

    @property
    def resolution(self) -> Optional[str]:
        """Retourne '1920x1080' ou None si les dimensions ne sont pas connues."""
        if self.resolution_w and self.resolution_h:
            return f"{self.resolution_w}x{self.resolution_h}"
        return None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.duration_ms is None:
            return None
        return self.duration_ms / 1000.0
