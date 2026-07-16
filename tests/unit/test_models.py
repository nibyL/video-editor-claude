"""Tests unitaires des modèles de domaine Media et Track."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.models.media import Media, MediaStatus, MediaType
from core.models.track import DEFAULT_TRACK_LAYOUT, Track, TrackType, create_default_tracks


class TestMedia:
    def test_creates_media_with_defaults(self, tmp_path):
        video_path = tmp_path / "clip.mp4"
        video_path.write_bytes(b"data")

        media = Media(
            project_id="proj-1",
            original_path=video_path,
            type=MediaType.VIDEO,
            file_size_bytes=4,
        )

        assert media.status == MediaStatus.AVAILABLE
        assert media.id  # UUID généré automatiquement
        assert media.is_available() is True

    def test_is_available_false_when_file_missing(self, tmp_path):
        video_path = tmp_path / "missing.mp4"
        media = Media(
            project_id="proj-1",
            original_path=video_path,
            type=MediaType.VIDEO,
            file_size_bytes=0,
        )
        # file_size_bytes=0 est valide au niveau du modèle (validé par
        # FileValidator en amont, pas ici) ; ce test porte sur is_available.
        assert media.is_available() is False

    def test_rejects_negative_file_size(self, tmp_path):
        with pytest.raises(ValueError):
            Media(
                project_id="proj-1",
                original_path=tmp_path / "clip.mp4",
                type=MediaType.VIDEO,
                file_size_bytes=-1,
            )

    def test_resolution_property(self, tmp_path):
        media = Media(
            project_id="proj-1",
            original_path=tmp_path / "clip.mp4",
            type=MediaType.VIDEO,
            file_size_bytes=10,
            resolution_w=1920,
            resolution_h=1080,
        )
        assert media.resolution == "1920x1080"

    def test_resolution_none_when_unknown(self, tmp_path):
        media = Media(
            project_id="proj-1",
            original_path=tmp_path / "clip.mp4",
            type=MediaType.VIDEO,
            file_size_bytes=10,
        )
        assert media.resolution is None


class TestTrack:
    def test_creates_track_with_defaults(self):
        track = Track(project_id="proj-1", type=TrackType.VIDEO_MAIN, name="V1", index=0)

        assert track.locked is False
        assert track.visible is True
        assert track.is_editable is True

    def test_locked_track_is_not_editable(self):
        track = Track(
            project_id="proj-1", type=TrackType.VIDEO_MAIN, name="V1", index=0, locked=True
        )
        assert track.is_editable is False

    def test_rejects_negative_index(self):
        with pytest.raises(ValueError):
            Track(project_id="proj-1", type=TrackType.VIDEO_MAIN, name="V1", index=-1)

    def test_rejects_height_out_of_bounds(self):
        with pytest.raises(ValueError):
            Track(
                project_id="proj-1",
                type=TrackType.VIDEO_MAIN,
                name="V1",
                index=0,
                height_px=200,
            )

    @pytest.mark.parametrize(
        "track_type,expected",
        [
            (TrackType.AUDIO_VIDEO, True),
            (TrackType.AUDIO_MUSIC, True),
            (TrackType.VIDEO_MAIN, False),
            (TrackType.TEXT, False),
        ],
    )
    def test_is_audio(self, track_type, expected):
        track = Track(project_id="proj-1", type=track_type, name="T", index=0)
        assert track.is_audio is expected


class TestCreateDefaultTracks:
    def test_creates_six_tracks_matching_layout(self):
        tracks = create_default_tracks(project_id="proj-1")

        assert len(tracks) == len(DEFAULT_TRACK_LAYOUT) == 6
        assert [t.index for t in tracks] == [0, 1, 2, 3, 4, 5]
        assert tracks[0].type == TrackType.VIDEO_MAIN
        assert tracks[-1].type == TrackType.AUDIO_MUSIC

    def test_all_tracks_belong_to_same_project(self):
        tracks = create_default_tracks(project_id="proj-42")
        assert all(t.project_id == "proj-42" for t in tracks)
