"""
Service de fusion vidéo — décision automatique copie directe / ré-encodage.

Principe : deux vidéos ne peuvent être concaténées par simple copie de flux
(FFmpeg concat demuxer, `-c copy`, rapide, sans perte) que si elles ont
EXACTEMENT le même codec vidéo, le même codec audio, la même résolution et
le même fps. Dans tous les autres cas, il faut ré-encoder pour harmoniser
les flux avant de les assembler (MoviePy, plus lent mais tolérant).

Ce service :
1. Valide chaque média source (FileValidator).
2. Complète automatiquement les métadonnées manquantes (résolution, fps,
   codecs) en sondant le fichier via FFmpegAdapter.get_media_metadata(),
   si elles ne sont pas déjà connues sur l'objet Media.
3. Détermine si tous les médias sont compatibles pour une copie directe.
4. Choisit la stratégie en conséquence — copie directe (FFmpeg, rapide)
   ou ré-encodage (MoviePy, avec harmonisation de résolution).
5. Si la copie directe échoue malgré des métadonnées compatibles (cas rare :
   keyframes mal alignées, conteneur capricieux...), replie automatiquement
   sur le ré-encodage plutôt que d'échouer.

Aucune logique technique directe ici : tout passe par infrastructure/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.exceptions import MergeError, ResourceError, ValidationError
from core.logger import get_logger
from core.models.media import Media
from infrastructure.ffmpeg_adapter import FFmpegAdapter
from infrastructure.file_validator import FileValidator
from infrastructure.moviepy_adapter import MoviePyAdapter

logger = get_logger(__name__)

# Champs qui doivent être strictement identiques entre tous les médias
# pour autoriser une copie directe (concat demuxer, sans ré-encodage).
# codec_audio EST comparé (None vs "aac" doit forcer un ré-encodage), mais
# n'est PAS dans la liste des champs "obligatoires" ci-dessous : une vidéo
# sans piste audio (écran de fond uni, capture muette...) a légitimement
# codec_audio = None après un sondage RÉUSSI, ce n'est pas une métadonnée
# inconnue. Deux vidéos sans audio sont compatibles entre elles.
_STRICT_COMPATIBILITY_FIELDS = ("codec_video", "codec_audio", "resolution_w", "resolution_h")

# Champs qu'une vidéo valide possède TOUJOURS (contrairement à l'audio, qui
# est optionnel). S'ils restent None après sondage, c'est le signe d'un
# échec réel de lecture des métadonnées, pas d'une absence légitime.
_METADATA_FIELDS_REQUIRED_FOR_DECISION = ("codec_video", "resolution_w", "resolution_h", "fps")

# Tolérance sur le fps : évite de déclencher un ré-encodage coûteux pour
# un écart d'arrondi négligeable (ex. 29.97 vs 29.970001 selon l'encodeur).
_FPS_TOLERANCE = 0.05

# Champs requis pour statuer sur la compatibilité (défini ci-dessus).


class MergeService:
    """Fusionne plusieurs médias vidéo, en choisissant automatiquement
    entre copie directe et ré-encodage selon leurs caractéristiques.
    """

    def __init__(
        self,
        ffmpeg_adapter: Optional[FFmpegAdapter] = None,
        moviepy_adapter: Optional[MoviePyAdapter] = None,
    ) -> None:
        # Injection optionnelle pour les tests ; instanciation paresseuse
        # par défaut pour ne pas exiger FFmpeg installé juste pour
        # construire un MergeService en mémoire.
        self._ffmpeg_adapter = ffmpeg_adapter
        self._moviepy_adapter = moviepy_adapter

    @property
    def ffmpeg(self) -> FFmpegAdapter:
        if self._ffmpeg_adapter is None:
            self._ffmpeg_adapter = FFmpegAdapter()
        return self._ffmpeg_adapter

    @property
    def moviepy(self) -> MoviePyAdapter:
        if self._moviepy_adapter is None:
            self._moviepy_adapter = MoviePyAdapter()
        return self._moviepy_adapter

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def merge(
        self,
        media_list: list[Media],
        output_path: Path,
        working_dir: Optional[Path] = None,
    ) -> Path:
        """Fusionne une liste de médias, dans l'ordre fourni.

        Args:
            media_list: médias à fusionner, dans l'ordre de la timeline.
            output_path: chemin du fichier de sortie.
            working_dir: dossier de travail du projet, transmis au
                         FileValidator pour la protection anti path-traversal.

        Returns:
            Le chemin du fichier fusionné (== output_path).

        Raises:
            ValidationError: si moins de deux médias, ou un média invalide.
            MergeError: si la stratégie choisie échoue et que le repli échoue aussi.
        """
        if len(media_list) < 2:
            raise ValidationError(
                "Au moins deux médias sont nécessaires pour une fusion"
            )

        for media in media_list:
            FileValidator.validate(media.original_path, working_dir=working_dir)

        for media in media_list:
            self._ensure_metadata(media)

        paths = [m.original_path for m in media_list]

        if self._can_stream_copy(media_list):
            logger.info(
                "Médias compatibles (même codec/résolution/fps) -> copie directe sans ré-encodage"
            )
            try:
                self.ffmpeg.merge_videos(paths, output_path)
                return output_path
            except ResourceError as exc:
                logger.warning(
                    "Copie directe impossible malgré des métadonnées compatibles ({}), "
                    "repli sur le ré-encodage",
                    exc,
                )
        else:
            logger.info("Médias hétérogènes -> ré-encodage nécessaire pour harmoniser les flux")

        target_resolution = self._pick_target_resolution(media_list)
        try:
            self.moviepy.merge_videos(paths, output_path, target_resolution=target_resolution)
            return output_path
        except Exception as exc:
            logger.error("Ré-encodage également en échec: {}", exc)
            raise MergeError(f"La fusion a échoué: {exc}") from exc

    # ------------------------------------------------------------------
    # Sondage automatique des métadonnées
    # ------------------------------------------------------------------

    def _ensure_metadata(self, media: Media) -> None:
        """Complète les champs de métadonnées manquants sur `media` en
        sondant le fichier via ffprobe, si nécessaire.

        Ne fait rien si toutes les métadonnées utiles à la décision sont
        déjà connues (cas d'un média déjà importé et indexé, itération 3+).
        """
        needs_probe = any(
            getattr(media, field) is None for field in _METADATA_FIELDS_REQUIRED_FOR_DECISION
        )
        if not needs_probe:
            return

        try:
            metadata = self.ffmpeg.get_media_metadata(media.original_path)
        except ResourceError as exc:
            logger.warning(
                "Métadonnées illisibles pour {} ({}) — ce média sera traité "
                "comme incompatible par prudence",
                media.original_path, exc,
            )
            return

        media.codec_video = media.codec_video or metadata.get("codec_video")
        media.codec_audio = media.codec_audio or metadata.get("codec_audio")
        media.resolution_w = media.resolution_w or metadata.get("width")
        media.resolution_h = media.resolution_h or metadata.get("height")
        media.fps = media.fps or metadata.get("fps")
        media.duration_ms = media.duration_ms or metadata.get("duration_ms")
        media.bitrate_kbps = media.bitrate_kbps or metadata.get("bitrate_kbps")

    # ------------------------------------------------------------------
    # Décision de stratégie
    # ------------------------------------------------------------------

    @staticmethod
    def _can_stream_copy(media_list: list[Media]) -> bool:
        """Détermine si tous les médias partagent exactement le même
        codec vidéo, codec audio, résolution, et un fps équivalent
        (tolérance _FPS_TOLERANCE).

        Si l'un des champs requis est encore inconnu après sondage
        (_ensure_metadata a échoué ou le fichier ne l'expose pas), on
        retourne False par prudence plutôt que de tenter une copie qui
        risquerait de produire un fichier corrompu ou un échec FFmpeg.
        """
        reference = media_list[0]
        if any(getattr(reference, f) is None for f in _METADATA_FIELDS_REQUIRED_FOR_DECISION):
            return False

        for media in media_list[1:]:
            if any(getattr(media, f) is None for f in _METADATA_FIELDS_REQUIRED_FOR_DECISION):
                return False
            if any(
                getattr(media, f) != getattr(reference, f)
                for f in _STRICT_COMPATIBILITY_FIELDS
            ):
                return False
            if abs(media.fps - reference.fps) > _FPS_TOLERANCE:
                return False

        return True

    @staticmethod
    def _pick_target_resolution(media_list: list[Media]) -> Optional[tuple[int, int]]:
        """Choisit la résolution cible pour le ré-encodage : la plus
        grande résolution parmi les médias connus, pour éviter de
        dégrader arbitrairement la meilleure source.

        Affiné à l'itération 3 : la résolution du projet (définie à la
        création, cf. entité Projet) primera sur cette heuristique.
        """
        candidates = [
            (m.resolution_w, m.resolution_h)
            for m in media_list
            if m.resolution_w and m.resolution_h
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda wh: wh[0] * wh[1])
