"""
Configuration runtime de l'application — préférences utilisateur.

Décision actée : PAS de .env en production. `.env` est réservé au
développement (voir .env.example). Un logiciel desktop packagé (Nuitka
ou PyInstaller) ne peut pas fiablement lire un .env : le dossier de
l'exécutable est souvent en lecture seule après installation.

Persistance retenue :
- Un fichier JSON simple dans le dossier de configuration standard de l'OS
  (%APPDATA%/VideoEditor sous Windows, ~/.config/VideoEditor sous Linux,
  ~/Library/Application Support/VideoEditor sous macOS).
- Migration prévue vers QSettings natif Qt à partir de l'itération 2
  (quand PySide6 est introduit) — voir TODO ci-dessous. Le format JSON
  est conservé en parallèle pour la portabilité inter-OS des préférences
  qui n'ont pas de sens dans le registre Windows (ex. dernier dossier
  d'export, qui doit rester un chemin explicite).

Ordre de résolution d'une valeur (méthode get) :
1. Valeur déjà chargée en mémoire depuis settings.json
2. Variable d'environnement système (utile en CI/CD, cf. build_nuitka.py)
3. Valeur par défaut fournie par l'appelant
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Optional


class AppSettings:
    """Gestionnaire de configuration unique (singleton).

    Attention en tests unitaires : ce singleton persiste sur disque entre
    les tests. Utiliser `AppSettings.reset_for_testing()` dans les fixtures
    pytest pour éviter les effets de bord entre tests.
    """

    _instance: Optional["AppSettings"] = None

    def __new__(cls) -> "AppSettings":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._config_dir = self._resolve_config_dir()
        self._config_file = self._config_dir / "settings.json"
        self._values: dict[str, Any] = {}
        self._load()
        self._initialized = True

    # ------------------------------------------------------------------
    # Résolution du dossier de configuration selon l'OS
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_config_dir() -> Path:
        """Retourne le dossier de configuration utilisateur standard,
        correct sur Windows, macOS et Linux (contrairement à une version
        naïve qui ne gérerait que APPDATA/HOME).
        """
        system = platform.system()

        if system == "Windows":
            base = os.getenv("APPDATA")
            root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        elif system == "Darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            # Linux et autres Unix : respecte XDG_CONFIG_HOME si défini
            xdg = os.getenv("XDG_CONFIG_HOME")
            root = Path(xdg) if xdg else Path.home() / ".config"

        config_dir = root / "VideoEditor"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    # ------------------------------------------------------------------
    # Chargement / sauvegarde JSON
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._config_file.exists():
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    self._values = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Fichier corrompu ou illisible : on repart d'une config vide
                # plutôt que de faire planter l'application au démarrage.
                self._values = {}
        else:
            self._values = {}

    def _save(self) -> None:
        with open(self._config_file, "w", encoding="utf-8") as f:
            json.dump(self._values, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # API générique
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._values:
            return self._values[key]
        return os.environ.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value
        self._save()

    # ------------------------------------------------------------------
    # Propriétés typées pour les préférences courantes
    # ------------------------------------------------------------------

    @property
    def ffmpeg_path(self) -> Optional[str]:
        """Chemin explicite vers FFmpeg, si défini par l'utilisateur.
        Sinon, FFmpegAdapter cherchera dans le PATH système.
        """
        return self.get("ffmpeg_path") or self.get("FFMPEG_PATH")

    @ffmpeg_path.setter
    def ffmpeg_path(self, path: str) -> None:
        self.set("ffmpeg_path", path)

    @property
    def last_output_folder(self) -> str:
        """Dernier dossier de sortie utilisé à l'export.
        Jamais un dossier imposé — cf. décision actée : l'utilisateur
        choisit toujours via le dialogue d'export (5.5 du CDC).
        """
        return self.get("last_output_folder", str(Path.home() / "Videos"))

    @last_output_folder.setter
    def last_output_folder(self, path: str) -> None:
        self.set("last_output_folder", path)

    @property
    def default_resolution(self) -> str:
        return self.get("default_resolution", "1920x1080")

    @property
    def default_fps(self) -> float:
        return float(self.get("default_fps", 30))

    @property
    def auto_save_interval_seconds(self) -> int:
        """Cf. entité Projet, attribut auto_save_interval (CDC section 6.1)."""
        return int(self.get("auto_save_interval_seconds", 120))

    # ------------------------------------------------------------------
    # Utilitaire de test
    # ------------------------------------------------------------------

    @classmethod
    def reset_for_testing(cls) -> None:
        """Réinitialise le singleton. À appeler dans les fixtures pytest
        pour éviter qu'un test lise/écrive le settings.json d'un autre test.
        """
        cls._instance = None
