"""
Modèle de données pour une piste (Track) de la timeline.

Fondation du domaine, au même titre que Media — aucune dépendance externe.
Correspond à l'entité Piste ajoutée en révision v1.2 du CDC (section 6.1),
qui comblait une omission : Clip référençait déjà track_id sans que Piste
soit définie.

Les 6 pistes du layout fixe (V1, V2, Texte, Sous-titres, A1, A2 — cf. CDC
section 5.4) sont créées automatiquement à la création d'un projet, avec
les valeurs d'index et de type ci-dessous. L'ajout de pistes supplémentaires
est explicitement hors périmètre v1 (prévu "version 2" par le CDC) : ce
module n'a donc pas besoin de gérer un nombre de pistes dynamique pour
l'itération 0.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


class TrackType(str, Enum):
    """Cf. CDC 6.1, entité Piste, attribut `type`."""

    VIDEO_MAIN = "video_main"
    VIDEO_BROLL = "video_broll"
    TEXT = "text"
    SUBTITLE = "subtitle"
    AUDIO_VIDEO = "audio_video"
    AUDIO_MUSIC = "audio_music"


# Layout fixe décrit au CDC section 5.4 : ordre d'affichage de haut en bas.
DEFAULT_TRACK_LAYOUT: tuple[tuple[TrackType, str, int], ...] = (
    (TrackType.VIDEO_MAIN, "Vidéo principale", 0),
    (TrackType.VIDEO_BROLL, "B-roll", 1),
    (TrackType.TEXT, "Textes / titres", 2),
    (TrackType.SUBTITLE, "Sous-titres", 3),
    (TrackType.AUDIO_VIDEO, "Audio original", 4),
    (TrackType.AUDIO_MUSIC, "Musique / voix", 5),
)

MIN_HEIGHT_PX = 32
MAX_HEIGHT_PX = 128
DEFAULT_HEIGHT_PX = 64


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Track:
    """Représente une piste de la timeline (vidéo, audio, texte ou sous-titre).

    `muted` et `solo` n'ont de sens que pour les pistes audio
    (AUDIO_VIDEO, AUDIO_MUSIC) mais restent définis pour tous les types
    par simplicité de schéma, comme au CDC.
    """

    project_id: str
    type: TrackType
    name: str
    index: int

    id: str = field(default_factory=_new_id)
    locked: bool = False
    visible: bool = True
    muted: bool = False
    solo: bool = False
    height_px: int = DEFAULT_HEIGHT_PX

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("index de piste ne peut pas être négatif")
        if not (MIN_HEIGHT_PX <= self.height_px <= MAX_HEIGHT_PX):
            raise ValueError(
                f"height_px doit être compris entre {MIN_HEIGHT_PX} et {MAX_HEIGHT_PX}"
            )

    @property
    def is_audio(self) -> bool:
        return self.type in (TrackType.AUDIO_VIDEO, TrackType.AUDIO_MUSIC)

    @property
    def is_editable(self) -> bool:
        """Une piste verrouillée ne doit accepter aucune modification de clip.
        Centralisé ici pour que tous les services posent la même question.
        """
        return not self.locked


def create_default_tracks(project_id: str) -> list[Track]:
    """Crée les 6 pistes du layout fixe pour un nouveau projet.

    Utilisé par le use case de création de projet (itération 3). Défini
    dès maintenant car il découle directement de DEFAULT_TRACK_LAYOUT
    ci-dessus et n'a pas de raison d'être dupliqué ailleurs plus tard.
    """
    return [
        Track(project_id=project_id, type=track_type, name=name, index=index)
        for track_type, name, index in DEFAULT_TRACK_LAYOUT
    ]
