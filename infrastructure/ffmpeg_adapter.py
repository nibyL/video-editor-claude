"""
Adaptateur FFmpeg — point d'entrée UNIQUE vers FFmpeg dans toute l'application.

Règle stricte (cf. CDC section 3.5.1 et 8.4.5, actée dans notre synthèse
d'architecture) : aucun appel direct à FFmpeg ou `subprocess` depuis
services/ ou application/. Tout transite par ici.

Exigences de sécurité appliquées (CDC 8.4.5) :
- Commandes toujours construites comme liste d'arguments, jamais
  `shell=True` ni concaténation de chaîne (prévention d'injection).
- Timeout systématique sur chaque appel.
- Toute exception technique est convertie en exception métier
  (ResourceError, ValidationError) avant de remonter.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.exceptions import ResourceError, ValidationError
from core.logger import get_logger
from core.settings import AppSettings

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300


class FFmpegAdapter:
    """Interface unique vers l'exécutable FFmpeg (et ffprobe)."""

    def __init__(self) -> None:
        self._settings = AppSettings()
        self._ffmpeg_path: Optional[str] = None
        self._ffprobe_path: Optional[str] = None
        self._detect_binaries()

    # ------------------------------------------------------------------
    # Détection des exécutables
    # ------------------------------------------------------------------

    def _detect_binaries(self) -> None:
        self._ffmpeg_path = self._detect_one("ffmpeg", self._settings.ffmpeg_path)
        self._ffprobe_path = self._detect_one("ffprobe", None)

        if not self._ffmpeg_path:
            logger.error("FFmpeg introuvable")
            raise ResourceError(
                "FFmpeg est requis mais n'a pas été trouvé. Installez-le ou "
                "renseignez son chemin dans les préférences de l'application. "
                "Voir scripts/setup_ffmpeg.py."
            )

    @staticmethod
    def _detect_one(binary_name: str, explicit_path: Optional[str]) -> Optional[str]:
        if explicit_path and Path(explicit_path).exists():
            return explicit_path
        return shutil.which(binary_name)

    @property
    def is_available(self) -> bool:
        return self._ffmpeg_path is not None

    # ------------------------------------------------------------------
    # Exécution sécurisée
    # ------------------------------------------------------------------

    def _run(
        self,
        binary_path: str,
        args: list[str],
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess:
        """Exécute une commande FFmpeg/ffprobe avec gestion d'erreur uniforme.

        `args` ne doit JAMAIS être construit par concaténation de chaîne à
        partir d'une entrée utilisateur (cf. CDC 8.4.5) : chaque élément
        de la liste est passé tel quel au système, sans interprétation shell.
        """
        cmd = [binary_path] + args
        logger.debug("Commande: {}", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,  # jamais shell=True, cf. exigence sécurité 8.4.5
            )
        except FileNotFoundError as exc:
            raise ResourceError(f"Exécutable introuvable: {binary_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ResourceError(
                f"FFmpeg n'a pas répondu dans le délai imparti ({timeout}s)"
            ) from exc
        except subprocess.SubprocessError as exc:
            raise ResourceError(f"Erreur d'exécution FFmpeg: {exc}") from exc

        if result.returncode != 0:
            stderr_excerpt = (result.stderr or "")[:500]
            logger.error("FFmpeg a échoué (code {}): {}", result.returncode, stderr_excerpt)
            raise ResourceError(f"FFmpeg a échoué: {stderr_excerpt}")

        return result

    # ------------------------------------------------------------------
    # API métier (itération 0)
    # ------------------------------------------------------------------

    def get_media_info(self, file_path: Path) -> dict:
        """Extrait les métadonnées d'un média via ffprobe (durée, résolution,
        fps, codecs, bitrate) au format JSON.

        Utilisé par le pipeline d'import (itération 0/1) pour peupler
        core.models.media.Media après validation par FileValidator.
        """
        if not self._ffprobe_path:
            raise ResourceError("ffprobe introuvable (requis pour lire les métadonnées)")

        if not file_path.exists():
            raise ValidationError(f"Fichier introuvable: {file_path}")

        args = [
            "-v", "error",
            "-show_entries", "format=duration,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json",
            str(file_path),
        ]
        result = self._run(self._ffprobe_path, args, timeout=30)

        import json
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ResourceError("Sortie ffprobe illisible") from exc

    def get_media_metadata(self, file_path: Path) -> dict:
        """Version normalisée de get_media_info(), directement exploitable
        par les services (MergeService notamment) sans qu'ils aient à
        connaître le format JSON brut de ffprobe.

        Retourne un dict à clés fixes :
        {codec_video, codec_audio, width, height, fps, duration_ms, bitrate_kbps}
        Les valeurs absentes ou non interprétables restent à None.
        """
        raw = self.get_media_info(file_path)
        return self._parse_probe_output(raw)

    @staticmethod
    def _parse_probe_output(raw: dict) -> dict:
        metadata: dict = {
            "codec_video": None,
            "codec_audio": None,
            "width": None,
            "height": None,
            "fps": None,
            "duration_ms": None,
            "bitrate_kbps": None,
        }

        fmt = raw.get("format", {})
        duration = fmt.get("duration")
        if duration is not None:
            try:
                metadata["duration_ms"] = int(float(duration) * 1000)
            except (TypeError, ValueError):
                pass

        bit_rate = fmt.get("bit_rate")
        if bit_rate is not None:
            try:
                metadata["bitrate_kbps"] = int(int(bit_rate) / 1000)
            except (TypeError, ValueError):
                pass

        for stream in raw.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video" and metadata["codec_video"] is None:
                metadata["codec_video"] = stream.get("codec_name")
                metadata["width"] = stream.get("width")
                metadata["height"] = stream.get("height")
                metadata["fps"] = FFmpegAdapter._parse_frame_rate(stream.get("r_frame_rate"))
            elif codec_type == "audio" and metadata["codec_audio"] is None:
                metadata["codec_audio"] = stream.get("codec_name")

        return metadata

    @staticmethod
    def _parse_frame_rate(value: Optional[str]) -> Optional[float]:
        """ffprobe retourne le fps sous forme de fraction textuelle ("30/1",
        "30000/1001"...). Converti en float arrondi, ou None si absent/invalide.
        """
        if not value or "/" not in value:
            return None
        numerator, _, denominator = value.partition("/")
        try:
            num, den = float(numerator), float(denominator)
            return round(num / den, 3) if den else None
        except ValueError:
            return None

    def merge_videos(self, input_paths: list[Path], output_path: Path) -> Path:
        """Concatène plusieurs vidéos via le concat demuxer FFmpeg.

        Stratégie rapide sans ré-encodage quand les flux sont compatibles
        (mêmes codec/résolution) ; en cas d'échec du concat demuxer,
        MergeService bascule vers MoviePyAdapter (résolutions différentes).

        Le fichier liste temporaire requis par `-f concat` est généré via
        infrastructure (jamais un chemin construit à la main dans services/).
        """
        if len(input_paths) < 2:
            raise ValidationError("Au moins deux vidéos sont requises pour une fusion")

        for path in input_paths:
            if not path.exists():
                raise ValidationError(f"Fichier introuvable: {path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as list_file:
            for path in input_paths:
                # Échappement des apostrophes requis par la syntaxe concat demuxer
                escaped = str(path.resolve()).replace("'", "'\\''")
                list_file.write(f"file '{escaped}'\n")
            list_file_path = Path(list_file.name)

        try:
            args = [
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file_path),
                "-c", "copy",
                str(output_path),
            ]
            self._run(self._ffmpeg_path, args)
        finally:
            list_file_path.unlink(missing_ok=True)

        logger.info("Fusion FFmpeg réussie: {}", output_path)
        return output_path

    def cut_segment(
        self,
        source_path: Path,
        output_path: Path,
        start_ms: int,
        end_ms: int,
        stream_copy: bool = True,
    ) -> Path:
        """Extrait le segment [start_ms, end_ms] de source_path.

        Deux modes :
        - stream_copy=True (rapide, sans perte) : copie les flux tels quels.
          FFmpeg ne peut alors couper qu'au keyframe le plus proche, donc le
          résultat peut être décalé de quelques centaines de millisecondes
          par rapport aux bornes demandées. C'est CutService (couche
          services/) qui vérifie cet écart et décide s'il faut refaire
          l'opération en mode précis — ce module ne fait qu'exécuter FFmpeg,
          jamais la décision de stratégie.
        - stream_copy=False (plus lent, ré-encodage) : découpe image par
          image, précise à la milliseconde près.

        `-ss` est placé AVANT `-i` (recherche rapide) et la durée est
        exprimée via `-t` plutôt que `-to`, pour éviter toute ambiguïté
        sur le référentiel temporel une fois combiné à `-ss` avant l'entrée.
        """
        if not source_path.exists():
            raise ValidationError(f"Fichier introuvable: {source_path}")
        if start_ms < 0:
            raise ValidationError("start_ms ne peut pas être négatif")
        if end_ms <= start_ms:
            raise ValidationError("end_ms doit être strictement supérieur à start_ms")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        start_s = start_ms / 1000.0
        duration_s = (end_ms - start_ms) / 1000.0

        args = ["-y", "-ss", f"{start_s:.3f}", "-i", str(source_path), "-t", f"{duration_s:.3f}"]
        if stream_copy:
            args += ["-c", "copy"]
        else:
            args += ["-c:v", "libx264", "-c:a", "aac"]
        args.append(str(output_path))

        self._run(self._ffmpeg_path, args)
        logger.info(
            "Découpe FFmpeg réussie ({}): {} [{} ms -> {} ms]",
            "copie" if stream_copy else "ré-encodage", output_path, start_ms, end_ms,
        )
        return output_path

    def export_video(
        self,
        source_path: Path,
        output_path: Path,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
        fps: Optional[float] = None,
    ) -> Path:
        """Ré-encode/exporte une vidéo. Implémentation de base pour
        l'itération 0 ; les options avancées (bitrate, presets plateforme,
        sous-titres incrustés) arrivent avec ExportOptions aux itérations
        suivantes.
        """
        if not source_path.exists():
            raise ValidationError(f"Source introuvable: {source_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        args = ["-y", "-i", str(source_path), "-c:v", video_codec, "-c:a", audio_codec]
        if fps:
            args += ["-r", str(fps)]
        args.append(str(output_path))

        self._run(self._ffmpeg_path, args)
        logger.info("Export FFmpeg réussi: {}", output_path)
        return output_path
