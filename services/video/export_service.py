"""
Service d'export vidéo — itération 0.

Le dossier/nom de sortie est TOUJOURS fourni par l'appelant (UI ou CLI) :
aucun dossier de sortie imposé (cf. décision actée — CDC section 5.5,
l'utilisateur choisit sa destination via un dialogue).

ExportOptions est une classe de valeur immuable séparée du service, pour
préfigurer l'entité de configuration d'export sans dépendre de la couche
persistance (storage/, itération 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.exceptions import ExportError, ValidationError
from core.logger import get_logger
from core.settings import AppSettings
from infrastructure.ffmpeg_adapter import FFmpegAdapter

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExportOptions:
    """Options d'export. Immuable : toute modification crée une nouvelle
    instance (`dataclasses.replace`), évitant les effets de bord partagés
    entre plusieurs exports lancés successivement.
    """

    resolution: str = "1920x1080"
    fps: float = 30.0
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    quality: str = "high"  # low | medium | high | lossless — affiné itération 14/18


class ExportService:
    """Exporte une vidéo source vers un fichier final, selon les options
    choisies. Le chemin de sortie est toujours explicite, jamais déduit.
    """

    def __init__(self, ffmpeg_adapter: Optional[FFmpegAdapter] = None) -> None:
        self._ffmpeg_adapter = ffmpeg_adapter
        self._settings = AppSettings()

    @property
    def ffmpeg(self) -> FFmpegAdapter:
        if self._ffmpeg_adapter is None:
            self._ffmpeg_adapter = FFmpegAdapter()
        return self._ffmpeg_adapter

    def export(
        self,
        source_path: Path,
        output_path: Path,
        options: Optional[ExportOptions] = None,
    ) -> Path:
        """Exporte `source_path` vers `output_path`.

        Args:
            source_path: vidéo source (résultat d'une fusion/timeline rendue).
            output_path: chemin de sortie choisi par l'utilisateur.
            options: paramètres d'export ; valeurs par défaut si omis.

        Returns:
            Le chemin exporté.

        Raises:
            ValidationError: source introuvable.
            ExportError: échec du pipeline d'export FFmpeg.
        """
        if not source_path.exists():
            raise ValidationError(f"Source introuvable: {source_path}")

        if output_path.exists():
            logger.warning("Le fichier de sortie existe déjà, il sera écrasé: {}", output_path)

        options = options or ExportOptions()

        logger.info(
            "Export vers {} ({}, {} fps, codec {})",
            output_path, options.resolution, options.fps, options.video_codec,
        )

        try:
            self.ffmpeg.export_video(
                source_path,
                output_path,
                video_codec=options.video_codec,
                audio_codec=options.audio_codec,
                fps=options.fps,
            )
        except Exception as exc:
            raise ExportError(f"L'export a échoué: {exc}") from exc

        # Mémorise le dossier utilisé pour le prochain export (préférence
        # utilisateur persistée via AppSettings/QSettings, pas un dossier imposé).
        self._settings.last_output_folder = str(output_path.parent)

        return output_path

    def get_platform_presets(self) -> dict[str, ExportOptions]:
        """Presets par plateforme sociale — squelette pour l'itération 10
        (Formats réseaux sociaux). Volontairement minimal ici : seuls
        deux presets illustratifs, complétés au moment de cette itération
        plutôt que définis en totalité maintenant (YAGNI).
        """
        return {
            "youtube_16_9": ExportOptions(resolution="1920x1080", fps=30.0),
            "tiktok_9_16": ExportOptions(resolution="1080x1920", fps=30.0),
        }
