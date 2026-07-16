"""
Gestion des chemins de l'application.

Centralise la résolution des chemins pour éviter les chemins en dur
dispersés dans le code, en particulier :
- Le dossier temporaire, qui doit être résolu dynamiquement selon l'OS
  (JAMAIS un chemin relatif à l'exécutable — casserait après packaging
  Nuitka/PyInstaller, cf. décision actée dans la synthèse d'architecture).
- La conversion chemin absolu <-> relatif pour la portabilité des fichiers
  projet .vproj (un projet déplacé sur une autre machine doit pouvoir
  retrouver ses médias si la structure relative est préservée).
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Optional


class PathManager:
    """Résout les chemins temporaires et projet.

    Une instance par session applicative suffit ; le dossier de projet
    change au fil des ouvertures/fermetures de projet (itération 3),
    d'où le setter `project_folder`.
    """

    def __init__(self, project_folder: Optional[Path] = None) -> None:
        self._project_folder: Optional[Path] = (
            project_folder.resolve() if project_folder else None
        )
        self._temp_dir: Optional[Path] = None

    # ------------------------------------------------------------------
    # Dossier de projet
    # ------------------------------------------------------------------

    @property
    def project_folder(self) -> Optional[Path]:
        return self._project_folder

    @project_folder.setter
    def project_folder(self, path: Path) -> None:
        self._project_folder = path.resolve()

    # ------------------------------------------------------------------
    # Dossier temporaire (résolution dynamique selon l'OS)
    # ------------------------------------------------------------------

    @property
    def temp_dir(self) -> Path:
        """Dossier temporaire dédié à l'application, résolu via le
        mécanisme standard de l'OS (tempfile.gettempdir()) :
        %TEMP%\\VideoEditor sous Windows, /tmp/video_editor sous Linux/macOS.

        Ne dépend jamais de l'emplacement de l'exécutable, ce qui reste
        valide après packaging Nuitka ou PyInstaller (dossier souvent
        en lecture seule dans Program Files).
        """
        if self._temp_dir is None:
            base = Path(tempfile.gettempdir()) / "video_editor"
            base.mkdir(parents=True, exist_ok=True)
            self._temp_dir = base
        return self._temp_dir

    def get_temp_file(self, suffix: str = ".mp4") -> Path:
        """Génère un chemin de fichier temporaire unique (non créé sur
        le disque, juste le chemin) — utilisé par les adaptateurs pour
        les fichiers intermédiaires de fusion/export.
        """
        return self.temp_dir / f"{uuid.uuid4().hex}{suffix}"

    def clear_temp_dir(self) -> None:
        """Nettoie le dossier temporaire. À appeler au démarrage de
        l'application (cf. structure du projet : temp/ "nettoyé au démarrage").
        """
        import shutil

        if self._temp_dir is not None and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Portabilité des chemins de projet
    # ------------------------------------------------------------------

    def resolve_media_path(self, relative_or_absolute_path: Path) -> Path:
        """Convertit un chemin (potentiellement relatif, tel que stocké
        dans le fichier .vproj) en chemin absolu utilisable.
        """
        if relative_or_absolute_path.is_absolute():
            return relative_or_absolute_path

        if self._project_folder is None:
            raise ValueError(
                "Aucun dossier de projet défini pour résoudre le chemin relatif"
            )

        return (self._project_folder / relative_or_absolute_path).resolve()

    def make_relative(self, absolute_path: Path) -> Path:
        """Convertit un chemin absolu en relatif au dossier projet, pour
        la portabilité du fichier .vproj entre machines. Si le fichier
        est hors du dossier projet, conserve l'absolu (ex. médias
        partagés sur un autre disque).
        """
        if self._project_folder is None:
            raise ValueError("Aucun dossier de projet défini")

        try:
            return absolute_path.resolve().relative_to(self._project_folder)
        except ValueError:
            return absolute_path

    @staticmethod
    def ensure_parent_directory(path: Path) -> Path:
        """Crée le dossier parent d'un chemin de fichier si nécessaire.
        Retourne le chemin inchangé, pour un usage en chaîne.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
