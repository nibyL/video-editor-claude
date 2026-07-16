"""
Test d'intégration de CutService avec un vrai FFmpeg et de vraies vidéos.

Contrairement aux tests unitaires (FFmpegAdapter mocké), ce test exécute
réellement FFmpeg sur des fichiers générés à la volée (mires de couleur
via `-f lavfi color=...`). Il valide le comportement réel du mécanisme
"copie directe -> vérification -> repli ré-encodage", pas seulement la
logique d'orchestration.

Ignoré automatiquement si FFmpeg n'est pas installé sur la machine
(cf. skip conditionnel ci-dessous), pour ne pas casser la CI sur un poste
qui n'aurait pas encore FFmpeg configuré.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from core.models.media import Media, MediaType
from services.video.cut_service import CutService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/ffprobe non installés sur cette machine",
)

# GOP de 2 secondes (keyframe toutes les 60 frames à 30 fps) : assez espacé
# pour garantir qu'une coupe en plein milieu d'un intervalle produise un
# écart significatif en mode copie directe, et déclenche le repli attendu.
_GOP_SIZE = 60
_FPS = 30
_DURATION_S = 12


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """Génère une vidéo de test de 12 secondes avec un GOP de 2s."""
    directory = tmp_path_factory.mktemp("fixtures")
    video_path = directory / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={_DURATION_S}:r={_FPS}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(_GOP_SIZE),
            str(video_path),
        ],
        check=True,
        timeout=30,
    )
    return video_path


@pytest.fixture
def media(sample_video):
    return Media(
        project_id="integration-test",
        original_path=sample_video,
        type=MediaType.VIDEO,
        file_size_bytes=sample_video.stat().st_size,
    )


def _probe_duration_ms(video_path) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return int(float(result.stdout.strip()) * 1000)


class TestCutServiceIntegration:
    def test_cut_aligned_with_keyframe_stays_fast_and_accurate(self, media, tmp_path):
        """Une coupe qui tombe pile sur une frontière de keyframe (ici 4000ms,
        multiple du GOP de 2000ms) doit rester précise en copie directe.
        """
        service = CutService()
        output = tmp_path / "aligned.mp4"

        service.cut_start_end(media, start_ms=4000, end_ms=8000, output_path=output)

        actual_duration = _probe_duration_ms(output)
        assert abs(actual_duration - 4000) <= 150

    def test_cut_mid_gop_falls_back_to_precise_reencode(self, media, tmp_path):
        """Une coupe démarrant en plein milieu d'un GOP doit automatiquement
        basculer en ré-encodage pour produire une durée exacte.
        """
        service = CutService()
        output = tmp_path / "mid_gop.mp4"

        # Keyframes à 0, 2000, 4000... ; 1000ms est à mi-chemin, décalage maximal.
        service.cut_start_end(media, start_ms=1000, end_ms=6000, output_path=output)

        actual_duration = _probe_duration_ms(output)
        # Le ré-encodage doit produire une durée quasi exacte (tolérance large
        # pour absorber l'arrondi des frames à 30 fps, soit ~33ms par image).
        assert abs(actual_duration - 5000) <= 100

    def test_split_at_produces_two_segments_covering_the_source(self, media, tmp_path):
        service = CutService()
        before = tmp_path / "before.mp4"
        after = tmp_path / "after.mp4"

        service.split_at(media, position_ms=5000, output_path_before=before, output_path_after=after)

        duration_before = _probe_duration_ms(before)
        duration_after = _probe_duration_ms(after)

        assert abs(duration_before - 5000) <= 150
        assert abs(duration_after - (_DURATION_S * 1000 - 5000)) <= 150

    def test_probed_duration_is_reported_on_media_object(self, media, tmp_path):
        assert media.duration_ms is None  # pas encore sondé

        service = CutService()
        service.split_at(
            media, position_ms=3000,
            output_path_before=tmp_path / "b.mp4", output_path_after=tmp_path / "a.mp4",
        )

        assert media.duration_ms is not None
        assert abs(media.duration_ms - _DURATION_S * 1000) <= 150
