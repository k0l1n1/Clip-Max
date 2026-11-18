#!/usr/bin/env python3
"""
clip-watcher: Autonomous Twitch & YouTube stream recorder, chat spike detector,
vertical 9:16 clip cutter, and Whisper subtitle generator.

Usage:
    ./watch.sh <twitch_or_youtube_target> [options]
    python clip_watcher.py <twitch_or_youtube_target> [options]

Workflow:
    1) streamlink records the live broadcast (Twitch or YouTube) to rec/.
    2) Reads live chat in real time:
       - Twitch: Anonymous IRC (irc.chat.twitch.tv:6667, NICK justinfan12345)
       - YouTube: Live Chat stream via pytchat
    3) Counts messages in 10 s buckets; a spike = 1.82x the median of the last 3 min and >= --min messages.
    4) For each spike, ffmpeg cuts 20 s before + 10 s after into clips/, center-cropped to 9:16.
    5) Whisper writes an .srt for each clip.
"""

import argparse
import collections
import datetime
import json
import os
import queue
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

# Force unbuffered stdout for immediate live logging
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)


def extract_youtube_video_id(url_or_id: str) -> str | None:
    """Extract 11-char YouTube video ID from various URL formats."""
    text = url_or_id.strip()
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", text):
        return text

    m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", text)
    if m:
        return m.group(1)

    m = re.search(r"(?:youtu\.be/|/live/|/embed/|/v/|/shorts/)([a-zA-Z0-9_-]{11})", text)
    if m:
        return m.group(1)

    return None


def resolve_stream_target(target: str) -> dict:
    """
    Detects whether target is Twitch, YouTube, or Kick, resolving URLs and identifiers.
    """
    t = target.strip()
    is_yt = ("youtube.com" in t) or ("youtu.be" in t) or (extract_youtube_video_id(t) is not None and not t.isalnum())
    is_kick = ("kick.com" in t)

    if is_yt:
        vid_id = extract_youtube_video_id(t)
        if vid_id:
            stream_url = f"https://www.youtube.com/watch?v={vid_id}"
            display_name = f"youtube_{vid_id}"
        else:
            # Channel URL like @Channel/live
            stream_url = t if t.startswith("http") else f"https://{t}"
            display_name = re.sub(r"[^a-zA-Z0-9_]", "_", t.split("/")[-1])
            vid_id = None

        return {
            "platform": "youtube",
            "stream_url": stream_url,
            "display_name": display_name,
            "youtube_video_id": vid_id,
        }
    elif is_kick:
        clean = re.sub(r"^https?://(?:www\.)?kick\.com/", "", t, flags=re.IGNORECASE)
        clean = clean.split("/")[0].lstrip("@").lstrip("#")
        return {
            "platform": "kick",
            "stream_url": f"https://kick.com/{clean}",
            "display_name": clean,
            "channel": clean,
        }
    else:
        # Twitch channel
        clean = re.sub(r"^https?://(?:www\.)?twitch\.tv/", "", t, flags=re.IGNORECASE)
        clean = clean.split("/")[0].lstrip("@").lstrip("#").lower()
        return {
            "platform": "twitch",
            "stream_url": f"https://twitch.tv/{clean}",
            "display_name": clean,
            "channel": clean,
        }


