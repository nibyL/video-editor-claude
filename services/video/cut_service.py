"""
Service de découpe vidéo — décision automatique copie directe / ré-encodage.

Contrairement à MergeService (où l'incompatibilité se détecte À L'AVANCE
en comparant les métadonnées), la découpe a un problème différent : FFmpeg
en mode copie de flux (`-c copy`) ne peut couper qu'au keyframe le plus
proche, donc on ne sait PAS à l'avance si le résultat sera assez précis —
il faut essayer, puis vérifier le résultat réel.

Stratégie retenue, cohérente avec le principe déjà appliqué dans
MergeService (rapide d'abord, vérifié, repli si besoin) :

1. Tenter la copie directe (rapide, sans perte de qualité).
2. Sonder la durée réelle du fichier produit.
3. Si l'écart avec la durée demandée dépasse la tolérance, refaire
   l'opération en mode ré-encodage (précis à la milliseconde près).

Un seul adaptateur est nécessaire ici (FFmpegAdapter) : contrairement à
la fusion, la découpe ne implique qu'une seule source, donc aucun besoin
d'harmoniser des résolutions différentes via MoviePy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.exceptions import ResourceError, ValidationError
from core.logger import get_logger
from core.models.media import Media
from infrastructure.ffmpeg_adapter import FFmpegAdapter
from infrastructure.file_validator import FileValidator

logger = get_logger(__name__)

# Écart maximal toléré (ms) entre la durée demandée et la durée réelle du
# segment produit en mode copie directe, avant de considérer que le résultat
# est trop imprécis et de basculer en ré-encodage. 150 ms couvre la plupart
# des intervalles inter-keyframes usuels (GOP de 1 à 5s) tout en restant
# perceptible si on le laissait passer tel quel.
_PRECISION_TOLERANCE_MS = 150


class CutService:
    """Découpe un média vidéo : extraction d'un segment ou division en deux."""

    def __init__(self, ffmpeg_adapter: Optional[FFmpegAdapter] = None) -> None:
        self._ffmpeg_adapter = ffmpeg_adapter

    @property
    def ffmpeg(self) -> FFmpegAdapter:
        if self._ffmpeg_adapter is None:
            self._ffmpeg_adapter = FFmpegAdapter()
        return self._ffmpeg_adapter

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def cut_start_end(
        self,
        media: Media,
        start_ms: int,
        end_ms: int,
        output_path: Path,
        working_dir: Optional[Path] = None,
    ) -> Path:
        """Extrait le segment [start_ms, end_ms] du média source.

        Args:
            media: média source à découper.
            start_ms: position de début du segment, en millisecondes.
            end_ms: position de fin du segment, en millisecondes.
            output_path: chemin du fichier de sortie.
            working_dir: dossier de travail du projet, transmis au
                         FileValidator (protection anti path-traversal).

        Returns:
            Le chemin du segment produit (== output_path).

        Raises:
            ValidationError: bornes invalides, média invalide, ou bornes
                              hors de la durée réelle du média (si connue).
        """
        FileValidator.validate(media.original_path, working_dir=working_dir)
        self._validate_bounds(media, start_ms, end_ms)

        requested_duration_ms = end_ms - start_ms
        logger.info(
            "Découpe de {} [{} ms -> {} ms] (durée demandée: {} ms)",
            media.original_path, start_ms, end_ms, requested_duration_ms,
        )

        self.ffmpeg.cut_segment(
            media.original_path, output_path, start_ms, end_ms, stream_copy=True
        )

        if self._is_precise_enough(output_path, requested_duration_ms):
            logger.info("Copie directe suffisamment précise, aucun ré-encodage nécessaire")
            return output_path

        logger.info(
            "Copie directe imprécise (écart > {} ms), reprise en mode ré-encodage",
            _PRECISION_TOLERANCE_MS,
        )
        self.ffmpeg.cut_segment(
            media.original_path, output_path, start_ms, end_ms, stream_copy=False
        )
        return output_path

    def split_at(
        self,
        media: Media,
        position_ms: int,
        output_path_before: Path,
        output_path_after: Path,
        working_dir: Optional[Path] = None,
    ) -> tuple[Path, Path]:
        """Divise le média en deux fichiers, à la position donnée.

        Args:
            media: média source. Sa durée totale doit être connue
                   (media.duration_ms), sinon elle est sondée automatiquement.
            position_ms: position de la coupure, en millisecondes.
            output_path_before: chemin du segment [0, position_ms].
            output_path_after: chemin du segment [position_ms, fin].

        Returns:
            Le couple (chemin_avant, chemin_après).

        Raises:
            ValidationError: position hors de la durée du média.
        """
        FileValidator.validate(media.original_path, working_dir=working_dir)

        if media.duration_ms is None:
            self._probe_duration(media)

        if media.duration_ms is None:
            raise ValidationError(
                "Impossible de déterminer la durée du média pour diviser à cette position"
            )

        if not (0 < position_ms < media.duration_ms):
            raise ValidationError(
                f"La position de division ({position_ms} ms) doit être strictement "
                f"comprise entre 0 et la durée du média ({media.duration_ms} ms)"
            )

        before = self.cut_start_end(
            media, 0, position_ms, output_path_before, working_dir=working_dir
        )
        after = self.cut_start_end(
            media, position_ms, media.duration_ms, output_path_after, working_dir=working_dir
        )
        return before, after

    # ------------------------------------------------------------------
    # Validation et sondage
    # ------------------------------------------------------------------

    def _validate_bounds(self, media: Media, start_ms: int, end_ms: int) -> None:
        if start_ms < 0:
            raise ValidationError("start_ms ne peut pas être négatif")
        if end_ms <= start_ms:
            raise ValidationError("end_ms doit être strictement supérieur à start_ms")

        if media.duration_ms is None:
            self._probe_duration(media)

        if media.duration_ms is not None and end_ms > media.duration_ms:
            raise ValidationError(
                f"end_ms ({end_ms} ms) dépasse la durée du média ({media.duration_ms} ms)"
            )

    def _probe_duration(self, media: Media) -> None:
        """Sonde la durée réelle du média si elle n'est pas déjà connue,
        et la reporte sur l'objet Media (même logique que MergeService).
        """
        try:
            metadata = self.ffmpeg.get_media_metadata(media.original_path)
            media.duration_ms = media.duration_ms or metadata.get("duration_ms")
        except ResourceError as exc:
            logger.warning(
                "Durée illisible pour {} ({}), impossible de valider les bornes",
                media.original_path, exc,
            )

    def _is_precise_enough(self, output_path: Path, requested_duration_ms: int) -> bool:
        """Vérifie que la durée réelle du fichier produit par la copie
        directe est suffisamment proche de la durée demandée.
        """
        try:
            metadata = self.ffmpeg.get_media_metadata(output_path)
        except ResourceError as exc:
            logger.warning(
                "Impossible de vérifier la précision du résultat ({}), "
                "ré-encodage effectué par prudence",
                exc,
            )
            return False

        actual_duration_ms = metadata.get("duration_ms")
        if actual_duration_ms is None:
            return False

        deviation_ms = abs(actual_duration_ms - requested_duration_ms)
        return deviation_ms <= _PRECISION_TOLERANCE_MS
