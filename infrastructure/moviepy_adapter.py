"""
Adaptateur MoviePy — point d'entrée UNIQUE vers MoviePy dans toute l'application.

Cf. CDC section 3.5 (Stratégie de dépendance vidéo) : MoviePy n'est pas
déprécié mais présente un risque de maintenance en sous-effectif. La
mitigation retenue est une encapsulation stricte :

- Aucun appel direct à `moviepy.editor.*` depuis services/ ou application/.
- L'interface exposée ici (merge_videos, export_video...) est définie
  selon les besoins métier, pas selon les primitives MoviePy — ce qui
  permet le plan de bascule vers FFmpeg/libavfilter direct (CDC 3.5.2)
  sans toucher aux couches supérieures.
- Version épinglée dans requirements.txt, avec tests de non-régression
  dédiés (cf. CDC 3.5.1) à écrire au fil des itérations.

Utilisé en complément de FFmpegAdapter pour les opérations que le concat
demuxer FFmpeg ne gère pas bien (résolutions hétérogènes, transitions,
effets — itérations 5+).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.exceptions import ExportError, MergeError, ResourceError, ValidationError
from core.logger import get_logger

logger = get_logger(__name__)


class MoviePyAdapter:
    """Interface unique vers MoviePy."""

    def __init__(self) -> None:
        self._check_moviepy_installed()

    @staticmethod
    def _check_moviepy_installed() -> None:
        try:
            import moviepy  # noqa: F401
        except ImportError as exc:
            raise ResourceError(
                "MoviePy est requis mais n'est pas installé. "
                "Exécutez: pip install -r requirements.txt"
            ) from exc

    # ------------------------------------------------------------------
    # API métier (itération 0)
    # ------------------------------------------------------------------

    def merge_videos(
        self,
        input_paths: list[Path],
        output_path: Path,
        target_resolution: Optional[tuple[int, int]] = None,
    ) -> Path:
        """Concatène plusieurs vidéos, y compris de résolutions différentes.

        Contrairement à FFmpegAdapter.merge_videos (concat demuxer, rapide
        mais exige des flux compatibles), cette méthode ré-encode et peut
        harmoniser les résolutions — c'est le cas d'usage principal justifiant
        de garder MoviePy en complément de FFmpeg pour l'itération 0.
        """
        for path in input_paths:
            if not path.exists():
                raise ValidationError(f"Fichier introuvable: {path}")

        if len(input_paths) < 2:
            raise ValidationError("Au moins deux vidéos sont requises pour une fusion")

        from moviepy import VideoFileClip, concatenate_videoclips

        clips = []
        try:
            for path in input_paths:
                clip = VideoFileClip(str(path))
                if target_resolution and (clip.w, clip.h) != tuple(target_resolution):
                    clip = clip.resized(target_resolution)
                clips.append(clip)

            final_clip = concatenate_videoclips(clips, method="compose")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            final_clip.write_videofile(
                str(output_path),
                codec="libx264",
                audio_codec="aac",
                logger=None,  # évite la barre de progression brute de MoviePy en console
            )
            final_clip.close()
        except Exception as exc:  # bibliothèque tierce : on convertit systématiquement
            logger.error("Échec de la fusion MoviePy: {}", exc)
            raise MergeError(f"La fusion a échoué: {exc}") from exc
        finally:
            # Fermeture systématique des ressources, même en cas d'erreur
            # (cf. exigence CDC : "ressources vidéo fermées correctement").
            for clip in clips:
                try:
                    clip.close()
                except Exception:
                    pass

        logger.info("Fusion MoviePy réussie: {}", output_path)
        return output_path

    def export_video(
        self,
        source_path: Path,
        output_path: Path,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
    ) -> Path:
        """Exporte un clip source vers un fichier. Implémentation de base ;
        les presets plateforme (résolution/FPS/bitrate) arrivent avec
        ExportOptions aux itérations suivantes.
        """
        if not source_path.exists():
            raise ValidationError(f"Source introuvable: {source_path}")

        from moviepy import VideoFileClip

        clip = None
        try:
            clip = VideoFileClip(str(source_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            clip.write_videofile(
                str(output_path),
                codec=video_codec,
                audio_codec=audio_codec,
                logger=None,
            )
        except Exception as exc:
            logger.error("Échec de l'export MoviePy: {}", exc)
            raise ExportError(f"L'export a échoué: {exc}") from exc
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass

        logger.info("Export MoviePy réussi: {}", output_path)
        return output_path
