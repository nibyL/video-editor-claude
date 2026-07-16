"""
Tests unitaires de FileValidator.

Premier module testé car il ne dépend ni de FFmpeg ni de MoviePy : il
peut être validé instantanément, sans fixture vidéo réelle. Sert de
convention pour les tests suivants (arrange / act / assert, un scénario
par test, noms explicites).
"""

from __future__ import annotations

import pytest

from core.exceptions import SecurityError, ValidationError
from core.models.media import MediaType
import infrastructure.file_validator as file_validator_module
from infrastructure.file_validator import FileValidator


@pytest.fixture
def working_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    return d


def _make_file(directory, name: str, content: bytes = b"\x00\x00\x00\x18ftypmp42") -> None:
    path = directory / name
    path.write_bytes(content)
    return path


class TestValidateExistence:
    def test_rejects_missing_file(self, working_dir):
        missing = working_dir / "does_not_exist.mp4"

        with pytest.raises(ValidationError, match="introuvable"):
            FileValidator.validate(missing)

    def test_rejects_directory(self, working_dir):
        with pytest.raises(ValidationError, match="pas un fichier"):
            FileValidator.validate(working_dir)


class TestValidateExtension:
    def test_accepts_supported_video_extension(self, working_dir):
        video = _make_file(working_dir, "clip.mp4")

        FileValidator.validate(video)  # ne doit lever aucune exception

    def test_rejects_unsupported_extension(self, working_dir):
        exe = _make_file(working_dir, "script.exe", content=b"MZ\x90\x00")

        with pytest.raises(ValidationError, match="non supporté"):
            FileValidator.validate(exe)


class TestValidateSize:
    def test_rejects_empty_file(self, working_dir):
        empty = _make_file(working_dir, "empty.mp4", content=b"")

        with pytest.raises(ValidationError, match="vide"):
            FileValidator.validate(empty)

    def test_rejects_oversized_file(self, working_dir, monkeypatch):
        # Plutôt que d'écrire un fichier de 2 Go sur disque, on abaisse
        # temporairement le seuil pour le test et on écrit un petit fichier
        # qui le dépasse.
        monkeypatch.setattr(file_validator_module, "MAX_FILE_SIZE_BYTES", 10)
        video = _make_file(working_dir, "huge.mp4", content=b"x" * 20)

        with pytest.raises(ValidationError, match="volumineux"):
            FileValidator.validate(video)


class TestValidateWorkingDirBoundary:
    def test_accepts_file_inside_working_dir(self, working_dir):
        video = _make_file(working_dir, "inside.mp4")

        FileValidator.validate(video, working_dir=working_dir)

    def test_rejects_path_traversal_outside_working_dir(self, working_dir, tmp_path):
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"\x00\x00\x00\x18ftypmp42")

        with pytest.raises(SecurityError):
            FileValidator.validate(outside, working_dir=working_dir)


class TestGetMediaType:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("clip.mp4", MediaType.VIDEO),
            ("clip.MOV", MediaType.VIDEO),
            ("photo.png", MediaType.IMAGE),
            ("track.mp3", MediaType.AUDIO),
        ],
    )
    def test_detects_correct_media_type(self, filename, expected):
        from pathlib import Path

        assert FileValidator.get_media_type(Path(filename)) == expected

    def test_returns_none_for_unknown_extension(self):
        from pathlib import Path

        assert FileValidator.get_media_type(Path("mystery.xyz")) is None
