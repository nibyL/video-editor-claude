"""
Tests unitaires du parsing ffprobe dans FFmpegAdapter.

Ne nécessitent ni FFmpeg installé ni fichier vidéo réel : on teste
uniquement la fonction pure de parsing (_parse_probe_output,
_parse_frame_rate) à partir de JSON simulé, représentatif de la
sortie réelle de `ffprobe -show_entries ... -of json`.
"""

from __future__ import annotations

from infrastructure.ffmpeg_adapter import FFmpegAdapter


class TestParseFrameRate:
    def test_parses_integer_ratio(self):
        assert FFmpegAdapter._parse_frame_rate("30/1") == 30.0

    def test_parses_ntsc_ratio(self):
        assert FFmpegAdapter._parse_frame_rate("30000/1001") == 29.97

    def test_returns_none_for_missing_value(self):
        assert FFmpegAdapter._parse_frame_rate(None) is None

    def test_returns_none_for_malformed_value(self):
        assert FFmpegAdapter._parse_frame_rate("not-a-fraction") is None

    def test_returns_none_for_zero_denominator(self):
        assert FFmpegAdapter._parse_frame_rate("30/0") is None


class TestParseProbeOutput:
    def test_extracts_video_and_audio_streams(self):
        raw = {
            "format": {"duration": "12.5", "bit_rate": "4000000"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }

        metadata = FFmpegAdapter._parse_probe_output(raw)

        assert metadata["codec_video"] == "h264"
        assert metadata["codec_audio"] == "aac"
        assert metadata["width"] == 1920
        assert metadata["height"] == 1080
        assert metadata["fps"] == 30.0
        assert metadata["duration_ms"] == 12500
        assert metadata["bitrate_kbps"] == 4000

    def test_handles_missing_fields_gracefully(self):
        metadata = FFmpegAdapter._parse_probe_output({"format": {}, "streams": []})

        assert metadata["codec_video"] is None
        assert metadata["width"] is None
        assert metadata["fps"] is None
        assert metadata["duration_ms"] is None

    def test_ignores_extra_video_streams_and_keeps_first(self):
        raw = {
            "format": {},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160},
            ],
        }

        metadata = FFmpegAdapter._parse_probe_output(raw)

        assert metadata["codec_video"] == "h264"
        assert metadata["width"] == 1920