class TwitchIRCListener(threading.Thread):
    """
    Connects anonymously to Twitch IRC over port 6667 as 'justinfan12345'
    without token, joins the specified channel, and tracks incoming PRIVMSG counts.
    """

    IRC_SERVER = "irc.chat.twitch.tv"
    IRC_PORT = 6667

    def __init__(self, channel: str, nick: str = "justinfan12345"):
        super().__init__(daemon=True, name="TwitchIRCListener")
        self.channel = channel.lower().lstrip("#")
        self.nick = nick
        self.running = False
        self.sock = None
        self._message_lock = threading.Lock()
        self._message_times = collections.deque()
        self.connected_event = threading.Event()
        self.reconnect_delay = 5

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except Exception:
                pass

    def run(self):
        self.running = True
        while self.running:
            try:
                print(f"[Twitch IRC] Connecting anonymously to {self.IRC_SERVER}:{self.IRC_PORT} as {self.nick}...")
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(15)
                self.sock.connect((self.IRC_SERVER, self.IRC_PORT))

                self.sock.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n".encode("utf-8"))
                self.sock.sendall(f"PASS oauth:none\r\n".encode("utf-8"))
                self.sock.sendall(f"NICK {self.nick}\r\n".encode("utf-8"))
                self.sock.sendall(f"USER {self.nick} 8 * :{self.nick}\r\n".encode("utf-8"))
                self.sock.sendall(f"JOIN #{self.channel}\r\n".encode("utf-8"))

                print(f"[Twitch IRC] Joined #{self.channel}. Listening to chat...")
                self.connected_event.set()

                buffer = ""
                self.sock.settimeout(60)
                while self.running:
                    try:
                        data = self.sock.recv(4096).decode("utf-8", errors="ignore")
                    except socket.timeout:
                        self.sock.sendall(f"PING :tmi.twitch.tv\r\n".encode("utf-8"))
                        continue
                    if not data:
                        print("[Twitch IRC] Connection closed by server.")
                        break

                    buffer += data
                    lines = buffer.split("\r\n")
                    buffer = lines.pop()

                    for line in lines:
                        if not line:
                            continue
                        if line.startswith("PING"):
                            pong = line.replace("PING", "PONG", 1)
                            self.sock.sendall(f"{pong}\r\n".encode("utf-8"))
                            continue

                        if f"PRIVMSG #{self.channel}" in line:
                            now = time.time()
                            with self._message_lock:
                                self._message_times.append(now)

            except Exception as e:
                if self.running:
                    print(f"[Twitch IRC] Warning: Connection error ({e}). Reconnecting in {self.reconnect_delay}s...")
                    time.sleep(self.reconnect_delay)
            finally:
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                self.connected_event.clear()

    def get_messages_in_interval(self, start_time: float, end_time: float) -> int:
        with self._message_lock:
            cutoff = start_time - 600
            while self._message_times and self._message_times[0] < cutoff:
                self._message_times.popleft()
            return sum(1 for t in self._message_times if start_time <= t < end_time)


class YouTubeChatListener(threading.Thread):
    """
    Connects to YouTube Live Chat using pytchat,
    streaming messages and tracking timestamps for bucket evaluation.
    """

    def __init__(self, video_id: str):
        super().__init__(daemon=True, name="YouTubeChatListener")
        self.video_id = video_id
        self.running = False
        self._message_lock = threading.Lock()
        self._message_times = collections.deque()
        self.chat = None
        self.connected_event = threading.Event()

    def stop(self):
        self.running = False
        if self.chat:
            try:
                self.chat.terminate()
            except Exception:
                pass

    def run(self):
        self.running = True
        try:
            import pytchat

            print(f"[YouTube Chat] Connecting to live chat for video: {self.video_id}...")
            self.chat = pytchat.create(video_id=self.video_id)
            print(f"[YouTube Chat] Connected! Listening to chat...")
            self.connected_event.set()

            while self.running and self.chat.is_alive():
                data = self.chat.get()
                now = time.time()
                if data and data.items:
                    with self._message_lock:
                        for _ in data.items:
                            self._message_times.append(now)
                time.sleep(1.0)

        except Exception as e:
            if self.running:
                print(f"[YouTube Chat] Live chat error ({e})")
        finally:
            self.connected_event.clear()

    def get_messages_in_interval(self, start_time: float, end_time: float) -> int:
        with self._message_lock:
            cutoff = start_time - 600
            while self._message_times and self._message_times[0] < cutoff:
                self._message_times.popleft()
            return sum(1 for t in self._message_times if start_time <= t < end_time)


