"""
Exceptions métier de l'application Video Editor.

Toute exception technique externe (FFmpeg, MoviePy, subprocess, I/O disque)
doit être interceptée et convertie en exception métier avant de remonter
aux couches supérieures (services, application, UI).

Aucune couche autre qu'infrastructure/ ne doit jamais laisser fuiter une
exception native de bibliothèque tierce (subprocess.CalledProcessError,
moviepy.editor.*, etc.).
"""

from __future__ import annotations


class VideoEditorError(Exception):
    """Exception de base pour toute l'application.

    Toutes les exceptions métier héritent de celle-ci, ce qui permet
    à l'UI et aux use cases de faire un `except VideoEditorError` générique
    pour tout ce qui est prévu, et de laisser remonter le reste (bug réel).
    """


class ValidationError(VideoEditorError):
    """Fichier invalide, codec non supporté, chemin inaccessible,
    ou tout rejet du FileValidator (cf. CDC section 8.4).
    """


class MergeError(VideoEditorError):
    """Erreur lors de la fusion de plusieurs vidéos."""


class ExportError(VideoEditorError):
    """Erreur lors de l'export du projet vers un fichier final."""


class ResourceError(VideoEditorError):
    """Ressource système indisponible : FFmpeg introuvable, mémoire
    insuffisante, disque plein, dépendance manquante.
    """


class ProjectError(VideoEditorError):
    """Erreur de gestion de projet : sauvegarde, ouverture, corruption
    du fichier .vproj. Réservée pour l'itération 3 (storage/), déclarée
    ici dès maintenant car elle appartient au socle d'exceptions commun.
    """


class SecurityError(VideoEditorError):
    """Violation détectée par les contrôles de sécurité applicative
    (cf. CDC section 8.4 — grille STRIDE) : traversée de répertoire,
    signature de fichier usurpée, commande shell non sécurisée tentée.

    Cette exception est volontairement distincte de ValidationError :
    une ValidationError est un rejet "normal" (mauvais format), une
    SecurityError signale une tentative suspecte à journaliser en
    priorité.
    """
