"""
Tests unitaires de MergeService.

Les adaptateurs FFmpeg/MoviePy sont mockés : ces tests valident la logique
de décision du service (compatibilité, sondage, stratégie choisie), pas
le comportement réel de FFmpeg — qui relève de tests d'intégration avec
de vraies fixtures vidéo (tests/fixtures/, à ajouter plus tard).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.exceptions import MergeError, ResourceError, ValidationError
from core.models.media import Media, MediaType
from services.video.merge_service import MergeService


def _make_media(directory: Path, name: str, **metadata) -> Media:
    path = directory / name
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return Media(
        project_id="proj-1",
        original_path=path,
        type=MediaType.VIDEO,
        file_size_bytes=path.stat().st_size,
        **metadata,
    )


@pytest.fixture
def working_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def service():
    ffmpeg_mock = MagicMock()
    moviepy_mock = MagicMock()
    return MergeService(ffmpeg_adapter=ffmpeg_mock, moviepy_adapter=moviepy_mock)


# ----------------------------------------------------------------------
# Validation d'entrée (inchangé)
# ----------------------------------------------------------------------

class TestMergeValidation:
    def test_rejects_single_media(self, service, working_dir):
        media = _make_media(working_dir, "clip1.mp4")

        with pytest.raises(ValidationError, match="Au moins deux"):
            service.merge([media], working_dir / "out.mp4")

    def test_rejects_empty_list(self, service, working_dir):
        with pytest.raises(ValidationError, match="Au moins deux"):
            service.merge([], working_dir / "out.mp4")

    def test_propagates_validation_error_for_missing_source(self, service, working_dir):
        media_ok = _make_media(working_dir, "clip1.mp4")
        media_missing = _make_media(working_dir, "clip2.mp4")
        media_missing.original_path.unlink()

        with pytest.raises(ValidationError, match="introuvable"):
            service.merge([media_ok, media_missing], working_dir / "out.mp4")


# ----------------------------------------------------------------------
# Décision automatique : métadonnées déjà connues sur les objets Media
# ----------------------------------------------------------------------

class TestStrategyDecisionWithKnownMetadata:
    """Cas où les Media portent déjà leurs métadonnées (ex. médias déjà
    importés et indexés) : aucun sondage ffprobe ne doit être nécessaire.
    """

    def test_uses_stream_copy_when_media_are_identical(self, service, working_dir):
        common = dict(
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media1 = _make_media(working_dir, "clip1.mp4", **common)
        media2 = _make_media(working_dir, "clip2.mp4", **common)
        output = working_dir / "out.mp4"

        result = service.merge([media1, media2], output, working_dir=working_dir)

        service.ffmpeg.merge_videos.assert_called_once()
        service.moviepy.merge_videos.assert_not_called()
        service.ffmpeg.get_media_metadata.assert_not_called()  # pas de sondage requis
        assert result == output

    def test_uses_reencode_when_resolutions_differ(self, service, working_dir):
        media1 = _make_media(
            working_dir, "clip1.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media2 = _make_media(
            working_dir, "clip2.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1280, resolution_h=720, fps=30.0,
        )
        output = working_dir / "out.mp4"

        result = service.merge([media1, media2], output, working_dir=working_dir)

        service.moviepy.merge_videos.assert_called_once()
        service.ffmpeg.merge_videos.assert_not_called()
        # La résolution la plus grande (1920x1080) doit être choisie comme cible
        _, kwargs = service.moviepy.merge_videos.call_args
        assert kwargs["target_resolution"] == (1920, 1080)
        assert result == output

    def test_uses_reencode_when_codecs_differ(self, service, working_dir):
        media1 = _make_media(
            working_dir, "clip1.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media2 = _make_media(
            working_dir, "clip2.mp4",
            codec_video="hevc", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        service.moviepy.merge_videos.assert_called_once()
        service.ffmpeg.merge_videos.assert_not_called()

    def test_tolerates_negligible_fps_rounding_difference(self, service, working_dir):
        media1 = _make_media(
            working_dir, "clip1.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=29.97,
        )
        media2 = _make_media(
            working_dir, "clip2.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=29.971,
        )

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        # Écart de 0.001 fps, sous la tolérance : copie directe attendue
        service.ffmpeg.merge_videos.assert_called_once()
        service.moviepy.merge_videos.assert_not_called()

    def test_uses_reencode_when_fps_differ_significantly(self, service, working_dir):
        media1 = _make_media(
            working_dir, "clip1.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=24.0,
        )
        media2 = _make_media(
            working_dir, "clip2.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=60.0,
        )

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        service.moviepy.merge_videos.assert_called_once()
        service.ffmpeg.merge_videos.assert_not_called()

    def test_uses_stream_copy_when_both_videos_have_no_audio_track(self, service, working_dir):
        """Régression : deux vidéos sans piste audio (écran de couleur unie,
        capture muette...) ont légitimement codec_audio=None après un
        sondage RÉUSSI. Ce n'est pas une métadonnée inconnue et ne doit
        pas forcer un ré-encodage.
        """
        common = dict(
            codec_video="h264", codec_audio=None,
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media1 = _make_media(working_dir, "clip1.mp4", **common)
        media2 = _make_media(working_dir, "clip2.mp4", **common)

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        service.ffmpeg.merge_videos.assert_called_once()
        service.moviepy.merge_videos.assert_not_called()

    def test_uses_reencode_when_only_one_video_has_audio(self, service, working_dir):
        """Deux vidéos dont une seule a une piste audio ne sont PAS
        compatibles : le nombre de flux diffère réellement.
        """
        media1 = _make_media(
            working_dir, "clip1.mp4",
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media2 = _make_media(
            working_dir, "clip2.mp4",
            codec_video="h264", codec_audio=None,
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        service.moviepy.merge_videos.assert_called_once()
        service.ffmpeg.merge_videos.assert_not_called()


# ----------------------------------------------------------------------
# Sondage automatique : métadonnées inconnues au départ
# ----------------------------------------------------------------------

class TestMetadataProbing:
    def test_probes_missing_metadata_and_chooses_stream_copy(self, service, working_dir):
        media1 = _make_media(working_dir, "clip1.mp4")  # aucune métadonnée connue
        media2 = _make_media(working_dir, "clip2.mp4")

        probed = {
            "codec_video": "h264", "codec_audio": "aac",
            "width": 1920, "height": 1080, "fps": 30.0,
            "duration_ms": 5000, "bitrate_kbps": 4000,
        }
        service.ffmpeg.get_media_metadata.return_value = probed

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        assert service.ffmpeg.get_media_metadata.call_count == 2
        service.ffmpeg.merge_videos.assert_called_once()
        service.moviepy.merge_videos.assert_not_called()
        # Les métadonnées sondées sont bien reportées sur les objets Media
        assert media1.codec_video == "h264"
        assert media1.resolution_w == 1920

    def test_treats_unprobeable_media_as_incompatible(self, service, working_dir):
        media1 = _make_media(working_dir, "clip1.mp4")
        media2 = _make_media(working_dir, "clip2.mp4")

        service.ffmpeg.get_media_metadata.side_effect = ResourceError("ffprobe indisponible")

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        # Par prudence, une métadonnée illisible force le ré-encodage
        service.moviepy.merge_videos.assert_called_once()
        service.ffmpeg.merge_videos.assert_not_called()

    def test_does_not_probe_media_with_already_known_fields(self, service, working_dir):
        known = dict(
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media1 = _make_media(working_dir, "clip1.mp4", **known)
        media2 = _make_media(working_dir, "clip2.mp4")  # celui-ci doit être sondé

        service.ffmpeg.get_media_metadata.return_value = {
            "codec_video": "h264", "codec_audio": "aac",
            "width": 1920, "height": 1080, "fps": 30.0,
            "duration_ms": None, "bitrate_kbps": None,
        }

        service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)

        service.ffmpeg.get_media_metadata.assert_called_once()  # un seul sondage, pas deux


# ----------------------------------------------------------------------
# Repli automatique en cas d'échec malgré une compatibilité détectée
# ----------------------------------------------------------------------

class TestFallbackOnStreamCopyFailure:
    def test_falls_back_to_moviepy_when_stream_copy_fails(self, service, working_dir):
        common = dict(
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media1 = _make_media(working_dir, "clip1.mp4", **common)
        media2 = _make_media(working_dir, "clip2.mp4", **common)

        service.ffmpeg.merge_videos.side_effect = ResourceError("conteneur incompatible")

        result = service.merge(
            [media1, media2], working_dir / "out.mp4", working_dir=working_dir
        )

        service.ffmpeg.merge_videos.assert_called_once()
        service.moviepy.merge_videos.assert_called_once()
        assert result == working_dir / "out.mp4"

    def test_raises_merge_error_when_both_strategies_fail(self, service, working_dir):
        common = dict(
            codec_video="h264", codec_audio="aac",
            resolution_w=1920, resolution_h=1080, fps=30.0,
        )
        media1 = _make_media(working_dir, "clip1.mp4", **common)
        media2 = _make_media(working_dir, "clip2.mp4", **common)

        service.ffmpeg.merge_videos.side_effect = ResourceError("échec ffmpeg")
        service.moviepy.merge_videos.side_effect = RuntimeError("échec moviepy")

        with pytest.raises(MergeError):
            service.merge([media1, media2], working_dir / "out.mp4", working_dir=working_dir)