class StreamRecorder:
    """
    Spawns streamlink to record the live stream (Twitch or YouTube) to rec/.
    Tracks stream start time and process health.
    """

    def __init__(self, target_info: dict, quality: str = "best", rec_dir: Path = Path("rec")):
        self.target_info = target_info
        self.stream_url = target_info["stream_url"]
        self.display_name = target_info["display_name"]
        self.quality = quality
        self.rec_dir = rec_dir
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.rec_path = self.rec_dir / f"{self.display_name}_{timestamp}.mp4"
        self.process = None
        self.start_time = None

    def _get_streamlink_cmd(self) -> list[str]:
        base_cmd = []
        if shutil.which("streamlink"):
            base_cmd = ["streamlink"]
        else:
            scripts_streamlink = Path(sys.executable).parent / "Scripts" / "streamlink.exe"
            if scripts_streamlink.exists():
                base_cmd = [str(scripts_streamlink)]
            else:
                base_cmd = [sys.executable, "-m", "streamlink"]

        cmd = base_cmd + [
            self.stream_url,
            self.quality,
            "-o",
            str(self.rec_path),
            "--force",
        ]

        if self.target_info["platform"] == "twitch":
            cmd += ["--twitch-disable-ads", "--twitch-low-latency"]

        return cmd

    def start(self) -> bool:
        cmd = self._get_streamlink_cmd()
        print(f"[Streamlink] Recording: {self.stream_url} ({self.quality}) -> {self.rec_path}")
        self.start_time = time.time()
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
        except Exception as e:
            print(f"[Error] Failed to spawn streamlink ({e})")
            return False

        def _read_output():
            for line in self.process.stdout:
                line_str = line.strip()
                if line_str:
                    if "error:" in line_str.lower() or "warning" in line_str.lower() or "available streams" in line_str.lower():
                        print(f"[Streamlink] {line_str}")

        threading.Thread(target=_read_output, daemon=True, name="StreamlinkLogger").start()
        return True

    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def get_elapsed_stream_time(self) -> float:
        if not self.start_time:
            return 0.0
        return max(0.0, time.time() - self.start_time)

    def stop(self):
        if self.process and self.is_alive():
            print("[Streamlink] Stopping recording...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class SpikeDetector:
    """
    Counts chat messages in 10s buckets.
    Maintains a 3-minute sliding window (18 buckets).
    Detects spike when bucket count >= 1.82 * median(last 3 min) and count >= min_messages.
    """

    def __init__(self, min_messages: int = 5, bucket_seconds: int = 10, window_minutes: int = 3, cooldown_seconds: int = 30, multiplier: float = 1.82):
        self.min_messages = min_messages
        self.bucket_seconds = bucket_seconds
        self.history_size = int((window_minutes * 60) / bucket_seconds)
        self.bucket_history = collections.deque(maxlen=self.history_size)
        self.cooldown_seconds = cooldown_seconds
        self.multiplier = multiplier
        self.last_spike_time = -999.0

    def evaluate_bucket(self, count: int, current_stream_time: float) -> tuple[bool, float, float]:
        if len(self.bucket_history) > 0:
            med = statistics.median(self.bucket_history)
        else:
            med = 0.0

        threshold = max(float(self.min_messages), self.multiplier * med)
        is_spike = False

        if count >= (self.multiplier * med) and count >= self.min_messages:
            if current_stream_time - self.last_spike_time >= self.cooldown_seconds:
                is_spike = True
                self.last_spike_time = current_stream_time
            else:
                print(f"[Spike Detector] Cooldown active ({current_stream_time - self.last_spike_time:.1f}s < {self.cooldown_seconds}s)")

        self.bucket_history.append(count)
        return is_spike, med, threshold


_WHISPER_LOCK = threading.Lock()


class ClipProcessor:
    """
    Cuts clips with FFmpeg (20s before + 10s after spike), center-crops to 9:16 vertical,
    and runs Whisper to generate .srt subtitles.
    Organizes clips into per-streamer directories under clips/<streamer>/.
    """

    def __init__(
        self,
        rec_path: Path,
        streamer_name: str,
        clips_dir: Path = Path("clips"),
        whisper_model: str = "base",
        enable_whisper: bool = True,
        on_log=None,
    ):
        self.rec_path = rec_path
        self.streamer_name = streamer_name
        self.clips_dir = clips_dir / self.streamer_name
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.whisper_model_name = whisper_model
        self.enable_whisper = enable_whisper
        self.on_log = on_log
        self.work_queue = queue.Queue()
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True, name="ClipProcessorWorker")
        self.worker_thread.start()
        self._whisper_model = None
        self.clip_counter = 0

    def log(self, text: str):
        print(text)
        if self.on_log:
            try:
                self.on_log(text)
            except Exception:
                pass

    def schedule_clip(self, spike_stream_time: float, trigger_wall_time: float):
        start_time = max(0.0, spike_stream_time - 20.0)
        end_time = spike_stream_time + 10.0
        duration = end_time - start_time
        ready_wall_time = trigger_wall_time + 12.0
        self.clip_counter += 1
        clip_id = self.clip_counter

        self.log(f"[Queue] Scheduled Clip #{clip_id} from {start_time:.1f}s to {end_time:.1f}s (duration: {duration:.1f}s)")
        self.work_queue.put({
            "clip_id": clip_id,
            "start_time": start_time,
            "duration": duration,
            "ready_wall_time": ready_wall_time,
            "spike_stream_time": spike_stream_time,
        })

    def _worker(self):
        while self.running:
            try:
                task = self.work_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            now = time.time()
            wait_needed = task["ready_wall_time"] - now
            if wait_needed > 0:
                print(f"[Clip #{task['clip_id']}] Waiting {wait_needed:.1f}s for recording buffer...")
                time.sleep(wait_needed)

            try:
                self._cut_and_transcribe(task)
            except Exception as e:
                print(f"[Clip #{task['clip_id']}] Error: {e}")
            finally:
                self.work_queue.task_done()

    def _cut_and_transcribe(self, task: dict):
        clip_id = task["clip_id"]
        start_time = task["start_time"]
        duration = task["duration"]
        stem = f"{self.rec_path.stem}_clip_{clip_id}_{int(task['spike_stream_time'])}s"
        out_clip_path = self.clips_dir / f"{stem}.mp4"
        out_srt_path = self.clips_dir / f"{stem}.srt"

        if not self.rec_path.exists() or self.rec_path.stat().st_size == 0:
            print(f"[Clip #{clip_id}] Recording file {self.rec_path} empty or missing.")
            return

        print(f"[FFmpeg] Cutting Clip #{clip_id}: {out_clip_path.name} (start: {start_time:.1f}s, dur: {duration:.1f}s, 9:16 vertical blurred background)...")

        # 9:16 Vertical with full uncropped center video and blurred top/bottom background (optimized)
        filter_complex = (
            "[0:v]scale=270:480:force_original_aspect_ratio=increase,"
            "crop=270:480,"
            "boxblur=8:2,"
            "scale=1080:1920[bg];"
            "[0:v]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_time:.2f}",
            "-t", f"{duration:.2f}",
            "-i", str(self.rec_path),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_clip_path),
        ]

        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            self.log(f"[FFmpeg Error] Clip #{clip_id} failed: {result.stderr[-400:]}")
            return

        self.log(f"✅ [FFmpeg] 9:16 Clip #{clip_id} generated: {out_clip_path.name} ({out_clip_path.stat().st_size / (1024*1024):.2f} MB)")

        if self.enable_whisper:
            self._generate_subtitles(out_clip_path, out_srt_path, clip_id)

    def _generate_subtitles(self, clip_path: Path, srt_path: Path, clip_id: int):
        self.log(f"🎙️ [Whisper] Transcribing subtitles for Clip #{clip_id} ({self.whisper_model_name})...")
        try:
            import whisper
            from whisper.utils import get_writer

            with _WHISPER_LOCK:
                if self._whisper_model is None:
                    self._whisper_model = whisper.load_model(self.whisper_model_name)
                transcription = self._whisper_model.transcribe(str(clip_path))
                writer = get_writer("srt", str(srt_path.parent))
                writer(transcription, clip_path.stem)
            self.log(f"📝 [Whisper] Subtitles written successfully: {srt_path.name}")
        except Exception as e:
            self.log(f"[Whisper Error] Transcription failed: {e}")

    def stop(self):
        self.running = False


