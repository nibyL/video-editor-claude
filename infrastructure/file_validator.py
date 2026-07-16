"""
Validation des fichiers média — couche Infrastructure (cf. CDC section 8.4).

Positionné ici et non dans utils/ car c'est un composant de sécurité,
premier rempart contre les fichiers non fiables (grille STRIDE, CDC 8.4.1) :
tout fichier importé doit passer par FileValidator.validate() AVANT
d'être exposé au reste de l'application.

Contrôles appliqués (cf. CDC 8.4.1 à 8.4.3) :
- Usurpation (Spoofing) : détection du type réel par signature binaire
  (python-magic), indépendamment de l'extension déclarée.
- Divulgation d'information : rejet de tout chemin résolu hors du
  dossier de travail autorisé (traversée de répertoire ../..).
- Déni de service : plafond de taille de fichier avant tout décodage complet.

Ce module ne dépend ni de FFmpeg ni de MoviePy : il ne fait que des
vérifications sur le système de fichiers, ce qui le rend testable
instantanément et en isolation totale (cf. tests/unit/test_file_validator.py).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

from core.exceptions import SecurityError, ValidationError
from core.logger import get_logger
from core.models.media import MediaType

logger = get_logger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg"}

ALL_SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

# Plafond par défaut, configurable plus tard via AppSettings (CDC 8.4.3).
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 Go

# Signatures binaires (magic bytes) minimales pour les formats vidéo/audio
# les plus courants. Utilisé comme filet de sécurité si python-magic n'est
# pas disponible sur la machine ; python-magic reste la méthode primaire
# (cf. requirements.txt).
_KNOWN_SIGNATURES: dict[bytes, str] = {
    b"\x00\x00\x00\x18ftyp": "video/mp4",
    b"\x00\x00\x00\x20ftyp": "video/mp4",
    b"RIFF": "audio/wav-or-avi",  # désambiguïsé ensuite par le contenu
    b"\x1aE\xdf\xa3": "video/webm-or-mkv",
    b"ID3": "audio/mp3",
    b"\xff\xfb": "audio/mp3",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


class FileValidator:
    """Valide un fichier avant toute intégration à la bibliothèque média.

    Toutes les méthodes sont statiques : ce composant n'a pas d'état,
    ce qui simplifie son usage depuis n'importe quelle couche autorisée
    (application/use_cases, jamais directement depuis ui/).
    """

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    @staticmethod
    def validate(file_path: Path, working_dir: Optional[Path] = None) -> None:
        """Valide un fichier. Lève une exception en cas de rejet.

        Args:
            file_path: chemin du fichier à valider.
            working_dir: si fourni, le fichier doit être physiquement
                         situé sous ce dossier (protection anti path-traversal,
                         cf. CDC 8.4.1 "Divulgation d'information").

        Raises:
            ValidationError: fichier absent, vide, trop volumineux,
                              extension non supportée.
            SecurityError: signature usurpée ou tentative de sortie du
                            dossier de travail.
        """
        FileValidator._check_exists(file_path)
        FileValidator._check_is_file(file_path)

        if working_dir is not None:
            FileValidator._check_within_working_dir(file_path, working_dir)

        FileValidator._check_extension(file_path)
        FileValidator._check_size(file_path)
        FileValidator._check_signature_matches_extension(file_path)

    # ------------------------------------------------------------------
    # Contrôles individuels
    # ------------------------------------------------------------------

    @staticmethod
    def _check_exists(file_path: Path) -> None:
        if not file_path.exists():
            raise ValidationError(f"Fichier introuvable: {file_path}")

    @staticmethod
    def _check_is_file(file_path: Path) -> None:
        if not file_path.is_file():
            raise ValidationError(f"Ce n'est pas un fichier: {file_path}")

    @staticmethod
    def _check_within_working_dir(file_path: Path, working_dir: Path) -> None:
        """Empêche toute traversée de répertoire (../..) hors du dossier
        de travail autorisé. Canonicalisation stricte via resolve().
        """
        resolved_file = file_path.resolve()
        resolved_root = working_dir.resolve()
        if resolved_root not in resolved_file.parents and resolved_file != resolved_root:
            logger.warning(
                "Tentative d'accès hors du dossier de travail: {}", resolved_file
            )
            raise SecurityError(
                "Le chemin du fichier sort du dossier de travail autorisé."
            )

    @staticmethod
    def _check_extension(file_path: Path) -> None:
        if file_path.suffix.lower() not in ALL_SUPPORTED_EXTENSIONS:
            raise ValidationError(f"Format non supporté: {file_path.suffix}")

    @staticmethod
    def _check_size(file_path: Path) -> None:
        size = file_path.stat().st_size
        if size == 0:
            raise ValidationError("Fichier vide")
        if size > MAX_FILE_SIZE_BYTES:
            size_mb = size / (1024 * 1024)
            max_gb = MAX_FILE_SIZE_BYTES / (1024 * 1024 * 1024)
            raise ValidationError(
                f"Fichier trop volumineux: {size_mb:.0f} Mo (maximum {max_gb:.0f} Go)"
            )

    @staticmethod
    def _check_signature_matches_extension(file_path: Path) -> None:
        """Détecte le type réel par signature binaire, indépendamment de
        l'extension déclarée (protection contre le spoofing, CDC 8.4.1).

        Utilise python-magic si disponible ; sinon repli sur une lecture
        des premiers octets comparée à _KNOWN_SIGNATURES. Le repli est
        volontairement permissif (n'échoue pas si la signature est inconnue)
        pour éviter de bloquer des formats valides non répertoriés — mais
        rejette explicitement les cas d'incohérence flagrante (ex. un
        exécutable Windows renommé en .mp4).
        """
        try:
            import magic  # python-magic

            detected_mime = magic.from_file(str(file_path), mime=True)
        except ImportError:
            detected_mime = FileValidator._guess_mime_from_header(file_path)
        except Exception as exc:
            logger.warning("Détection de signature impossible pour {}: {}", file_path, exc)
            return  # ne bloque pas l'import sur un échec de la détection elle-même

        if detected_mime is None:
            return

        # Rejet explicite des signatures clairement exécutables/dangereuses,
        # quelle que soit l'extension déclarée.
        dangerous_prefixes = ("application/x-msdownload", "application/x-executable", "application/x-sh")
        if detected_mime.startswith(dangerous_prefixes):
            logger.warning(
                "Signature dangereuse détectée pour {} ({}), extension déclarée: {}",
                file_path, detected_mime, file_path.suffix,
            )
            raise SecurityError(
                "Le contenu réel du fichier ne correspond pas à un média "
                "attendu et a été rejeté pour des raisons de sécurité."
            )

    @staticmethod
    def _guess_mime_from_header(file_path: Path) -> Optional[str]:
        """Repli minimal sans python-magic : lecture des premiers octets."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(16)
        except OSError:
            return None

        for signature, mime in _KNOWN_SIGNATURES.items():
            if header.startswith(signature):
                return mime

        # Repli ultime sur le module mimetypes standard (basé sur l'extension,
        # donc moins fiable, mais mieux que rien si aucune signature ne matche).
        guessed, _ = mimetypes.guess_type(str(file_path))
        return guessed

    # ------------------------------------------------------------------
    # Utilitaires de classification
    # ------------------------------------------------------------------

    @staticmethod
    def get_media_type(file_path: Path) -> Optional[MediaType]:
        """Déduit le MediaType depuis l'extension. Retourne None si
        l'extension n'est pas reconnue (l'appelant doit alors traiter
        cela comme un rejet, `validate()` aura de toute façon déjà levé
        une ValidationError avant qu'on atteigne ce point en pratique).
        """
        ext = file_path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return MediaType.VIDEO
        if ext in IMAGE_EXTENSIONS:
            return MediaType.IMAGE
        if ext in AUDIO_EXTENSIONS:
            return MediaType.AUDIO
        return None
