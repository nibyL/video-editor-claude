# Video Editor

Logiciel desktop de montage vidéo en Python, développé selon une approche
itérative (cf. `docs/` et le cahier des charges CDC-VIDEO-PY-2026-v1.2).

**État actuel : Itération 0 — Socle technique.**

## Installation

```bash
python3 -m venv venv
source venv/bin/activate  # Windows : venv\Scripts\activate
pip install -r requirements.txt
```

Vérifiez que FFmpeg est installé et accessible dans le PATH :

```bash
ffmpeg -version
```

Sinon, renseignez son chemin dans `.env` (copié depuis `.env.example`, développement uniquement)
ou dans les préférences de l'application une fois l'itération 2 (UI) disponible.

## Utilisation — mode console (itération 0-1)

```bash
python3 main.py merge video1.mp4 video2.mp4 -o fusion.mp4
python3 main.py export fusion.mp4 -o export_final.mp4 --resolution 1920x1080 --fps 30
```

## Tests

```bash
python3 -m pytest tests/unit/ -v
```

## Structure du projet

| Dossier | Rôle | Couche |
|---|---|---|
| `core/` | Modèles de domaine, exceptions, logger, settings | Domaine / transversal |
| `application/` | Commandes (Undo/Redo) et cas d'usage | Application |
| `services/` | Logique métier organisée par domaine fonctionnel (video, render, audio...) | Domaine |
| `infrastructure/` | Adaptateurs FFmpeg, MoviePy, validation de fichiers | Infrastructure |
| `storage/` | Persistance des projets (SQLite + JSON) | Infrastructure |
| `ui/` | Interface PySide6 | Présentation |
| `utils/` | Utilitaires transversaux (chemins) | Transversal |
| `tests/` | Tests unitaires et d'intégration | — |

Principe d'architecture : aucune couche ne dépend d'une couche supérieure.
Toute interaction avec FFmpeg ou MoviePy passe exclusivement par
`infrastructure/`, ce qui permet un plan de bascule sans refonte des
couches supérieures si nécessaire (cf. CDC section 3.5).

## Itérations

Voir le cahier des charges pour le détail des 21 itérations (0 à 20).
L'itération 0 livre : structure du projet, adaptateurs FFmpeg/MoviePy
encapsulés, modèles Media et Track, validation de fichiers (sécurité),
service de fusion avec repli automatique, service d'export basique.
