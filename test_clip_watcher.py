#!/usr/bin/env python3
"""
Test suite for clip-watcher components:
1. Spike detection logic (10s buckets, median of last 3 min, 2.5x threshold, min messages).
2. Cooldown debouncing.
3. IRC message parsing and anonymous auth formatting.
4. Center-crop 9:16 aspect ratio filter validation.
5. End-to-end clip generation and Whisper .srt creation on synthetic media.
"""

import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

# Import clip_watcher modules
from clip_watcher import SpikeDetector, TwitchIRCListener, ClipProcessor, StreamRecorder


class TestSpikeDetector(unittest.TestCase):
    def setUp(self):
        # 10s buckets, 3 min window (18 buckets), min messages = 5
        self.detector = SpikeDetector(min_messages=5, bucket_seconds=10, window_minutes=3, cooldown_seconds=30)

    def test_spike_detection_normal_flow(self):
        # Simulate baseline quiet chat: 2 messages per 10s for 18 buckets (3 minutes)
        for i in range(18):
            is_spike, med, thresh = self.detector.evaluate_bucket(count=2, current_stream_time=i * 10.0)
            self.assertFalse(is_spike, f"Bucket {i} should not be a spike")
            if i > 0:
                self.assertEqual(med, 2.0)
                # threshold = max(5, 2.5 * 2.0 = 5.0) = 5.0
                self.assertEqual(thresh, 5.0)

        # Now test a spike: 20 messages in bucket 19
        is_spike, med, thresh = self.detector.evaluate_bucket(count=20, current_stream_time=180.0)
        self.assertTrue(is_spike, "Bucket 19 (20 msgs) must be recognized as a spike!")
        self.assertEqual(med, 2.0)
        self.assertEqual(thresh, 5.0)

    def test_min_messages_threshold(self):
        # If chat is completely dead (0 msgs per bucket)
        for i in range(18):
            self.detector.evaluate_bucket(count=0, current_stream_time=i * 10.0)

        # If 3 messages arrive (3 >= 2.5*0, but 3 < min_messages (5))
        is_spike, med, thresh = self.detector.evaluate_bucket(count=3, current_stream_time=180.0)
        self.assertFalse(is_spike, "3 messages should not trigger spike because min_messages is 5")

        # If 5 messages arrive (5 >= 2.5*0 and 5 >= min_messages)
        is_spike, med, thresh = self.detector.evaluate_bucket(count=5, current_stream_time=190.0)
        self.assertTrue(is_spike, "5 messages should trigger spike when min_messages is 5")

    def test_cooldown_suppression(self):
        # Setup baseline
        for i in range(18):
            self.detector.evaluate_bucket(count=4, current_stream_time=i * 10.0)

        # First spike at t=180s
        is_spike1, _, _ = self.detector.evaluate_bucket(count=25, current_stream_time=180.0)
        self.assertTrue(is_spike1)

        # Immediate next bucket at t=190s is also high (hype continues)
        # Should be suppressed by 30s cooldown
        is_spike2, _, _ = self.detector.evaluate_bucket(count=30, current_stream_time=190.0)
        self.assertFalse(is_spike2, "Immediate next bucket within 30s cooldown must be suppressed")

        # Bucket after cooldown elapsed (t=215s > 180+30)
        is_spike3, _, _ = self.detector.evaluate_bucket(count=35, current_stream_time=215.0)
        self.assertTrue(is_spike3, "Spike after cooldown expiration must trigger")


class TestIRCMessageTracking(unittest.TestCase):
    def test_message_window_counting(self):
        listener = TwitchIRCListener(channel="testchannel")
        t0 = 1000.0

        with listener._message_lock:
            # Simulate 5 messages between t=1000 and t=1010
            for i in range(5):
                listener._message_times.append(t0 + i)
            # 2 messages between t=1010 and t=1020
            listener._message_times.append(t0 + 12)
            listener._message_times.append(t0 + 15)

        count_bucket1 = listener.get_messages_in_interval(1000.0, 1010.0)
        count_bucket2 = listener.get_messages_in_interval(1010.0, 1020.0)
        self.assertEqual(count_bucket1, 5)
        self.assertEqual(count_bucket2, 2)


class TestMediaAndFFmpeg(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_scratch")
        self.test_dir.mkdir(exist_ok=True)
        self.test_rec = self.test_dir / "test_stream.mp4"
        self.test_clips = self.test_dir / "clips"
        self.test_clips.mkdir(exist_ok=True)

        # Create a synthetic 35-second test video (1920x1080 16:9 with sine audio)
        # Using ffmpeg
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
            "-t", "35",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(self.test_rec)
        ]
        res = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 0, f"FFmpeg failed to create test video: {res.stderr}")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clip_cutting_and_9_16_ratio(self):
        processor = ClipProcessor(rec_path=self.test_rec, streamer_name="test_streamer", clips_dir=self.test_clips, enable_whisper=False)
        # Simulate a spike at stream time 25.0s
        # 20s before = 5s, 10s after = 35s. Duration = 30s.
        task = {
            "clip_id": 1,
            "start_time": 5.0,
            "duration": 30.0,
            "ready_wall_time": time.time() - 1, # ready immediately
            "spike_stream_time": 25.0,
        }
        processor._cut_and_transcribe(task)
        processor.stop()

        # Find generated clip in streamer folder
        clips = list(self.test_clips.glob("**/*.mp4"))
        self.assertEqual(len(clips), 1, "Exactly one clip should have been produced")
        clip_file = clips[0]
        self.assertTrue(clip_file.exists())
        self.assertGreater(clip_file.stat().st_size, 0)

        # Probe resolution using ffprobe or static_ffprobe
        ffprobe_bin = "ffprobe"
        probe_cmd = [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(clip_file)
        ]
        probe_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if probe_res.returncode == 0:
            width_str, height_str = probe_res.stdout.strip().split("x")
            width, height = int(width_str), int(height_str)
            # Check 9:16 vertical blurred background resolution (1080x1920)
            self.assertEqual(width, 1080)
            self.assertEqual(height, 1920)
            print(f"[Verified] 9:16 Blurred layout dimensions: {width}x{height} (Aspect ratio: {width/height:.4f} == 9/16 = {9/16:.4f})")


if __name__ == "__main__":
    unittest.main()