def main():
    parser = argparse.ArgumentParser(
        description="clip-watcher: Record live stream (Twitch or YouTube), detect chat spikes, crop 9:16 vertical clips, and generate Whisper subtitles."
    )
    parser.add_argument("target", help="Twitch channel or YouTube URL/video_id (e.g. 'shroud' or 'https://www.youtube.com/watch?v=...')")
    parser.add_argument(
        "--min",
        dest="min_messages",
        type=int,
        default=5,
        help="Minimum messages in a 10s bucket to qualify as a spike. Default: 5",
    )
    parser.add_argument(
        "--multiplier",
        type=float,
        default=1.82,
        help="Multiplier over rolling median to trigger spike (default: 1.82)",
    )
    parser.add_argument(
        "--quality",
        default="best",
        help="Streamlink stream quality (default: best)",
    )
    parser.add_argument(
        "--model",
        dest="whisper_model",
        default="base",
        help="Whisper model for subtitles (default: base, options: tiny, base, small, medium)",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=30,
        help="Cooldown in seconds between clips. Default: 30s",
    )
    parser.add_argument(
        "--rec-dir",
        type=Path,
        default=Path("rec"),
        help="Directory to store recordings (default: rec)",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=Path("clips"),
        help="Directory to store 9:16 clips & srt (default: clips)",
    )
    parser.add_argument(
        "--no-whisper",
        action="store_true",
        help="Disable Whisper subtitles",
    )

    args = parser.parse_args()
    target_info = resolve_stream_target(args.target)

    # If YouTube target has no video_id yet (e.g. @Channel/live), probe streamlink for metadata
    if target_info["platform"] == "youtube" and not target_info.get("youtube_video_id"):
        print(f"[YouTube] Resolving live video ID for {target_info['stream_url']}...")
        try:
            probe = subprocess.run(["streamlink", "--json", target_info["stream_url"]], capture_output=True, text=True)
            meta = json.loads(probe.stdout)
            yt_id = meta.get("metadata", {}).get("id")
            if yt_id:
                target_info["youtube_video_id"] = yt_id
                target_info["display_name"] = f"youtube_{yt_id}"
                print(f"[YouTube] Found active live stream ID: {yt_id}")
        except Exception:
            pass

    print("=" * 70)
    print("                    CLIP-WATCHER                    ")
    print("=" * 70)
    print(f" Platform         : {target_info['platform'].upper()}")
    print(f" Target Stream    : {target_info['stream_url']}")
    print(f" Spike Threshold  : >= {args.multiplier}x (median of last 3 min) AND >= {args.min_messages} messages")
    print(f" Clip Parameters  : 20s before + 10s after, center-cropped 9:16")
    print(f" Subtitles        : Whisper ({args.whisper_model}) -> .srt")
    print(f" Output Folders   : {args.rec_dir}/ (full stream), {args.clips_dir}/ (clips & srt)")
    print("=" * 70)

    # 1. Start Chat Listener (Twitch IRC or YouTube Live Chat)
    if target_info["platform"] == "youtube":
        yt_id = target_info.get("youtube_video_id")
        if not yt_id:
            print("[Error] Could not determine YouTube live video ID for chat.")
            sys.exit(1)
        chat_listener = YouTubeChatListener(video_id=yt_id)
    else:
        chat_listener = TwitchIRCListener(channel=target_info["channel"])

    chat_listener.start()

    # 2. Start Streamlink recording
    recorder = StreamRecorder(target_info=target_info, quality=args.quality, rec_dir=args.rec_dir)
    if not recorder.start():
        chat_listener.stop()
        sys.exit(1)

    # 3. Setup Clip Processor & Spike Detector
    clip_processor = ClipProcessor(
        rec_path=recorder.rec_path,
        streamer_name=target_info["display_name"],
        clips_dir=args.clips_dir,
        whisper_model=args.whisper_model,
        enable_whisper=not args.no_whisper,
    )
    detector = SpikeDetector(
        min_messages=args.min_messages,
        bucket_seconds=10,
        window_minutes=3,
        cooldown_seconds=args.cooldown,
        multiplier=args.multiplier,
    )

    print("[Init] Stabilizing recording & chat (5s)...")
    time.sleep(5)

    if not recorder.is_alive():
        print(f"\n[Notice] Stream appears offline or streamlink exited.")
        chat_listener.stop()
        recorder.stop()
        clip_processor.stop()
        sys.exit(0)

    print("\n[Monitoring] Live analysis running. Press Ctrl+C to stop.\n")

    bucket_interval = 10.0
    next_bucket_time = time.time() + bucket_interval

    try:
        while True:
            if not recorder.is_alive():
                print("\n[Streamlink] Stream ended or disconnected.")
                break

            now = time.time()
            sleep_time = next_bucket_time - now
            if sleep_time > 0:
                time.sleep(min(1.0, sleep_time))
                continue

            bucket_end = next_bucket_time
            bucket_start = bucket_end - bucket_interval
            next_bucket_time += bucket_interval

            msg_count = chat_listener.get_messages_in_interval(bucket_start, bucket_end)
            stream_time = recorder.get_elapsed_stream_time()

            is_spike, med, threshold = detector.evaluate_bucket(msg_count, stream_time)

            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            elapsed_str = str(datetime.timedelta(seconds=int(stream_time)))

            spike_badge = " [*** CHAT SPIKE! ***]" if is_spike else ""
            print(
                f"[{now_str} | +{elapsed_str}] "
                f"Bucket (10s): {msg_count:3d} msgs | "
                f"3m Median: {med:4.1f} | "
                f"Threshold: {threshold:4.1f}{spike_badge}"
            )

            if is_spike:
                spike_center = max(0.0, stream_time - 5.0)
                clip_processor.schedule_clip(spike_center, trigger_wall_time=time.time())

    except KeyboardInterrupt:
        print("\n[Shutdown] Stopping...")
    finally:
        chat_listener.stop()
        recorder.stop()
        clip_processor.work_queue.join()
        clip_processor.stop()
        print("[Shutdown] Complete.")


if __name__ == "__main__":
    main()
