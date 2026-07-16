"""
Tests unitaires de CutService.

FFmpegAdapter est mocké : ces tests valident la logique de décision
(validation des bornes, seuil de précision, sondage automatique de durée),
pas le comportement réel de FFmpeg. Cf. tests/integration/test_cut_service_integration.py
pour la validation sur de vraies vidéos.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.exceptions import ResourceError, ValidationError
from core.models.media import Media, MediaType
from services.video.cut_service import CutService


def _make_media(directory: Path, name: str = "clip.mp4", **metadata) -> Media:
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
    return CutService(ffmpeg_adapter=MagicMock())


# ----------------------------------------------------------------------
# Validation des bornes
# ----------------------------------------------------------------------

class TestCutStartEndValidation:
    def test_rejects_negative_start(self, service, working_dir):
        media = _make_media(working_dir)

        with pytest.raises(ValidationError, match="négatif"):
            service.cut_start_end(media, start_ms=-100, end_ms=1000, output_path=working_dir / "out.mp4")

    def test_rejects_end_before_start(self, service, working_dir):
        media = _make_media(working_dir)

        with pytest.raises(ValidationError, match="supérieur"):
            service.cut_start_end(media, start_ms=5000, end_ms=1000, output_path=working_dir / "out.mp4")

    def test_rejects_end_beyond_known_duration(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)

        with pytest.raises(ValidationError, match="dépasse la durée"):
            service.cut_start_end(media, start_ms=0, end_ms=15_000, output_path=working_dir / "out.mp4")

    def test_probes_duration_when_unknown_to_validate_bounds(self, service, working_dir):
        media = _make_media(working_dir)  # duration_ms inconnue
        service.ffmpeg.get_media_metadata.return_value = {"duration_ms": 10_000}

        with pytest.raises(ValidationError, match="dépasse la durée"):
            service.cut_start_end(media, start_ms=0, end_ms=20_000, output_path=working_dir / "out.mp4")

        service.ffmpeg.get_media_metadata.assert_called_once()


# ----------------------------------------------------------------------
# Décision copie directe / ré-encodage
# ----------------------------------------------------------------------

class TestCutStrategyDecision:
    def test_keeps_stream_copy_when_precise_enough(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)
        # La copie directe produit un fichier de durée quasi exacte (10 ms d'écart)
        service.ffmpeg.get_media_metadata.return_value = {"duration_ms": 2010}

        service.cut_start_end(media, start_ms=1000, end_ms=3000, output_path=working_dir / "out.mp4")

        assert service.ffmpeg.cut_segment.call_count == 1
        _, kwargs = service.ffmpeg.cut_segment.call_args
        assert kwargs["stream_copy"] is True

    def test_falls_back_to_reencode_when_imprecise(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)
        # La copie directe produit un écart de 500 ms, largement au-dessus de la tolérance
        service.ffmpeg.get_media_metadata.return_value = {"duration_ms": 2500}

        service.cut_start_end(media, start_ms=1000, end_ms=3000, output_path=working_dir / "out.mp4")

        assert service.ffmpeg.cut_segment.call_count == 2
        first_call_kwargs = service.ffmpeg.cut_segment.call_args_list[0].kwargs
        second_call_kwargs = service.ffmpeg.cut_segment.call_args_list[1].kwargs
        assert first_call_kwargs["stream_copy"] is True
        assert second_call_kwargs["stream_copy"] is False

    def test_falls_back_to_reencode_when_verification_fails(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)
        service.ffmpeg.get_media_metadata.side_effect = ResourceError("sondage impossible")

        service.cut_start_end(media, start_ms=1000, end_ms=3000, output_path=working_dir / "out.mp4")

        assert service.ffmpeg.cut_segment.call_count == 2
        second_call_kwargs = service.ffmpeg.cut_segment.call_args_list[1].kwargs
        assert second_call_kwargs["stream_copy"] is False


# ----------------------------------------------------------------------
# split_at
# ----------------------------------------------------------------------

class TestSplitAt:
    def test_splits_into_two_segments_with_known_duration(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)
        # Chaque segment est vérifié séparément : le premier dure 4000 ms
        # (0 -> 4000), le second 6000 ms (4000 -> 10000). Le mock doit
        # renvoyer une durée cohérente avec CHAQUE appel pour rester précis.
        service.ffmpeg.get_media_metadata.side_effect = [
            {"duration_ms": 4000},
            {"duration_ms": 6000},
        ]

        before, after = service.split_at(
            media,
            position_ms=4000,
            output_path_before=working_dir / "before.mp4",
            output_path_after=working_dir / "after.mp4",
        )

        assert before == working_dir / "before.mp4"
        assert after == working_dir / "after.mp4"
        assert service.ffmpeg.cut_segment.call_count == 2

    def test_probes_duration_when_unknown(self, service, working_dir):
        media = _make_media(working_dir)  # duration_ms inconnue au départ
        service.ffmpeg.get_media_metadata.return_value = {"duration_ms": 10_000}

        service.split_at(
            media,
            position_ms=4000,
            output_path_before=working_dir / "before.mp4",
            output_path_after=working_dir / "after.mp4",
        )

        assert media.duration_ms == 10_000

    def test_rejects_position_at_or_beyond_duration(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)

        with pytest.raises(ValidationError, match="comprise entre"):
            service.split_at(
                media,
                position_ms=10_000,
                output_path_before=working_dir / "before.mp4",
                output_path_after=working_dir / "after.mp4",
            )

    def test_rejects_position_zero(self, service, working_dir):
        media = _make_media(working_dir, duration_ms=10_000)

        with pytest.raises(ValidationError, match="comprise entre"):
            service.split_at(
                media,
                position_ms=0,
                output_path_before=working_dir / "before.mp4",
                output_path_after=working_dir / "after.mp4",
            )

    def test_raises_when_duration_cannot_be_determined(self, service, working_dir):
        media = _make_media(working_dir)
        service.ffmpeg.get_media_metadata.side_effect = ResourceError("ffprobe indisponible")

        with pytest.raises(ValidationError, match="Impossible de déterminer la durée"):
            service.split_at(
                media,
                position_ms=4000,
                output_path_before=working_dir / "before.mp4",
                output_path_after=working_dir / "after.mp4",
            )
