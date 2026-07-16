#!/usr/bin/env python3
"""
Point d'entrée de Video Editor.

Itération 0-1 : mode console uniquement (MergeService, ExportService).
Itération 2+ : mode graphique PySide6 (ui/), activé via --gui, cf.
architecture couche Application/UI à venir avec le CommandBus.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.logger import configure_logging, get_logger
from core.models.media import Media, MediaType
from core.settings import AppSettings
from infrastructure.file_validator import FileValidator
from services.video.cut_service import CutService
from services.video.export_service import ExportOptions, ExportService
from services.video.merge_service import MergeService
from utils.path_manager import PathManager

logger = get_logger(__name__)


def _media_from_path(path: Path, project_id: str = "console-session") -> Media:
    """Construit un Media minimal à partir d'un chemin, après validation.
    Les métadonnées techniques (durée, résolution...) seront peuplées par
    FFmpegAdapter.get_media_info() à partir de l'itération 1.
    """
    FileValidator.validate(path)
    media_type = FileValidator.get_media_type(path)
    return Media(
        project_id=project_id,
        original_path=path,
        type=media_type or MediaType.VIDEO,
        file_size_bytes=path.stat().st_size,
    )


def run_merge(input_files: list[str], output: Path) -> None:
    media_list = [_media_from_path(Path(f)) for f in input_files]
    service = MergeService()
    result = service.merge(media_list, output)
    print(f"Fusion terminée: {result}")


def run_export(source_file: str, output: Path, resolution: str, fps: float) -> None:
    service = ExportService()
    options = ExportOptions(resolution=resolution, fps=fps)
    result = service.export(Path(source_file), output, options)
    print(f"Export terminé: {result}")


def run_cut(source_file: str, output: Path, start_ms: int, end_ms: int) -> None:
    media = _media_from_path(Path(source_file))
    service = CutService()
    result = service.cut_start_end(media, start_ms=start_ms, end_ms=end_ms, output_path=output)
    print(f"Découpe terminée: {result}")


def run_split(source_file: str, position_ms: int, output_before: Path, output_after: Path) -> None:
    media = _media_from_path(Path(source_file))
    service = CutService()
    before, after = service.split_at(
        media, position_ms=position_ms, output_path_before=output_before, output_path_after=output_after
    )
    print(f"Division terminée: {before} + {after}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Video Editor — mode console")
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser("merge", help="Fusionner plusieurs vidéos")
    merge_parser.add_argument("files", nargs="+", help="Fichiers vidéo à fusionner")
    merge_parser.add_argument("-o", "--output", type=Path, default=Path("merged.mp4"))

    export_parser = subparsers.add_parser("export", help="Exporter une vidéo")
    export_parser.add_argument("file", help="Fichier vidéo source")
    export_parser.add_argument("-o", "--output", type=Path, default=Path("export.mp4"))
    export_parser.add_argument("--resolution", default="1920x1080")
    export_parser.add_argument("--fps", type=float, default=30.0)

    cut_parser = subparsers.add_parser("cut", help="Extraire un segment d'une vidéo")
    cut_parser.add_argument("file", help="Fichier vidéo source")
    cut_parser.add_argument("--start", type=int, required=True, help="Début du segment (ms)")
    cut_parser.add_argument("--end", type=int, required=True, help="Fin du segment (ms)")
    cut_parser.add_argument("-o", "--output", type=Path, default=Path("cut.mp4"))

    split_parser = subparsers.add_parser("split", help="Diviser une vidéo en deux à une position")
    split_parser.add_argument("file", help="Fichier vidéo source")
    split_parser.add_argument("--at", type=int, required=True, dest="position_ms", help="Position de division (ms)")
    split_parser.add_argument("--before", type=Path, default=Path("split_before.mp4"))
    split_parser.add_argument("--after", type=Path, default=Path("split_after.mp4"))

    return parser


def main() -> int:
    configure_logging()
    settings = AppSettings()
    path_manager = PathManager()
    path_manager.clear_temp_dir()

    logger.info("Video Editor démarré (config: {})", settings._config_dir)

    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        if args.command == "merge":
            run_merge(args.files, args.output)
        elif args.command == "export":
            run_export(args.file, args.output, args.resolution, args.fps)
        elif args.command == "cut":
            run_cut(args.file, args.output, args.start, args.end)
        elif args.command == "split":
            run_split(args.file, args.position_ms, args.before, args.after)
    except Exception as exc:
        # Toute exception métier (VideoEditorError et sous-classes) remonte
        # ici proprement affichée à l'utilisateur console, sans traceback brut.
        logger.error("Échec de la commande '{}': {}", args.command, exc)
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
