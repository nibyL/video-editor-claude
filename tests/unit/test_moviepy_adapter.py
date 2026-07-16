"""
Tests unitaires de MoviePyAdapter.merge_videos — vérifie que le
redimensionnement (coûteux, traitement pixel par pixel) n'est déclenché
que si la résolution du clip diffère réellement de la cible.

MoviePy lui-même est mocké : on ne teste pas le comportement réel de
l'encodage, seulement la logique de décision "faut-il appeler .resized()".
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class _FakeClip:
    """Simule un VideoFileClip MoviePy avec une taille fixe, sans dépendre
    de la bibliothèque réelle ni d'un fichier vidéo.
    """

    def __init__(self, w: int, h: int):
        self.w = w
        self.h = h
        self.resize_called_with = None
        self.closed = False

    def resized(self, target):
        self.resize_called_with = target
        clip = _FakeClip(target[0], target[1])
        clip._is_resized_copy = True
        return clip

    def close(self):
        self.closed = True


@pytest.fixture
def moviepy_module(monkeypatch):
    """Injecte un faux module `moviepy` dans sys.modules pour intercepter
    VideoFileClip et concatenate_videoclips sans dépendre de la vraie lib.
    """
    fake_module = types.ModuleType("moviepy")

    created_clips: list[_FakeClip] = []

    def fake_video_file_clip(path: str):
        # Résolution fixe simulée : toutes les fixtures de ce test sont en 1920x1080
        clip = _FakeClip(1920, 1080)
        created_clips.append(clip)
        return clip

    final_clip = MagicMock()
    final_clip.write_videofile = MagicMock()
    final_clip.close = MagicMock()

    fake_module.VideoFileClip = fake_video_file_clip
    fake_module.concatenate_videoclips = MagicMock(return_value=final_clip)

    monkeypatch.setitem(sys.modules, "moviepy", fake_module)
    return fake_module, created_clips


@pytest.fixture
def working_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    for name in ("clip1.mp4", "clip2.mp4"):
        (d / name).write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return d


def _adapter_without_dependency_check(monkeypatch):
    """Construit un MoviePyAdapter sans déclencher la vérification
    d'installation réelle de MoviePy (déjà mockée via sys.modules).
    """
    from infrastructure.moviepy_adapter import MoviePyAdapter

    return MoviePyAdapter()


class TestResizeSkipping:
    def test_skips_resize_when_resolution_already_matches_target(
        self, moviepy_module, working_dir, monkeypatch
    ):
        _, created_clips = moviepy_module
        adapter = _adapter_without_dependency_check(monkeypatch)

        adapter.merge_videos(
            [working_dir / "clip1.mp4", working_dir / "clip2.mp4"],
            working_dir / "out.mp4",
            target_resolution=(1920, 1080),  # identique à la résolution simulée des clips
        )

        assert all(clip.resize_called_with is None for clip in created_clips), (
            "resized() n'aurait pas dû être appelé : la résolution correspond déjà à la cible"
        )

    def test_calls_resize_when_resolution_differs_from_target(
        self, moviepy_module, working_dir, monkeypatch
    ):
        _, created_clips = moviepy_module
        adapter = _adapter_without_dependency_check(monkeypatch)

        adapter.merge_videos(
            [working_dir / "clip1.mp4", working_dir / "clip2.mp4"],
            working_dir / "out.mp4",
            target_resolution=(1280, 720),  # différent de la résolution simulée (1920x1080)
        )

        assert all(clip.resize_called_with == (1280, 720) for clip in created_clips)

    def test_skips_resize_when_no_target_resolution_given(
        self, moviepy_module, working_dir, monkeypatch
    ):
        _, created_clips = moviepy_module
        adapter = _adapter_without_dependency_check(monkeypatch)

        adapter.merge_videos(
            [working_dir / "clip1.mp4", working_dir / "clip2.mp4"],
            working_dir / "out.mp4",
            target_resolution=None,
        )

        assert all(clip.resize_called_with is None for clip in created_clips)
