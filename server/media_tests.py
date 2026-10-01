"""Targeted offline tests. Run python media_tests.py. No models/network/upload.

Synthetic artifacts stay under work/daigui-recorder/media-test-artifacts so tests
never delete user files or write generated content outside the workspace.
"""
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
import uuid
import wave

import media_pipeline as media


class TimelineTests(unittest.TestCase):
    def test_short_pauses_and_complete_syllables_survive(self):
        speech = [(1, 2.3), (2.7, 3.3), (5, 6.5)]
        ranges = media.build_keep_intervals(8, speech)
        self.assertEqual(len(ranges), 2)
        for start, end in speech:
            self.assertTrue(any(a <= start and b >= end for a, b in ranges))
        self.assertTrue(any(a <= 2.3 and b >= 2.7 for a, b in ranges))
        retained_long_pause = ranges[0][1] - 3.3 + 5 - ranges[1][0]
        self.assertGreaterEqual(retained_long_pause, .35 - .000001)
        self.assertLessEqual(retained_long_pause, .35 + 2/30)
        self.assertLess(ranges[0][0], 1)
        self.assertGreater(ranges[-1][1], 6.5)

    def test_quiet_syllable_inside_long_pause_is_preserved(self):
        speech = [(0, 1), (2.7, 2.76), (4.2, 5)]
        ranges = media.build_keep_intervals(5, speech)
        self.assertTrue(any(start <= 2.7 and end >= 2.76 for start, end in ranges))

    def test_no_speech_is_explicit_error(self):
        with self.assertRaises(media.MediaPipelineError) as error:
            media.build_keep_intervals(10, [])
        self.assertEqual(error.exception.code, "no_speech")

    def test_invalid_detector_output_cannot_make_empty_movie(self):
        with self.assertRaises(media.MediaPipelineError):
            media.build_keep_intervals(10, [(float("nan"), 3)])

    def test_path_escape_is_rejected(self):
        with self.assertRaises(media.MediaPipelineError):
            media._inside(Path(__file__).parent, "../../unexpected.txt")

    def test_missing_metadata_does_not_fabricate_title(self):
        with patch.dict("os.environ", {"DAIGUI_TEXT_PROVIDER": "", "METADATA_PROVIDER": ""}):
            with self.assertRaises(media.MediaPipelineError) as error:
                media.generate_metadata("这是一次真实测试。")
            self.assertEqual(error.exception.code, "metadata_config")

    def test_blank_base_url_uses_selected_protocol_and_custom_url_is_used(self):
        value = {'title': 'Sample title', 'description': 'Sample description'}
        import os
        providers = {
            'openai': ('https://api.openai.com/v1/chat/completions', {'choices': [{'message': {'content': json.dumps(value)}}]}),
            'anthropic': ('https://api.anthropic.com/v1/messages', {'content': [{'type': 'text', 'text': json.dumps(value)}]}),
            'gemini': ('https://generativelanguage.googleapis.com/v1beta/models/fixture-model:generateContent',
                       {'candidates': [{'content': {'parts': [{'text': json.dumps(value)}]}}]}),
        }
        for provider, (endpoint, reply) in providers.items():
            with self.subTest(provider=provider), patch.dict(os.environ, {
                'DAIGUI_TEXT_PROVIDER': provider, 'DAIGUI_TEXT_MODEL': 'fixture-model',
                'DAIGUI_TEXT_API_KEY': 'fixture-only', 'DAIGUI_TEXT_BASE_URL': '', 'METADATA_BASE_URL': ''}), \
                    patch.object(media, '_text_api', return_value=reply) as request, \
                    patch.object(media, 'validate_metadata', side_effect=lambda result: result):
                self.assertEqual(media.generate_metadata('Sample transcript.'), value)
                self.assertEqual(request.call_args.args[0], endpoint)
                os.environ['DAIGUI_TEXT_BASE_URL'] = 'https://my-api.example.test/v1'
                media.generate_metadata('Sample transcript.')
                self.assertTrue(request.call_args.args[0].startswith('https://my-api.example.test/v1/'))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent / "media-test-artifacts" / uuid.uuid4().hex[:10]
        cls.root.mkdir(parents=True)
        cls.source = cls.root / "synthetic.webm"
        gate = "between(t,1,2.3)+between(t,2.7,3.3)+between(t,5,6.5)"
        args = media._ffmpeg() + ["-f", "lavfi", "-i",
            f"color=c=black:s=320x180:r=30:d=8,drawbox=color=white:t=fill:enable='{gate}'",
            "-f", "lavfi", "-i", f"aevalsrc='if({gate},0.2*sin(2*PI*440*t),0)':s=48000:d=8",
            "-c:v", "libvpx", "-threads", "1", "-b:v", "150k", "-c:a", "libopus", "-y", str(cls.source)]
        media._run(args, timeout=60)

    def test_real_cut_decodes_and_audio_video_events_remain_aligned(self):
        original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        intervals = media.build_keep_intervals(8, [(1, 2.3), (2.7, 3.3), (5, 6.5)])
        outdir = self.root / "render"
        outdir.mkdir()
        final = media.render_timeline(self.source, outdir, intervals)
        stats = media.verify_output(final, sum(e-s for s, e in intervals))
        self.assertTrue(stats["decoded"])
        self.assertLess(stats["duration"], 5)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)
        gray = subprocess.run(media._ffmpeg() + ["-i", str(final), "-an", "-vf", "scale=1:1:flags=area,format=gray",
              "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
        wav = outdir / "measure.wav"
        media.extract_audio(final, wav)
        with wave.open(str(wav), "rb") as audio_file:
            sample_rate = audio_file.getframerate()
            samples = array("h", audio_file.readframes(audio_file.getnframes()))
        mismatches = []
        for index, brightness in enumerate(gray):
            # Test centres of stable frames, excluding transition-neighbour frames.
            if index < 2 or index + 2 >= len(gray) or any((gray[j] > 120) != (brightness > 120)
                    for j in range(index-2, index+3)):
                continue
            a = int((index + .2) / 30 * sample_rate)
            b = int((index + .8) / 30 * sample_rate)
            window = samples[a:b]
            audible = bool(window) and sum(x*x for x in window) / len(window) > 100000
            if audible != (brightness > 120):
                mismatches.append(index)
        self.assertEqual(mismatches, [], f"Audio/video events differ at frames {mismatches}")
        (self.root / "evidence.json").write_text(json.dumps({"output": str(final), "stats": stats,
            "audio_video_event_mismatches": mismatches, "source_unchanged": True}, indent=2), encoding="utf-8")

    def test_silent_recording_failure_preserves_original(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with patch.object(media, "detect_speech", return_value=[]):
            with self.assertRaises(media.MediaPipelineError) as error:
                media.process_job(self.source, self.root / "no-speech")
        self.assertEqual(error.exception.code, "no_speech")
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertFalse((self.root / "no-speech" / "final.mp4").exists())

    def test_original_cannot_share_generated_output_directory(self):
        with self.assertRaises(media.MediaPipelineError) as error:
            media.process_job(self.source, self.root)
        self.assertEqual(error.exception.code, "source_in_output")

    def test_missing_metadata_returns_reusable_movie_and_transcript(self):
        outdir = self.root / "metadata-missing"
        with patch.object(media, "detect_speech", return_value=[(1, 2.3), (2.7, 3.3), (5, 6.5)]), \
             patch.object(media, "transcribe", return_value={"text": "这是测试口播。", "segments": [], "language": "zh"}), \
             patch.dict("os.environ", {"DAIGUI_TEXT_PROVIDER": "", "METADATA_PROVIDER": ""}):
            result = media.process_job(self.source, outdir)
        self.assertTrue(result["metadata_required"])
        self.assertEqual(result["title"], "")
        self.assertEqual(result["transcript"], "这是测试口播。")
        final = Path(result["final_path"])
        self.assertTrue(final.exists())
        stamp = final.stat().st_mtime_ns
        with patch.object(media, "detect_speech", side_effect=AssertionError("Must reuse verified output")), \
             patch.object(media, "transcribe", side_effect=AssertionError("Must reuse transcript")), \
             patch.object(media, "generate_metadata", return_value={"title": "測試口播", "description": "測試內容的簡介。"}):
            retried = media.process_job(self.source, outdir)
        self.assertFalse(retried["metadata_required"])
        self.assertEqual(final.stat().st_mtime_ns, stamp)
        self.assertEqual(retried["title"], "測試口播")


if __name__ == "__main__":
    unittest.main(verbosity=2)
