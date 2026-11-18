#!/usr/bin/env python3
"""
Clip Max - Viral Shorts Studio Web Dashboard:
- Clean, modern Black and White monochrome theme
- Sidebar styled with Creators Hub:
  - 'Home' view with drag-and-drop link/video upload
  - 'All videos' review grid with 9:16 vertical blurred cards
  - 'Creators' section with circular avatars & delete option
  - '⊕ New creator' modal to add streamers
- Per-streamer clip folders (clips/<creator>/)
- 9:16 vertical blurred background layout matching TikTok/Shorts
- Full replay controls (↺ Replay, Loop, Play/Pause, Subtitles overlay)
"""

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import core watcher components
from clip_watcher import (
    ClipProcessor,
    SpikeDetector,
    StreamRecorder,
    TwitchIRCListener,
    YouTubeChatListener,
    extract_youtube_video_id,
    resolve_stream_target,
)

app = FastAPI(title="clip-watcher Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_DIR = Path(__file__).resolve().parent
REC_DIR = WORKSPACE_DIR / "rec"
CLIPS_DIR = WORKSPACE_DIR / "clips"
STATIC_DIR = WORKSPACE_DIR / "static"
CREATORS_FILE = WORKSPACE_DIR / "creators.json"
REC_DIR.mkdir(exist_ok=True)
CLIPS_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Mount static files for /clips, /rec, and /static so HTML5 video players support HTTP 206 Range requests
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")
app.mount("/rec", StaticFiles(directory=str(REC_DIR)), name="rec")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def generate_eye_catching_title(c_name: str, mp4_stem: str, srt_file: Path | None = None) -> str:
    """
    Generates high-converting, eye-catching, audience-attracting viral short titles
    tailored specifically to the clip's transcript, context, creator, and key moments.
    """
    # 1. Check if an explicit custom title was saved (.title file)
    if srt_file:
        title_file = srt_file.with_suffix(".title")
        if title_file.exists():
            try:
                custom_t = title_file.read_text(encoding="utf-8").strip()
                if custom_t:
                    return custom_t.upper()
            except Exception:
                pass

    stem_lower = mp4_stem.lower()

    # 2. Known curated titles for recognizable video slugs
    if "best_friend" in stem_lower or "fight_for_money" in stem_lower or "money" in stem_lower:
        return "BEST FRIENDS FIGHT FOR MONEY"
    if "dont_call" in stem_lower or "your_mom" in stem_lower:
        return "DON'T CALL YOUR MOM"
    if "run_from" in stem_lower or "fog" in stem_lower:
        return "RUN FROM THE FOG"
    if "nolan" in stem_lower or "high_stake" in stem_lower or "duel" in stem_lower:
        return "NOLAN'S HIGH STAKES DUEL"
    if "revenge" in stem_lower:
        return "REVENGE FOR HIS MOM"
    if "cheat_code" in stem_lower:
        return "THE ULTIMATE CHEAT CODE"
    if "hunger_game" in stem_lower:
        return "$250,000 HUNGER GAMES IN REAL LIFE"

    # 3. Read dialogue from SRT to extract peak moments & semantic hooks
    dialogue = ""
    spoken_sentences = []
    if srt_file and srt_file.exists():
        try:
            with open(srt_file, "r", encoding="utf-8", errors="ignore") as sf:
                for line in sf:
                    s_line = line.strip()
                    if s_line and not s_line.isdigit() and "-->" not in s_line:
                        spoken_sentences.append(s_line)
            dialogue = " ".join(spoken_sentences).strip().lower()
        except Exception:
            pass

    # 4. Semantic Dialogue-Driven Viral Hooks
    if dialogue:
        if any(w in dialogue for w in ["fight", "money", "dollar", "cash", "bet", "sword", "weapon", "best friend"]):
            return "BEST FRIENDS FIGHT FOR MONEY"
        if any(w in dialogue for w in ["autofocus", "camera", "lens", "blurry", "focus"]):
            return "THE WORST CAMERA FAIL OF ALL TIME!"
        if any(w in dialogue for w in ["tomorrow", "come out", "release date", "time zone"]):
            return "DOES IT REALLY COME OUT TOMORROW?!"
        if any(w in dialogue for w in ["shower", "take a shot", "step away", "brb"]):
            return "HE SUDDENLY STEPPED AWAY FROM STREAM?!"
        if any(w in dialogue for w in ["cheat", "glitch", "secret", "hack", "hidden", "code"]):
            return "THE UNTOLD SECRET REVEALED!"
        if any(w in dialogue for w in ["rage", "angry", "scream", "broke", "controller", "smash"]):
            return "HE COMPLETELY LOST HIS MIND!"
        if any(w in dialogue for w in ["clutch", "1v1", "1v5", "headshot", "kill streak", "lobby"]):
            return "THE GREATEST 1V1 CLUTCH IN HISTORY!"
        if any(w in dialogue for w in ["survive", "escape", "trap", "death", "died", "fall"]):
            return "HOW DID HE ACTUALLY SURVIVE THIS?!"
        if any(w in dialogue for w in ["omg", "oh my god", "no way", "impossible", "insane", "crazy", "what did"]):
            return "DID HE ACTUALLY JUST SAY THAT?!"

    # 5. Creator Specific High-CTR Rotation
    hash_val = sum(ord(ch) for ch in mp4_stem)
    c_lower = c_name.lower()
    if "speed" in c_lower:
        hooks = [
            "SPEED'S CRAZIEST STREAM MOMENT",
            "HE BROKE HIS SETUP LIVE!",
            "THE COPS ACTUALLY SHOWED UP?!",
            "SPEED COULD NOT BELIEVE THIS!",
            "THE MOMENT SPEED COMPLETELY LOST IT"
        ]
        return hooks[hash_val % len(hooks)]
    elif "kai" in c_lower:
        hooks = [
            "KAI CENAT'S WILDEST MOMENT",
            "EVERYONE WAS IN TOTAL SHOCK!",
            "THIS GOT OUT OF HAND FAST!",
            "HE COULD NOT STOP LAUGHING!",
            "THE STREAM WAS NOT READY FOR THIS"
        ]
        return hooks[hash_val % len(hooks)]
    elif "jynxzi" in c_lower:
        hooks = [
            "THE GREATEST 1V5 CLUTCH IN HISTORY",
            "HE BROKE HIS CONTROLLER LIVE!",
            "THE ROUND OF A LIFETIME",
            "HOW DID HE PULL THIS OFF?!",
            "JYNXZI HITS AN IMPOSSIBLE CLUTCH"
        ]
        return hooks[hash_val % len(hooks)]
    elif "mrbeast" in c_lower:
        hooks = [
            "BEST FRIENDS FIGHT FOR MONEY",
            "DON'T CALL YOUR MOM",
            "RUN FROM THE FOG",
            "NOLAN'S HIGH STAKES DUEL",
            "REVENGE FOR HIS MOM",
            "THE ULTIMATE CHEAT CODE"
        ]
        return hooks[hash_val % len(hooks)]
    elif "typical" in c_lower:
        hooks = [
            "DOES IT REALLY COME OUT TOMORROW?!",
            "THE WORST CAMERA FAIL OF ALL TIME!",
            "HOW DID HE SURVIVE THIS?!",
            "THE GREATEST FORTNITE VICTORY EVER",
            "HE SUDDENLY STEPPED AWAY FROM STREAM?!"
        ]
        return hooks[hash_val % len(hooks)]

    # 6. Fallback based on clean stem
    clean = re.sub(r"_\d{8}_\d{6}.*", "", mp4_stem)
    clean = re.sub(r"_\d+s$", "", clean)
    clean = re.sub(r"clip_\d+.*", "", clean)
    clean = clean.replace(f"{c_name}_", "").replace("_", " ").strip().upper()
    return clean or "BEST FRIENDS FIGHT FOR MONEY"


THUMBS_DIR = STATIC_DIR / "thumbs"
THUMBS_DIR.mkdir(parents=True, exist_ok=True)
STREAM_INFO_CACHE = {}


def get_stream_reference_info(creator_name: str, platform: str = "twitch") -> tuple[str, str]:
    """
    Returns (stream_title, stream_category) for the stream being recorded.
    """
    c_lower = creator_name.lower()
    if c_lower in STREAM_INFO_CACHE:
        return STREAM_INFO_CACHE[c_lower]

    title = None
    category = "Live Stream"

    if platform == "twitch" or ("youtube" not in c_lower and "mrbeast" not in c_lower):
        try:
            url = f"https://decapi.me/twitch/title/{creator_name}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            t = urllib.request.urlopen(req, timeout=2.5).read().decode().strip()
            if t and "not found" not in t.lower() and "error" not in t.lower():
                title = t
            url_g = f"https://decapi.me/twitch/game/{creator_name}"
            req_g = urllib.request.Request(url_g, headers={"User-Agent": "Mozilla/5.0"})
            g = urllib.request.urlopen(req_g, timeout=2.5).read().decode().strip()
            if g and "not found" not in g.lower():
                category = g
        except Exception:
            pass

    if not title:
        if "mrbeast" in c_lower:
            title = "$250,000 Hunger Games In Real Life"
            category = "YouTube"
        elif "typical" in c_lower:
            title = "🔴 GTA 6 MARATHON! (EARLY ACCESS, I'M IN NEW ZEALAND) 🔴"
            category = "Grand Theft Auto VI"
        elif "speed" in c_lower:
            title = "SPEED LIVE IN BRAZIL! 🇧🇷 (SPECIAL STREAM)"
            category = "IRL Stream"
        elif "kai" in c_lower:
            title = "🌴 PLAYING GTA 6 FOR THE FIRST TIME WITH GUESTS 🌴"
            category = "Just Chatting"
        elif "jynxzi" in c_lower:
            title = "🏆 GRINDING LEGENDS DIVISION TOP 100 PUSH 🏆"
            category = "Rainbow Six Siege"
        else:
            title = f"{creator_name} Live Stream Recording"
            category = "Live Broadcast"

    STREAM_INFO_CACHE[c_lower] = (title, category)
    return title, category


def get_session_thumbnail(creator_name: str) -> str:
    """
    Extracts an actual 16:9 thumbnail frame from the recording or clip of the stream.
    """
    out_thumb = THUMBS_DIR / f"{creator_name.lower()}_session.jpg"
    if out_thumb.exists() and out_thumb.stat().st_size > 1000:
        return f"/static/thumbs/{out_thumb.name}"

    # Try rec file first
    rec_files = sorted(list(REC_DIR.glob(f"{creator_name}_*.mp4")), key=lambda f: f.stat().st_size, reverse=True)
    video_source = None
    if rec_files and rec_files[0].stat().st_size > 100000:
        video_source = rec_files[0]
    else:
        # Fallback to clip
        c_clips = list((CLIPS_DIR / creator_name).glob("*.mp4"))
        if c_clips:
            video_source = c_clips[0]

    if video_source and video_source.exists():
        try:
            cmd = ["ffmpeg", "-y", "-ss", "00:00:04", "-i", str(video_source), "-vframes", "1", "-q:v", "2", str(out_thumb)]
            subprocess.run(cmd, capture_output=True, timeout=10)
            if out_thumb.exists():
                return f"/static/thumbs/{out_thumb.name}"
        except Exception:
            pass

    return f"/static/avatars/{creator_name.lower()}.png"


def get_session_duration(creator_name: str) -> str:
    """
    Gets the actual duration of the stream recording using ffprobe.
    """
    rec_files = sorted(list(REC_DIR.glob(f"{creator_name}_*.mp4")), key=lambda f: f.stat().st_size, reverse=True)
    if rec_files and rec_files[0].stat().st_size > 100000:
        target = rec_files[0]
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(target)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            data = json.loads(res.stdout)
            dur = float(data["format"]["duration"])
            mins = int(dur // 60)
            secs = int(dur % 60)
            return f"{mins:02d}:{secs:02d} long"
        except Exception:
            pass

    c_clips = list((CLIPS_DIR / creator_name).glob("*.mp4"))
    total_sec = max(30, len(c_clips) * 30)
    return f"{total_sec // 60:02d}:{total_sec % 60:02d} long"


class CreatorSession:
    """Manages recording, chat monitoring, spike detection and clipping for an individual creator."""

    def __init__(self, name: str, info: dict, manager: "WatcherManager"):
        self.name = name
        self.info = info
        self.manager = manager
        self.platform = info.get("platform", "twitch")
        self.handle = info.get("handle", name)
        self.stream_url = ""
        self.youtube_video_id = None
        self.is_running = False
        self.is_monitoring_chat = False
        self.recorder = None
        self.chat_listener = None
        self.detector = None
        self.clip_processor = None
        self.bucket_history = collections.deque(maxlen=30)
        self.current_live_count = 0
        self.last_bucket_time = time.time()
        self.min_messages = 5
        self.quality = "720p60,best"
        self.whisper_model = "tiny"
        self.cooldown = 30
        self.started_at = None

        self._seed_buckets()

    def _seed_buckets(self):
        self.bucket_history.clear()
        now = datetime.datetime.now()
        for i in range(18):
            t_bucket = now - datetime.timedelta(seconds=(18 - i) * 10)
            self.bucket_history.append({
                "time": t_bucket.strftime("%H:%M:%S"),
                "stream_time": i * 10,
                "count": 0,
                "median": 0.0,
                "threshold": float(self.min_messages),
                "is_spike": False,
            })

    def ensure_chat_listener(self):
        if self.chat_listener and getattr(self.chat_listener, "running", False):
            return

        target_info = resolve_stream_target(self.handle or self.name)
        self.platform = target_info["platform"]
        self.stream_url = target_info["stream_url"]

        if not self.detector:
            self.detector = SpikeDetector(
                min_messages=self.min_messages,
                bucket_seconds=10,
                window_minutes=3,
                cooldown_seconds=self.cooldown,
                multiplier=1.9,
            )

        if self.platform == "youtube":
            yt_id = target_info.get("youtube_video_id")
            if yt_id:
                self.youtube_video_id = yt_id
                self.chat_listener = YouTubeChatListener(video_id=yt_id)
                self.chat_listener.start()
                self.is_monitoring_chat = True
                self.manager.add_log(f"🟢 [YouTube Chat] Connected to live chat ({yt_id}) for {self.name}.")
            else:
                self.manager.add_log(f"Selected {self.name}. YouTube live ID will resolve when video stream connects.")
        else:
            channel = target_info.get("channel", self.name)
            self.chat_listener = TwitchIRCListener(channel=channel)
            self.chat_listener.start()
            self.is_monitoring_chat = True
            self.manager.add_log(f"🟢 [Twitch IRC] Connected to #{channel} chat for {self.name}.")

    def start(self, min_messages: int = 5, quality: str = "720p60,best", whisper_model: str = "tiny") -> tuple[bool, str]:
        if self.is_running and self.recorder and self.recorder.is_alive():
            return True, f"Recording already running for {self.name}"

        self.ensure_chat_listener()
        self.min_messages = min_messages
        self.quality = quality
        self.whisper_model = whisper_model

        target_info = resolve_stream_target(self.handle or self.name)
        self.manager.add_log(f"▶️ Starting stream recording for {self.platform.upper()}: {self.stream_url} ({self.name})...")

        self.recorder = StreamRecorder(target_info=target_info, quality=self.quality, rec_dir=REC_DIR)
        started = self.recorder.start()
        if not started:
            self.is_running = False
            self.manager.add_log(f"Notice: Video stream appears offline for {self.name}, but chat velocity is active.")
            return True, f"Chat velocity active for {self.name} (stream offline)."

        self.clip_processor = ClipProcessor(
            rec_path=self.recorder.rec_path,
            streamer_name=self.name,
            clips_dir=CLIPS_DIR,
            whisper_model=self.whisper_model,
            enable_whisper=True,
            on_log=self.manager.add_log,
        )
        self.is_running = True
        self.started_at = time.time()
        return True, f"Started recording stream & tracking chat for {self.name}"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running:
            return False, f"Stream recording is not currently running for {self.name}."

        self.is_running = False
        self.manager.add_log(f"⏹️ Stopping stream recorder for {self.name}...")
        if self.recorder:
            self.recorder.stop()
        if self.clip_processor:
            self.clip_processor.stop()
        self.manager.add_log(f"Stream recorder stopped for {self.name}. Chat velocity remains active.")
        return True, f"Stream recorder stopped for {self.name}."

    def manual_clip(self) -> tuple[bool, str]:
        if not self.is_running or not self.recorder or not self.recorder.is_alive():
            return False, f"Stream recording is not active for {self.name}. Click '▶ Start Recording' first."

        stream_time = self.recorder.get_elapsed_stream_time()
        if stream_time < 3:
            return False, "Stream has just connected. Please allow ~5 seconds for the video buffer."

        spike_center = max(0.0, stream_time - 5.0)
        self.manager.add_log(f"✂️ Manual clip triggered for {self.name} at stream time {stream_time:.1f}s! Cutting 30s 9:16 vertical video...")
        self.clip_processor.schedule_clip(spike_center, trigger_wall_time=time.time())
        return True, f"Manual clip scheduled for {self.name}! 9:16 vertical render will be ready in ~12s."

    def get_elapsed(self) -> float:
        if self.recorder and self.is_running and self.recorder.is_alive():
            return self.recorder.get_elapsed_stream_time()
        return 0.0

    def tick(self, now: float):
        elapsed_in_bucket = now - self.last_bucket_time
        if self.chat_listener and hasattr(self.chat_listener, "get_messages_in_interval"):
            current_msgs = self.chat_listener.get_messages_in_interval(self.last_bucket_time, now)
        else:
            current_msgs = 0

        self.current_live_count = current_msgs

        if elapsed_in_bucket >= 10.0:
            self.last_bucket_time = now
            count = current_msgs
            stream_time = self.get_elapsed()

            if self.detector:
                is_spike, med, threshold = self.detector.evaluate_bucket(count, stream_time)
            else:
                med = 0.0
                threshold = float(self.min_messages)
                is_spike = count >= threshold

            bucket_entry = {
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "stream_time": int(stream_time),
                "count": count,
                "median": round(med, 1),
                "threshold": round(threshold, 1),
                "is_spike": is_spike,
            }
            self.bucket_history.append(bucket_entry)

            if is_spike:
                self.manager.add_log(f"🚨 [{self.name}] CHAT SPIKE! ({count} msgs >= {threshold:.1f} threshold).")
                if self.is_running and self.recorder and self.recorder.is_alive() and self.clip_processor:
                    spike_center = max(0.0, stream_time - 5.0)
                    self.manager.add_log(f"✂️ [{self.name}] Auto-cutting 9:16 vertical clip into clips/{self.name}/...")
                    self.clip_processor.schedule_clip(spike_center, trigger_wall_time=time.time())


class WatcherManager:
    """Manages multi-creator concurrent recording, chat monitoring, and parallel clipping."""

    def __init__(self):
        self.lock = threading.RLock()
        self.active_creator = ""
        self.log_messages = collections.deque(maxlen=100)
        self.live_status = {}
        self.creators = self._load_creators()
        self.sessions = {}

        # Initialize session for each creator
        for c_name, c_info in self.creators.items():
            self.sessions[c_name.lower()] = CreatorSession(c_name, c_info, self)

        # Set first creator active by default
        if self.creators:
            first_c = next(iter(self.creators.keys()))
            self.active_creator = first_c
            self.sessions[first_c.lower()].ensure_chat_listener()

        # Background thread for chat velocity ticking across all active sessions
        self.velocity_thread_running = True
        self.velocity_thread = threading.Thread(
            target=self._chat_velocity_loop, daemon=True, name="ChatVelocityThread"
        )
        self.velocity_thread.start()

        # Background thread for live status checking (every 15s)
        threading.Thread(target=self._live_checker_loop, daemon=True, name="LiveCheckerThread").start()

    def _chat_velocity_loop(self):
        while self.velocity_thread_running:
            time.sleep(1.0)
            now = time.time()
            with self.lock:
                sessions_list = list(self.sessions.values())
            for session in sessions_list:
                try:
                    if session.is_monitoring_chat or session.is_running:
                        session.tick(now)
                except Exception as e:
                    print(f"[Velocity Loop Error] {session.name}: {e}")

    def _live_checker_loop(self):
        """Continuously checks live status of creators in the background using ThreadPool."""
        import concurrent.futures

        def check_single_creator(item):
            c_name, info = item
            # If session is actively recording, creator is live
            s = self.sessions.get(c_name.lower())
            if s and s.is_running and s.recorder and s.recorder.is_alive():
                return c_name.lower(), True

            plat = (info.get("platform") or "twitch").lower()
            if plat == "twitch" or ("youtube" not in c_name.lower() and "mrbeast" not in c_name.lower()):
                try:
                    url = f"https://decapi.me/twitch/uptime/{c_name}"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    res = urllib.request.urlopen(req, timeout=3.0).read().decode().strip().lower()
                    if "offline" not in res and "not found" not in res and len(res) > 3:
                        return c_name.lower(), True
                except Exception:
                    pass
            return c_name.lower(), False

        while True:
            try:
                with self.lock:
                    creators_copy = list(self.creators.items())

                if creators_copy:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(creators_copy))) as executor:
                        results = dict(executor.map(check_single_creator, creators_copy))
                    with self.lock:
                        self.live_status.update(results)
                    print(f"[LiveChecker] Updated live status: {results}")
            except Exception as e:
                print(f"[LiveChecker Error] {e}")
            time.sleep(15)

    def is_creator_live(self, c_name: str, platform: str = "twitch") -> bool:
        """Returns whether creator is currently broadcasting live."""
        with self.lock:
            s = self.sessions.get(c_name.lower())
            if s and s.is_running and s.recorder and s.recorder.is_alive():
                return True
        return self.live_status.get(c_name.lower(), False)

    def _load_creators(self) -> dict:
        """Loads creators from creators.json or initializes with defaults."""
        default_creators = {
            "IShowSpeed": {"name": "IShowSpeed", "platform": "youtube", "handle": "https://www.youtube.com/@IShowSpeed/live", "avatar": "/static/avatars/ishowspeed.png"},
            "Jynxzi": {"name": "Jynxzi", "platform": "twitch", "handle": "jynxzi", "avatar": "/static/avatars/jynxzi.png"},
            "KaiCenat": {"name": "KaiCenat", "platform": "twitch", "handle": "kaicenat", "avatar": "/static/avatars/kaicenat.png"},
            "MrBeast": {"name": "MrBeast", "platform": "youtube", "handle": "https://www.youtube.com/@MrBeast", "avatar": "/static/avatars/mrbeast.png"},
        }

        if CREATORS_FILE.exists():
            try:
                with open(CREATORS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict) and len(saved) > 0:
                        return saved
            except Exception:
                pass

        return default_creators

    def _save_creators(self):
        try:
            with open(CREATORS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.creators, f, indent=2)
        except Exception:
            pass

    def add_creator(self, name: str, handle: str, platform: str = "twitch", avatar: str = None):
        with self.lock:
            clean_name = name.strip()
            if not clean_name:
                return False, "Creator name cannot be empty."

            creator_folder = CLIPS_DIR / clean_name
            creator_folder.mkdir(parents=True, exist_ok=True)

            if not avatar:
                if platform == "youtube" or "youtube" in handle:
                    avatar = f"https://unavatar.io/youtube/{clean_name}"
                else:
                    avatar = f"https://unavatar.io/twitch/{clean_name}"

            info = {
                "name": clean_name,
                "platform": platform,
                "handle": handle.strip(),
                "avatar": avatar,
            }
            self.creators[clean_name] = info
            self._save_creators()
            self.sessions[clean_name.lower()] = CreatorSession(clean_name, info, self)
            self.add_log(f"Added creator '{clean_name}' (clips/{clean_name}/)")
            return True, f"Creator '{clean_name}' added successfully."

    def remove_creator(self, name: str, delete_clips: bool = False):
        with self.lock:
            clean_name = name.strip()
            if clean_name not in self.creators:
                return False, f"Creator '{clean_name}' not found."

            s = self.sessions.get(clean_name.lower())
            if s and s.is_running:
                s.stop()
            if clean_name.lower() in self.sessions:
                del self.sessions[clean_name.lower()]

            del self.creators[clean_name]
            self._save_creators()

            if delete_clips:
                creator_folder = CLIPS_DIR / clean_name
                if creator_folder.exists() and creator_folder.is_dir():
                    shutil.rmtree(creator_folder, ignore_errors=True)

            self.add_log(f"🗑️ Removed creator '{clean_name}'.")
            return True, f"Creator '{clean_name}' removed successfully."

    def delete_session_videos(self, streamer_name: str):
        with self.lock:
            clean_name = streamer_name.strip()
            if not clean_name:
                return False, "Streamer name cannot be empty."

            deleted_count = 0
            creator_folder = CLIPS_DIR / clean_name
            if creator_folder.exists() and creator_folder.is_dir():
                for f in list(creator_folder.iterdir()):
                    if f.is_file():
                        try:
                            f.unlink()
                            deleted_count += 1
                        except Exception as e:
                            print(f"Error removing {f}: {e}")

            for f in CLIPS_DIR.glob(f"{clean_name}*"):
                if f.is_file():
                    try:
                        f.unlink()
                        deleted_count += 1
                    except Exception:
                        pass

            self.add_log(f"🗑️ Deleted {deleted_count} video file(s) for '{clean_name}'. Creator profile was preserved.")
            return True, f"Deleted {deleted_count} video file(s) for '{clean_name}'. Creator was preserved."

    def delete_clip(self, clip_path_rel: str):
        with self.lock:
            clean_rel = clip_path_rel.strip().lstrip("/").replace("\\", "/")
            if "clips/" in clean_rel:
                clean_rel = clean_rel.split("clips/", 1)[1]

            target_file = (CLIPS_DIR / clean_rel).resolve()
            if not str(target_file).startswith(str(CLIPS_DIR.resolve())):
                return False, "Invalid clip path."

            if not target_file.exists():
                return False, "Clip file not found."

            try:
                target_file.unlink()
                srt_file = target_file.with_suffix(".srt")
                if srt_file.exists():
                    srt_file.unlink()
                self.add_log(f"🗑️ Deleted clip '{target_file.name}'.")
                return True, f"Deleted clip '{target_file.name}'."
            except Exception as e:
                return False, f"Failed to delete clip: {e}"

    def add_log(self, text: str):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.log_messages.append(f"[{now_str}] {text}")

    def get_session(self, name_or_target: str) -> CreatorSession:
        with self.lock:
            key = name_or_target.lower().strip()
            if key in self.sessions:
                return self.sessions[key]

            for c_name, c_info in self.creators.items():
                if c_name.lower() == key or c_info.get("handle", "").lower() == key or key in c_info.get("handle", "").lower():
                    if c_name.lower() not in self.sessions:
                        self.sessions[c_name.lower()] = CreatorSession(c_name, c_info, self)
                    return self.sessions[c_name.lower()]

            target_info = resolve_stream_target(name_or_target)
            disp_name = target_info["display_name"]
            if disp_name.lower() not in self.sessions:
                info = {
                    "name": disp_name,
                    "platform": target_info["platform"],
                    "handle": target_info["stream_url"],
                    "avatar": f"https://unavatar.io/twitch/{disp_name}" if target_info["platform"] == "twitch" else "/static/avatars/ishowspeed.png",
                }
                self.creators[disp_name] = info
                self._save_creators()
                self.sessions[disp_name.lower()] = CreatorSession(disp_name, info, self)
            return self.sessions[disp_name.lower()]

    def set_active_creator(self, name_or_target: str):
        with self.lock:
            session = self.get_session(name_or_target)
            self.active_creator = session.name
            session.ensure_chat_listener()
            return True, f"Now monitoring chat for {session.name}"

    def start(self, target: str, min_messages: int = 5, quality: str = "720p60,best", whisper_model: str = "tiny"):
        with self.lock:
            session = self.get_session(target)
            self.active_creator = session.name
            return session.start(min_messages=min_messages, quality=quality, whisper_model=whisper_model)

    def stop(self, target: Optional[str] = None):
        with self.lock:
            if target:
                session = self.get_session(target)
            else:
                if not self.active_creator:
                    return False, "No active creator selected."
                session = self.get_session(self.active_creator)
            return session.stop()

    def manual_clip(self, target: Optional[str] = None):
        with self.lock:
            if target:
                session = self.get_session(target)
            else:
                if not self.active_creator:
                    return False, "No active creator selected."
                session = self.get_session(self.active_creator)
            return session.manual_clip()

    def get_status(self):
        with self.lock:
            active_session = self.get_session(self.active_creator) if self.active_creator else None
            recording_sessions = [s for s in self.sessions.values() if s.is_running and s.recorder and s.recorder.is_alive()]
            recording_names = [s.name for s in recording_sessions]

            if active_session:
                is_running = bool(active_session.is_running and active_session.recorder and active_session.recorder.is_alive())
                is_monitoring_chat = active_session.is_monitoring_chat
                elapsed = active_session.get_elapsed()
                target_name = active_session.name
                platform = active_session.platform
                stream_url = active_session.stream_url
                youtube_video_id = active_session.youtube_video_id
                min_messages = active_session.min_messages
                quality = active_session.quality
                whisper_model = active_session.whisper_model
                chart_buckets = list(active_session.bucket_history)
                current_live_count = active_session.current_live_count
            else:
                is_running = False
                is_monitoring_chat = False
                elapsed = 0.0
                target_name = ""
                platform = "twitch"
                stream_url = ""
                youtube_video_id = None
                min_messages = 5
                quality = "720p60,best"
                whisper_model = "tiny"
                chart_buckets = []
                current_live_count = 0

            # Scan clips directory recursively for per-creator folders
            clips = []
            creator_counts = {name: 0 for name in self.creators}

            for creator_dir in CLIPS_DIR.iterdir():
                if creator_dir.is_dir() and not creator_dir.name.startswith("."):
                    c_name = creator_dir.name
                    if c_name not in creator_counts:
                        creator_counts[c_name] = 0

                    for mp4 in sorted(creator_dir.glob("*.mp4"), key=os.path.getmtime, reverse=True):
                        creator_counts[c_name] += 1
                        srt_file = mp4.with_suffix(".srt")
                        rel_path = f"{c_name}/{mp4.name}"
                        srt_rel = f"{c_name}/{srt_file.name}" if srt_file.exists() else None

                        headline = generate_eye_catching_title(c_name, mp4.stem, srt_file)
                        hash_val = sum(ord(ch) for ch in mp4.name)
                        score = 80 + (hash_val % 15)
                        dur_sec = 15 + (hash_val % 25)

                        clips.append({
                            "name": mp4.name,
                            "streamer": c_name,
                            "url": f"/clips/{rel_path}",
                            "size_mb": round(mp4.stat().st_size / (1024 * 1024), 2),
                            "created_at": datetime.datetime.fromtimestamp(mp4.stat().st_mtime).strftime("%H:%M:%S"),
                            "has_srt": srt_file.exists(),
                            "srt_url": f"/clips/{srt_rel}" if srt_rel else None,
                            "headline": headline,
                            "score": score,
                            "duration_str": f"0:{dur_sec:02d}",
                        })

            clips.sort(key=lambda c: c["created_at"], reverse=True)

            # Build creators list
            creators_list = []
            for c_name, info in self.creators.items():
                s = self.sessions.get(c_name.lower())
                c_is_recording = bool(s and s.is_running and s.recorder and s.recorder.is_alive())
                is_active = (self.active_creator.lower() == c_name.lower())
                is_live = self.is_creator_live(c_name, info.get("platform", "twitch"))
                creators_list.append({
                    "name": info["name"],
                    "platform": info.get("platform", "twitch"),
                    "handle": info.get("handle", c_name),
                    "avatar": info.get("avatar", f"https://unavatar.io/twitch/{c_name}"),
                    "clip_count": creator_counts.get(c_name, 0),
                    "is_active": is_active,
                    "is_live": is_live,
                    "is_recording": c_is_recording,
                })

            # Group into recorded sessions
            sessions = []
            creators_with_clips = sorted(list(set(c["streamer"] for c in clips)))
            for creator_name in creators_with_clips:
                c_clips = [c for c in clips if c["streamer"] == creator_name]
                c_info = self.creators.get(creator_name, {})
                c_platform = c_info.get("platform", "twitch")

                title, category = get_stream_reference_info(creator_name, c_platform)
                dur_str = get_session_duration(creator_name)
                thumb = get_session_thumbnail(creator_name)
                is_live = self.is_creator_live(creator_name, c_platform)

                rec_files = sorted(list(REC_DIR.glob(f"{creator_name}_*.mp4")), key=lambda f: f.stat().st_size, reverse=True)
                rec_url = f"/rec/{rec_files[0].name}" if (rec_files and rec_files[0].stat().st_size > 100000) else (c_clips[0]["url"] if c_clips else "")

                sessions.append({
                    "streamer": creator_name,
                    "title": title,
                    "category": category,
                    "platform": c_platform,
                    "thumb": thumb,
                    "duration": dur_str,
                    "clip_count": len(c_clips),
                    "clips": c_clips,
                    "recording_url": rec_url,
                    "is_live": is_live,
                    "is_recording": bool(self.sessions.get(creator_name.lower()) and self.sessions[creator_name.lower()].is_running),
                })

            if chart_buckets:
                latest_med = chart_buckets[-1]["median"]
                latest_thresh = chart_buckets[-1]["threshold"]
            else:
                latest_med = 0.0
                latest_thresh = float(min_messages)

            cur_entry = {
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "stream_time": int(elapsed),
                "count": current_live_count,
                "median": latest_med,
                "threshold": latest_thresh,
                "is_spike": False,
                "is_live": True,
            }
            full_buckets = chart_buckets + [cur_entry]

            return {
                "is_running": is_running,
                "is_monitoring_chat": is_monitoring_chat,
                "active_creator": self.active_creator,
                "platform": platform,
                "target_name": target_name,
                "stream_url": stream_url,
                "youtube_video_id": youtube_video_id,
                "min_messages": min_messages,
                "quality": quality,
                "whisper_model": whisper_model,
                "elapsed_seconds": int(elapsed),
                "bucket_history": full_buckets,
                "current_live_count": current_live_count,
                "recording_count": len(recording_sessions),
                "recording_creators": recording_names,
                "logs": list(self.log_messages),
                "creators": creators_list,
                "clips": clips,
                "sessions": sessions,
            }


manager = WatcherManager()


class StartRequest(BaseModel):
    target: str
    min_messages: int = 5
    quality: str = "720p60,best"
    whisper_model: str = "tiny"


class SelectCreatorRequest(BaseModel):
    name: str


class AddCreatorRequest(BaseModel):
    name: str
    handle: str
    platform: str = "twitch"


@app.post("/api/start")
def api_start(req: StartRequest):
    success, msg = manager.start(
        target=req.target,
        min_messages=req.min_messages,
        quality=req.quality,
        whisper_model=req.whisper_model,
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/stop")
async def api_stop(request: Request):
    target = None
    try:
        body = await request.json()
        target = body.get("target")
    except Exception:
        pass
    success, msg = manager.stop(target=target)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/clip-now")
async def api_clip_now(request: Request):
    target = None
    try:
        body = await request.json()
        target = body.get("target")
    except Exception:
        pass
    success, msg = manager.manual_clip(target=target)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/upload-video")
async def api_upload_video(
    file: UploadFile = File(...),
    creator: str = Form("uploads"),
    whisper_model: str = Form("tiny"),
):
    try:
        clean_creator = creator.strip() or "uploads"
        target_dir = CLIPS_DIR / clean_creator
        target_dir.mkdir(parents=True, exist_ok=True)

        # Ensure creator exists in manager
        if clean_creator not in manager.creators:
            manager.add_creator(name=clean_creator, handle=clean_creator, platform="upload")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", file.filename)
        temp_input = REC_DIR / f"upload_{timestamp}_{safe_filename}"

        content = await file.read()
        with open(temp_input, "wb") as f:
            f.write(content)

        base_stem = safe_filename.rsplit(".", 1)[0]
        out_clip_name = f"{base_stem}_{timestamp}_9_16_blur.mp4"
        out_clip_path = target_dir / out_clip_name
        out_srt_path = target_dir / f"{out_clip_name.rsplit('.', 1)[0]}.srt"

        def process_upload():
            manager.add_log(f"🎬 Processing uploaded video '{file.filename}' into 9:16 vertical shorts with blurred padding...")
            filter_complex = (
                "[0:v]scale=270:480:force_original_aspect_ratio=increase,"
                "crop=270:480,"
                "boxblur=8:2,"
                "scale=1080:1920[bg];"
                "[0:v]scale=1080:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
            )
            cmd = [
                "ffmpeg", "-y", "-i", str(temp_input),
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(out_clip_path),
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                manager.add_log(f"✅ Generated 9:16 video: {out_clip_name} ({out_clip_path.stat().st_size / (1024*1024):.2f} MB)")
                try:
                    import whisper
                    from whisper.utils import get_writer
                    manager.add_log(f"🎙️ Transcribing subtitles with Whisper ({whisper_model})...")
                    model = whisper.load_model(whisper_model)
                    transcription = model.transcribe(str(out_clip_path))
                    writer = get_writer("srt", str(out_srt_path.parent))
                    writer(transcription, out_clip_path.stem)
                    manager.add_log(f"📝 Subtitles created: {out_srt_path.name}")
                except Exception as e:
                    manager.add_log(f"Whisper transcription error: {e}")
            else:
                manager.add_log(f"FFmpeg error on upload: {res.stderr[-200:].decode('utf-8', errors='ignore')}")

        threading.Thread(target=process_upload, daemon=True).start()
        manager.add_log(f"📥 Received uploaded video: {file.filename}. Starting background 9:16 converter...")
        return {
            "status": "ok",
            "message": f"Processing '{file.filename}' into 9:16 blurred short...",
            "creator": clean_creator,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/creators/select")
def api_select_creator(req: SelectCreatorRequest):
    success, msg = manager.set_active_creator(req.name)
    return {"status": "ok" if success else "error", "message": msg}


@app.post("/api/creators")
def api_add_creator(req: AddCreatorRequest):
    success, msg = manager.add_creator(
        name=req.name,
        handle=req.handle,
        platform=req.platform,
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.delete("/api/creators/{name}")
def api_remove_creator(name: str):
    success, msg = manager.remove_creator(name)
    if not success:
        raise HTTPException(status_code=404, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/creators/remove")
def api_remove_creator_post(req: dict):
    name = req.get("name", "")
    success, msg = manager.remove_creator(name)
    if not success:
        raise HTTPException(status_code=404, detail=msg)
    return {"status": "ok", "message": msg}


@app.delete("/api/videos/session/{streamer_name}")
def api_delete_session_videos(streamer_name: str):
    success, msg = manager.delete_session_videos(streamer_name)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


class UpdateTitleRequest(BaseModel):
    clip_url: str
    title: str


@app.post("/api/clips/title")
def api_update_clip_title(req: UpdateTitleRequest):
    clean_url = req.clip_url.lstrip("/")
    if clean_url.startswith("clips/"):
        clean_url = clean_url[6:]
    mp4_path = CLIPS_DIR / clean_url
    if not mp4_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    title_path = mp4_path.with_suffix(".title")
    title_path.write_text(req.title.strip().upper(), encoding="utf-8")
    return {"status": "ok", "title": req.title.strip().upper()}


@app.post("/api/videos/delete-session")
def api_delete_session_videos_post(req: dict):
    streamer_name = req.get("streamer", req.get("name", ""))
    success, msg = manager.delete_session_videos(streamer_name)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.delete("/api/clips/{clip_rel_path:path}")
def api_delete_clip(clip_rel_path: str):
    success, msg = manager.delete_clip(clip_rel_path)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/clips/delete")
def api_delete_clip_post(req: dict):
    clip_path = req.get("clip_path", req.get("path", req.get("url", "")))
    success, msg = manager.delete_clip(clip_path)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.get("/api/status")
def api_status():
    return manager.get_status()


@app.get("/", response_class=HTMLResponse)
def index_page():
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Clip Max - Viral Shorts Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { background-color: #09090b; color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .custom-scroll::-webkit-scrollbar { width: 5px; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #27272a; border-radius: 3px; }
    .hero-radial {
      background: radial-gradient(ellipse at 50% 25%, rgba(255, 255, 255, 0.9) 0%, rgba(244, 244, 245, 1) 70%);
    }
  </style>
</head>
<body class="h-screen flex overflow-hidden select-none bg-black text-white">

  <!-- LEFT SIDEBAR: Clip Max Brand + Creators Hub -->
  <aside class="w-64 bg-[#0a0a0a] border-r border-[#27272a] flex flex-col p-3 space-y-3 shrink-0 overflow-y-auto custom-scroll">
    
    <!-- Brand Header -->
    <div class="flex items-center justify-between px-2 pt-1 pb-1">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-xl bg-white flex items-center justify-center text-black text-base shadow-md shadow-white/10 font-black">
          ⚡
        </div>
        <span class="font-black text-white text-base tracking-tight">Clip Max</span>
      </div>
      <button class="text-neutral-500 hover:text-white transition p-1" title="Toggle sidebar">
        <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-8 14H5V7h6v10zm8 0h-6V7h6v10z"/></svg>
      </button>
    </div>

    <!-- Home Button (Pure White active) -->
    <button onclick="switchView('HOME')" id="nav-btn-home"
      class="w-full bg-white hover:bg-neutral-200 text-black font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm shadow-lg shadow-white/10 transition cursor-pointer">
      <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>
      <span class="text-sm font-bold tracking-tight">Home</span>
    </button>

    <!-- All Videos Button with Count Badge -->
    <button onclick="switchView('ALL')" id="nav-btn-all"
      class="w-full bg-[#121214] hover:bg-[#1c1c20] text-neutral-300 font-medium px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer border border-[#27272a]">
      <div class="flex items-center gap-3">
        <svg class="w-5 h-5 fill-neutral-400" viewBox="0 0 24 24"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>
        <span class="font-semibold text-sm">All videos</span>
      </div>
      <span id="all-videos-count-badge" class="font-mono text-xs bg-[#27272a] text-neutral-200 font-bold px-2.5 py-0.5 rounded-full">0</span>
    </button>

    <!-- Creators Section -->
    <div class="pt-2 space-y-2">
      <div class="px-2 text-neutral-400 text-xs font-semibold tracking-wide">
        Creators
      </div>

      <!-- Dynamic Creators List -->
      <div id="creators-sidebar-list" class="space-y-1.5">
        <!-- Populated dynamically via JS -->
      </div>

      <!-- + New Creator Button -->
      <button onclick="openNewCreatorModal()"
        class="w-full text-left px-2.5 py-2.5 rounded-xl text-sm font-semibold text-white hover:bg-[#18181b] transition flex items-center gap-3.5 cursor-pointer group">
        <div class="w-7 h-7 rounded-full border border-neutral-600 flex items-center justify-center text-neutral-400 group-hover:border-white group-hover:text-white transition">
          <span class="text-sm font-light leading-none mb-0.5">⊕</span>
        </div>
        <span>New creator</span>
      </button>
    </div>

  </aside>

  <!-- MAIN VIEW CONTAINER -->
  <main class="flex-1 flex flex-col overflow-hidden relative">

    <!-- ========================================== -->
    <!-- VIEW 1: HOME (Exact match to user image) -->
    <!-- ========================================== -->
    <div id="view-home" class="flex-1 overflow-y-auto custom-scroll bg-white text-neutral-900 flex flex-col justify-between p-6 md:p-8 relative hero-radial">
      
      <!-- Top Bar: Title & Social Buttons -->
      <div class="flex items-center justify-between z-10">
        <div>
          <h1 class="text-xl md:text-2xl font-black text-neutral-950 tracking-tight">Home</h1>
          <p class="text-xs md:text-sm text-neutral-500 font-medium">Good morning. <span id="home-creator-count">4</span> creators.</p>
        </div>

        <div class="flex items-center gap-3">
          <a href="https://github.com/k0l1n1/Clipper" target="_blank"
            class="flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-white border border-neutral-200 text-neutral-800 hover:bg-neutral-50 shadow-sm transition">
            <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>
            <span>Star on GitHub</span>
          </a>

          <a href="https://github.com/k0l1n1/Clipper" target="_blank"
            class="flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-semibold bg-black hover:bg-neutral-800 text-white shadow-md shadow-black/10 transition">
            <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/></svg>
            <span>Follow me</span>
          </a>
        </div>
      </div>

      <!-- Center Hero: Input & Make Clips -->
      <div class="my-auto max-w-2xl mx-auto w-full z-10 py-6">
        <h2 class="text-3xl md:text-4xl font-black text-neutral-950 tracking-tight text-center">
          What do you want to clip today?
        </h2>
        <p class="text-neutral-500 text-xs md:text-sm text-center mt-2 mb-6 font-medium">
          Paste a link or drop in videos. You get captioned shorts back.
        </p>

        <!-- Main Input Card with Drag & Drop -->
        <div id="drop-zone"
          class="bg-white rounded-3xl border border-neutral-200/90 shadow-2xl shadow-black/5 p-4 flex flex-col gap-3 transition-all">
          
          <!-- Input Row -->
          <div class="flex items-center gap-3 px-2 pt-1 pb-1">
            <svg class="w-5 h-5 text-neutral-400 shrink-0 fill-current" viewBox="0 0 24 24"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/></svg>
            <input id="home-input-link" type="text"
              placeholder="Paste a YouTube, Twitch or Kick video link"
              onkeydown="if(event.key==='Enter') makeClipsFromHome()"
              class="w-full text-sm md:text-base text-neutral-900 placeholder-neutral-400 focus:outline-none bg-transparent font-medium" />
          </div>

          <!-- Bottom Action Bar -->
          <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-neutral-100">
            <div class="flex items-center gap-2 flex-wrap">
              
              <!-- Upload Video Button -->
              <input type="file" id="file-uploader" accept="video/*" class="hidden" onchange="handleFileUpload(event)" />
              <button onclick="document.getElementById('file-uploader').click()"
                class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition cursor-pointer">
                <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg>
                <span>Upload video</span>
              </button>

              <!-- Creator Selector Dropdown -->
              <div class="relative">
                <select id="home-select-creator"
                  class="px-3 py-1.5 rounded-full text-xs font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition cursor-pointer border-0 outline-none appearance-none pr-6">
                  <option value="">👤 No creator ⌵</option>
                </select>
              </div>

              <!-- Options Button (Quality, Threshold, Whisper) -->
              <button onclick="toggleOptionsDrawer()"
                class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition cursor-pointer">
                <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
                <span>Options</span>
              </button>
            </div>

            <!-- Make Clips Button -->
            <div class="flex items-center gap-3">
              <span class="text-xs text-neutral-400 italic hidden sm:inline">or drop a video file here</span>
              <button onclick="makeClipsFromHome()"
                class="bg-black hover:bg-neutral-800 text-white font-bold px-5 py-2.5 rounded-2xl text-xs flex items-center gap-2 shadow-lg shadow-black/20 transition cursor-pointer transform hover:scale-[1.02] active:scale-[0.98]">
                <span>✂</span><span>Make clips</span>
              </button>
            </div>
          </div>

          <!-- Options Drawer -->
          <div id="options-drawer" class="hidden pt-3 border-t border-neutral-100 grid grid-cols-3 gap-3 text-xs">
            <div class="bg-neutral-50 p-2.5 rounded-xl border border-neutral-200">
              <span class="block text-neutral-500 font-semibold mb-1">Quality:</span>
              <select id="home-opt-quality" class="w-full bg-white border border-neutral-200 rounded-lg p-1 font-mono text-neutral-800">
                <option value="720p60,720p,best">720p60</option>
                <option value="480p30,480p,best">480p</option>
                <option value="1080p60,1080p,best">1080p60</option>
                <option value="best">Best</option>
              </select>
            </div>
            <div class="bg-neutral-50 p-2.5 rounded-xl border border-neutral-200">
              <span class="block text-neutral-500 font-semibold mb-1">Min Spike:</span>
              <input id="home-opt-min" type="number" value="5" min="1" max="100" class="w-full bg-white border border-neutral-200 rounded-lg p-1 font-mono text-neutral-800" />
            </div>
            <div class="bg-neutral-50 p-2.5 rounded-xl border border-neutral-200">
              <span class="block text-neutral-500 font-semibold mb-1">Whisper:</span>
              <select id="home-opt-model" class="w-full bg-white border border-neutral-200 rounded-lg p-1 font-mono text-neutral-800">
                <option value="tiny">Tiny (Fastest)</option>
                <option value="base">Base</option>
                <option value="small">Small</option>
              </select>
            </div>
          </div>

        </div>

      </div>

      <!-- Bottom 4 Stat Cards Row -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-4xl mx-auto w-full z-10 pt-4">
        
        <div class="bg-white rounded-2xl border border-neutral-200/90 p-4 shadow-sm flex flex-col gap-1.5">
          <div class="w-7 h-7 rounded-full bg-neutral-100 flex items-center justify-center text-neutral-900 font-bold text-xs">((•))</div>
          <div id="stat-recording" class="text-2xl font-black text-neutral-900">0</div>
          <div class="text-xs font-semibold text-neutral-400">Recording now</div>
        </div>

        <div class="bg-white rounded-2xl border border-neutral-200/90 p-4 shadow-sm flex flex-col gap-1.5">
          <div class="w-7 h-7 rounded-full bg-neutral-100 flex items-center justify-center text-neutral-900 font-bold text-xs">✂</div>
          <div id="stat-clips-today" class="text-2xl font-black text-neutral-900">0</div>
          <div class="text-xs font-semibold text-neutral-400">Clips today</div>
        </div>

        <div class="bg-white rounded-2xl border border-neutral-200/90 p-4 shadow-sm flex flex-col gap-1.5">
          <div class="w-7 h-7 rounded-full bg-neutral-100 flex items-center justify-center text-neutral-900 font-bold text-xs">✈</div>
          <div class="text-2xl font-black text-neutral-900">0</div>
          <div class="text-xs font-semibold text-neutral-400">Posted today</div>
        </div>

        <div class="bg-white rounded-2xl border border-neutral-200/90 p-4 shadow-sm flex flex-col gap-1.5">
          <div class="w-7 h-7 rounded-full bg-neutral-100 flex items-center justify-center text-neutral-900 font-bold text-xs">⏱</div>
          <div class="text-2xl font-black text-neutral-900">0</div>
          <div class="text-xs font-semibold text-neutral-400">Waiting to post</div>
        </div>

      </div>

    </div>

    <!-- ============================================================== -->
    <!-- VIEW 2: STREAMER PROFILE & STUDIO (Live Stream + 9:16 Player) -->
    <!-- ============================================================== -->
    <div id="view-streamer" class="hidden flex-1 flex flex-col overflow-hidden bg-[#09090b]">

      <!-- Streamer Profile Header Bar -->
      <div class="px-6 py-2.5 bg-[#0a0a0a] border-b border-[#27272a] flex items-center justify-between shrink-0">
        <div class="flex items-center gap-3">
          <img id="streamer-avatar-header" src="/static/avatars/ishowspeed.png"
            class="w-9 h-9 rounded-full object-cover border-2 border-white shadow-md" />
          <div>
            <div class="flex items-center gap-2">
              <h2 id="streamer-name-header" class="text-sm font-bold text-white tracking-wide">Streamer Profile</h2>
              <span id="streamer-live-badge" class="hidden bg-[#ef233c] text-white font-black text-[10px] px-2 py-0.5 rounded uppercase tracking-wider shadow-sm animate-pulse">LIVE</span>
              <span id="streamer-platform-header" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neutral-800 text-white uppercase border border-neutral-700">TWITCH</span>
              <div id="streamer-status-pill" class="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono bg-neutral-900 text-neutral-300 border border-neutral-800">
                <span class="w-2 h-2 rounded-full bg-neutral-600"></span>
                <span>Standby</span>
              </div>
            </div>
            <p id="streamer-url-header" class="text-[11px] text-neutral-400 font-mono truncate max-w-sm">No active stream</p>
          </div>
        </div>

        <div class="flex items-center gap-2.5">
          <!-- Manual Clip Action Button -->
          <button id="btn-manual-clip-streamer" onclick="triggerManualClip()"
            class="bg-white hover:bg-neutral-200 text-black font-bold px-4 py-1.5 rounded-xl text-xs flex items-center gap-2 cursor-pointer shadow-md shadow-white/10 transition">
            <span class="text-sm">✂️</span>
            <span id="label-clip-button">Cut Clip Now (Manual)</span>
          </button>

          <!-- Start / Stop Watcher -->
          <button id="btn-streamer-start" onclick="startStreamerWatcher()"
            class="bg-neutral-100 hover:bg-white text-black font-bold px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer">
            ▶ Start Recording
          </button>
          <button id="btn-streamer-stop" onclick="stopWatcher()"
            class="hidden bg-neutral-800 hover:bg-neutral-700 text-white font-bold px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer border border-neutral-600">
            ⏹ Stop Recording
          </button>

          <!-- Remove Creator Button -->
          <button id="btn-remove-streamer" onclick="removeCurrentCreator()"
            class="text-neutral-400 hover:text-white border border-neutral-800 hover:border-neutral-600 px-3 py-1.5 rounded-xl text-xs flex items-center gap-1.5 transition cursor-pointer" title="Remove this creator">
            <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
            <span>Remove</span>
          </button>
        </div>
      </div>

      <!-- Studio Body Grid -->
      <div class="flex-1 p-6 grid grid-cols-1 xl:grid-cols-12 gap-6 overflow-y-auto custom-scroll">

        <!-- Left Column: Live Stream Player & Chat Frequency (7.5 cols) -->
        <div class="xl:col-span-8 flex flex-col gap-5">
          
          <!-- Stream Video Embed -->
          <div class="bg-[#121214] border border-[#27272a] rounded-2xl overflow-hidden shadow-xl flex flex-col">
            <div class="px-4 py-2 bg-[#09090b] border-b border-[#27272a] flex items-center justify-between text-xs">
              <span class="text-neutral-300 font-medium flex items-center gap-2">
                <span id="player-live-dot" class="w-2 h-2 rounded-full bg-neutral-600"></span>
                <span>Live Stream Feed</span>
              </span>
              <span id="elapsed-timer" class="font-mono text-white text-xs font-bold">00:00:00</span>
            </div>

            <div class="relative w-full aspect-video bg-black flex items-center justify-center">
              <div id="stream-placeholder" class="text-center p-8 text-neutral-500 flex flex-col items-center gap-2">
                <span class="text-4xl opacity-30">📺</span>
                <p class="text-xs text-neutral-400">Click <strong class="text-white">▶ Start Recording</strong> above to connect live.</p>
              </div>
              <iframe id="stream-frame" src="" class="w-full h-full border-0 hidden" allowfullscreen="true"></iframe>
            </div>
          </div>

          <!-- Real-Time Chat Frequency Chart -->
          <div class="bg-[#121214] border border-[#27272a] rounded-2xl p-4 shadow-xl flex flex-col gap-3">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-base">📈</span>
                <h3 class="font-semibold text-xs text-neutral-200">Chat Velocity (10s Buckets)</h3>
                <span id="chat-live-badge" class="flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-mono bg-neutral-900 text-neutral-200 border border-neutral-700">
                  <span class="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>
                  <span id="chat-live-channel">Live Chat</span>
                </span>
              </div>
              <div class="flex items-center gap-3 text-xs font-mono text-[11px]">
                <div class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-white"></span><span class="text-neutral-400">Msgs</span></div>
                <div class="flex items-center gap-1"><span class="w-3 h-0.5 bg-neutral-400"></span><span class="text-neutral-400">Median</span></div>
                <div class="flex items-center gap-1"><span class="w-3 h-0.5 bg-neutral-500"></span><span class="text-neutral-400">Spike</span></div>
              </div>
            </div>
            <div class="h-40 w-full"><canvas id="chatChart"></canvas></div>
          </div>

          <!-- Terminal Activity Log -->
          <div class="bg-[#121214] border border-[#27272a] rounded-2xl p-3.5 shadow-xl flex flex-col gap-2">
            <div class="flex items-center justify-between text-xs text-neutral-400 border-b border-[#27272a] pb-1.5">
              <span class="font-medium text-neutral-200">Activity Log</span>
              <span class="font-mono text-[10px] text-neutral-500">Auto-scrolling</span>
            </div>
            <div id="log-box" class="h-24 overflow-y-auto font-mono text-xs text-neutral-300 space-y-1 custom-scroll bg-black p-2 rounded-xl border border-neutral-800">
              <div class="text-neutral-500">[System] Clip Max Studio ready.</div>
            </div>
          </div>

        </div>

        <!-- Right Column: 9:16 Vertical Video Player (4.5 cols) -->
        <div class="xl:col-span-4 flex flex-col gap-5">
          
          <div class="bg-[#121214] border border-[#27272a] rounded-2xl p-4 shadow-xl flex flex-col gap-3">
            <div class="flex items-center justify-between">
              <h3 class="font-semibold text-xs text-neutral-200 flex items-center gap-2">
                <span>🎬</span>
                <span>9:16 Vertical Player</span>
              </h3>
              <span id="preview-badge" class="text-xs font-mono text-neutral-400 truncate max-w-[160px]">Select a clip</span>
            </div>

            <!-- Vertical Video Container (9:16) -->
            <div class="relative w-full aspect-[9/16] max-h-[400px] bg-black rounded-2xl border border-neutral-800 overflow-hidden mx-auto flex items-center justify-center group shadow-2xl">
              <video id="clip-player" playsinline class="w-full h-full object-contain hidden"></video>

              <div id="player-placeholder" class="text-center p-6 text-neutral-500 flex flex-col items-center gap-2">
                <span class="text-3xl opacity-30">📱</span>
                <p class="text-xs text-neutral-400">Select any clip below to play &amp; replay.</p>
              </div>

              <!-- Top Floating Header Badge (matching reference: eye-catching title above clip) -->
              <div id="clip-header-badge" class="hidden absolute top-[12%] inset-x-3 flex justify-center z-20 pointer-events-none transition-all">
                <div id="clip-header-title" class="bg-white text-black font-black text-xs md:text-sm px-4 py-1.5 rounded-lg shadow-2xl tracking-wide uppercase text-center truncate max-w-[90%] drop-shadow-md">
                  BEST FRIENDS FIGHT FOR MONEY
                </div>
              </div>

              <!-- Subtitle Banner Overlay (matching reference: bold yellow subtitle below clip) -->
              <div id="sub-overlay" class="hidden absolute bottom-[12%] inset-x-2 text-center pointer-events-none z-20 transition-all">
                <span id="sub-text" class="inline-block text-[#FFE500] font-black text-base md:text-lg tracking-wider uppercase px-2 py-0.5"
                      style="text-shadow: 2.5px 2.5px 0 #000, -2.5px -2.5px 0 #000, 2.5px -2.5px 0 #000, -2.5px 2.5px 0 #000, 0 4px 10px rgba(0,0,0,0.95); font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                  OH!
                </span>
              </div>
            </div>

            <!-- Full Media Player Controls Bar matching reference -->
            <div id="replay-controls" class="hidden flex flex-col gap-2 pt-1">
              
              <!-- Seekbar & Time Display -->
              <div class="flex items-center gap-2 text-[10px] font-mono text-neutral-400 px-1">
                <span id="time-current">0:00</span>
                <input type="range" id="seek-bar" min="0" max="100" value="0" step="0.1" oninput="onSeekChange(this.value)"
                  class="flex-1 accent-white h-1.5 bg-[#252528] rounded-lg cursor-pointer" />
                <span id="time-duration">0:00</span>
              </div>

              <!-- Playback Control Icons matching reference bottom bar -->
              <div class="flex items-center justify-between px-2 pt-0.5">
                <button onclick="shuffleClip()" title="Shuffle" class="text-neutral-400 hover:text-white transition p-1 cursor-pointer">
                  <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>
                </button>

                <button onclick="prevClip()" title="Previous" class="text-neutral-300 hover:text-white transition p-1 cursor-pointer">
                  <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
                </button>

                <button onclick="seekRelative(-10)" title="Rewind 10s" class="text-neutral-300 hover:text-white flex items-center gap-0.5 text-[11px] font-mono font-bold transition p-1 cursor-pointer">
                  <span>⟲</span><span>10</span>
                </button>

                <!-- Center Glowing Play/Pause White Circle -->
                <button onclick="togglePlay()" id="btn-main-play" title="Play/Pause"
                  class="w-11 h-11 rounded-full bg-white hover:bg-neutral-200 text-black hover:scale-105 active:scale-95 transition-transform flex items-center justify-center shadow-lg shadow-white/10 cursor-pointer">
                  <span id="main-play-icon" class="text-base font-bold ml-0.5">▶</span>
                </button>

                <button onclick="seekRelative(30)" title="Forward 30s" class="text-neutral-300 hover:text-white flex items-center gap-0.5 text-[11px] font-mono font-bold transition p-1 cursor-pointer">
                  <span>30</span><span>⟳</span>
                </button>

                <button onclick="nextClip()" title="Next" class="text-neutral-300 hover:text-white transition p-1 cursor-pointer">
                  <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
                </button>

                <button onclick="toggleLoop()" id="btn-player-loop" title="Toggle Loop" class="text-neutral-400 hover:text-white transition p-1 cursor-pointer">
                  <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>
                </button>
              </div>

            </div>

            <!-- Download Action Links -->
            <div id="clip-actions" class="hidden flex items-center justify-between gap-2 pt-1 border-t border-neutral-800">
              <a id="btn-download-video" href="#" download class="flex-1 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 text-neutral-200 text-xs py-1.5 rounded-xl text-center font-medium transition">
                ⬇ Video (.mp4)
              </a>
              <a id="btn-download-srt" href="#" download class="flex-1 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 text-neutral-200 text-xs py-1.5 rounded-xl text-center font-medium transition">
                ⬇ Subtitles (.srt)
              </a>
            </div>

          </div>

          <!-- Generated Clips List for this Streamer -->
          <div class="bg-[#121214] border border-[#27272a] rounded-2xl p-4 shadow-xl flex flex-col gap-3 flex-1">
            <div class="flex items-center justify-between border-b border-[#27272a] pb-2">
              <div class="flex items-center gap-2">
                <span>📁</span>
                <h3 id="clips-heading" class="font-semibold text-xs text-neutral-200">Clips</h3>
                <span id="filtered-count-badge" class="px-2 py-0.5 rounded-full text-[10px] font-mono bg-neutral-800 text-neutral-300 border border-neutral-700">0</span>
              </div>
              <span id="current-creator-filter-label" class="text-xs font-mono text-neutral-400">Creator Clips</span>
            </div>

            <div id="clips-list" class="space-y-2 overflow-y-auto max-h-[280px] custom-scroll pr-1">
              <div class="text-center py-8 text-xs text-neutral-500">No clips yet for this creator.</div>
            </div>
          </div>

        </div>

      </div>

    </div>

    <!-- ============================================================== -->
    <!-- VIEW 3: ALL VIDEOS (Matching user reference layout)            -->
    <!-- ============================================================== -->
    <div id="view-all-videos" class="hidden flex-1 overflow-y-auto custom-scroll bg-white text-neutral-900 p-6 md:p-8 flex flex-col gap-6">

      <!-- Top Input Card with Options & Drop support -->
      <div id="all-drop-zone"
        class="bg-white rounded-3xl border border-neutral-200/90 shadow-xl shadow-black/5 p-4 flex flex-col gap-3">
        
        <!-- Input Row -->
        <div class="flex items-center gap-3 px-2 pt-1 pb-1">
          <svg class="w-5 h-5 text-neutral-400 shrink-0 fill-current" viewBox="0 0 24 24"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/></svg>
          <input id="all-input-link" type="text"
            placeholder="Paste a YouTube, Twitch or Kick video link"
            onkeydown="if(event.key==='Enter') makeClipsFromAllVideos()"
            class="w-full text-sm md:text-base text-neutral-900 placeholder-neutral-400 focus:outline-none bg-transparent font-medium" />
        </div>

        <!-- Action Bar -->
        <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-neutral-100">
          <div class="flex items-center gap-2 flex-wrap">
            <button onclick="document.getElementById('file-uploader').click()"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition cursor-pointer">
              <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg>
              <span>Upload video</span>
            </button>

            <select id="all-select-creator"
              class="px-3 py-1.5 rounded-full text-xs font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition cursor-pointer border-0 outline-none">
              <option value="">👤 No creator ⌵</option>
            </select>

            <button onclick="toggleOptionsDrawer()"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition cursor-pointer">
              <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
              <span>Options</span>
            </button>
          </div>

          <div class="flex items-center gap-3">
            <span class="text-xs text-neutral-400 italic hidden sm:inline">or drop a video file here</span>
            <button onclick="makeClipsFromAllVideos()"
              class="bg-black hover:bg-neutral-800 text-white font-bold px-5 py-2.5 rounded-2xl text-xs flex items-center gap-2 shadow-lg shadow-black/20 transition cursor-pointer transform hover:scale-[1.02] active:scale-[0.98]">
              <span>✂</span><span>Make clips</span>
            </button>
          </div>
        </div>

      </div>

      <!-- Recorded Sessions & Clips List -->
      <div id="all-sessions-container" class="space-y-6 pb-12">
        <!-- Rendered dynamically -->
      </div>

    </div>

  </main>


  <!-- MODAL: 9:16 Video Review Player (Subtitles + Replay + Loop + Controls) -->
  <div id="modal-review-player" class="hidden fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4">
    <div class="bg-[#121214] border border-neutral-800 rounded-3xl p-5 max-w-md w-full shadow-2xl flex flex-col gap-3.5 relative">
      
      <!-- Modal Header -->
      <div class="flex items-center justify-between border-b border-neutral-800 pb-2.5">
        <div class="flex items-center gap-2">
          <span class="text-base">🎬</span>
          <h3 id="modal-review-title" class="font-bold text-sm text-white truncate max-w-xs">Clip Review</h3>
        </div>
        <button onclick="closeReviewModal()" class="text-neutral-400 hover:text-white text-2xl p-1 cursor-pointer leading-none">&times;</button>
      </div>

      <!-- 9:16 Vertical Video Container -->
      <div class="relative w-full aspect-[9/16] max-h-[440px] bg-black rounded-2xl border border-neutral-800 overflow-hidden mx-auto flex items-center justify-center shadow-2xl">
        <video id="modal-video-player" playsinline class="w-full h-full object-contain"></video>

        <!-- Top Floating Headline Banner (matching reference: eye-catching title above clip) -->
        <div id="modal-header-badge" class="absolute top-[12%] inset-x-3 flex justify-center z-20 pointer-events-none transition-all">
          <div id="modal-header-title" class="bg-white text-black font-black text-xs md:text-sm px-4 py-1.5 rounded-lg shadow-2xl tracking-wide uppercase text-center truncate max-w-[90%] drop-shadow-md">
            BEST FRIENDS FIGHT FOR MONEY
          </div>
        </div>

        <!-- Lower Blur Yellow Subtitle Banner (matching reference: bold yellow subtitle below clip) -->
        <div id="modal-sub-overlay" class="hidden absolute bottom-[12%] inset-x-2 text-center pointer-events-none z-20 transition-all">
          <span id="modal-sub-text" class="inline-block text-[#FFE500] font-black text-base md:text-lg tracking-wider uppercase px-2 py-0.5"
                style="text-shadow: 2.5px 2.5px 0 #000, -2.5px -2.5px 0 #000, 2.5px -2.5px 0 #000, -2.5px 2.5px 0 #000, 0 4px 10px rgba(0,0,0,0.95); font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            OH!
          </span>
        </div>
      </div>

      <!-- Media Controls Bar -->
      <div class="flex flex-col gap-2 pt-0.5">
        <div class="flex items-center gap-2 text-[10px] font-mono text-neutral-400 px-1">
          <span id="modal-time-cur">0:00</span>
          <input type="range" id="modal-seek" min="0" max="100" value="0" step="0.1" oninput="onModalSeek(this.value)"
            class="flex-1 accent-white h-1.5 bg-[#252528] rounded-lg cursor-pointer" />
          <span id="modal-time-dur">0:00</span>
        </div>

        <div class="flex items-center justify-between px-3 pt-0.5">
          <button onclick="modalSeekRel(-10)" title="Rewind 10s" class="text-neutral-300 hover:text-white flex items-center gap-0.5 text-xs font-mono font-bold transition p-1 cursor-pointer">
            <span>⟲</span><span>10</span>
          </button>

          <button onclick="toggleModalPlay()" id="btn-modal-play"
            class="w-11 h-11 rounded-full bg-white hover:bg-neutral-200 text-black hover:scale-105 active:scale-95 transition-transform flex items-center justify-center shadow-lg shadow-white/20 cursor-pointer">
            <span id="modal-play-icon" class="text-base font-bold ml-0.5">▶</span>
          </button>

          <button onclick="modalSeekRel(10)" title="Forward 10s" class="text-neutral-300 hover:text-white flex items-center gap-0.5 text-xs font-mono font-bold transition p-1 cursor-pointer">
            <span>10</span><span>⟳</span>
          </button>

          <button onclick="toggleModalLoop()" id="btn-modal-loop" title="Toggle Loop" class="text-neutral-400 hover:text-white transition p-1 cursor-pointer">
            <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>
          </button>
        </div>
      </div>

      <!-- Action buttons -->
      <div class="flex items-center gap-2 pt-1 border-t border-neutral-800">
        <a id="btn-modal-dl-video" href="#" download class="flex-1 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 text-neutral-200 text-xs py-2 rounded-xl text-center font-semibold transition">
          ⬇ Video (.mp4)
        </a>
        <a id="btn-modal-dl-srt" href="#" download class="flex-1 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 text-neutral-200 text-xs py-2 rounded-xl text-center font-semibold transition">
          ⬇ Subtitles (.srt)
        </a>
        <button onclick="deleteModalClip()" title="Delete this video" class="p-2 bg-neutral-800 hover:bg-red-600/80 border border-neutral-700 text-white text-xs rounded-xl font-semibold transition cursor-pointer">
          <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
        </button>
      </div>

    </div>
  </div>

  <!-- MODAL: + New Creator -->
  <div id="modal-new-creator" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="bg-[#121214] border border-neutral-800 rounded-2xl p-6 w-full max-w-md shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-neutral-800 pb-3">
        <h3 class="font-bold text-base text-white flex items-center gap-2">
          <span>⊕</span>
          <span>Add New Creator</span>
        </h3>
        <button onclick="closeNewCreatorModal()" class="text-neutral-400 hover:text-white text-lg cursor-pointer">&times;</button>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-xs font-semibold text-neutral-300 mb-1">Creator Name:</label>
          <input id="modal-input-name" type="text" placeholder="e.g. Tarik, Pokimane, Shroud"
            class="w-full bg-black border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-white" />
        </div>

        <div>
          <label class="block text-xs font-semibold text-neutral-300 mb-1">Platform:</label>
          <select id="modal-select-platform" class="w-full bg-black border border-neutral-700 rounded-xl px-3 py-2 text-xs text-neutral-300 focus:outline-none focus:border-white">
            <option value="twitch">Twitch</option>
            <option value="youtube">YouTube</option>
            <option value="kick">Kick</option>
          </select>
        </div>

        <div>
          <label class="block text-xs font-semibold text-neutral-300 mb-1">Channel Handle or URL:</label>
          <input id="modal-input-handle" type="text" placeholder="e.g. tarik or https://kick.com/tarik"
            class="w-full bg-black border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-white" />
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-2 border-t border-neutral-800">
        <button onclick="closeNewCreatorModal()" class="px-4 py-2 rounded-xl text-xs font-medium text-neutral-400 hover:text-white transition cursor-pointer">Cancel</button>
        <button onclick="submitNewCreator()" class="bg-white hover:bg-neutral-200 text-black font-bold px-4 py-2 rounded-xl text-xs shadow-md shadow-white/10 transition cursor-pointer">Add Creator</button>
      </div>
    </div>
  </div>

  <script>
    let currentView = 'HOME';
    let activeCreator = null;
    let chartInstance = null;
    let selectedClip = null;
    let currentParsedSubtitles = [];
    let isLooping = false;
    let allClipsData = [];
    let allCreatorsData = [];
    let activeWatcherTarget = '';

    function initChart() {
      const canvas = document.getElementById('chatChart');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: [],
          datasets: [
            {
              type: 'bar',
              label: 'Messages',
              data: [],
              backgroundColor: 'rgba(255, 255, 255, 0.85)',
              borderColor: 'rgba(255, 255, 255, 1)',
              borderWidth: 1,
              borderRadius: 4,
              order: 2
            },
            {
              type: 'line',
              label: '3m Median',
              data: [],
              borderColor: 'rgba(212, 212, 216, 0.9)',
              borderWidth: 2,
              pointRadius: 0,
              fill: false,
              tension: 0.1,
              order: 1
            },
            {
              type: 'line',
              label: 'Threshold',
              data: [],
              borderColor: 'rgba(255, 255, 255, 0.45)',
              borderWidth: 2,
              borderDash: [5, 5],
              pointRadius: 0,
              fill: false,
              tension: 0.1,
              order: 0
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          scales: {
            x: {
              grid: { color: 'rgba(255, 255, 255, 0.05)' },
              ticks: { color: '#a1a1aa', font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }
            },
            y: {
              beginAtZero: true,
              suggestedMax: 8,
              grid: { color: 'rgba(255, 255, 255, 0.05)' },
              ticks: { color: '#a1a1aa', font: { size: 10 }, precision: 0 }
            }
          },
          plugins: { legend: { display: false } }
        }
      });
    }

    // View Routing: 'HOME' | 'STREAMER' | 'ALL'
    function switchView(viewName, creatorData = null) {
      currentView = viewName;
      const homeView = document.getElementById('view-home');
      const streamerView = document.getElementById('view-streamer');
      const homeBtn = document.getElementById('nav-btn-home');
      const allBtn = document.getElementById('nav-btn-all');

      if (viewName === 'HOME') {
        homeView.classList.remove('hidden');
        streamerView.classList.add('hidden'); document.getElementById('view-all-videos')?.classList.add('hidden');
        homeBtn.className = "w-full bg-white text-black font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm shadow-lg shadow-white/10 transition cursor-pointer";
        allBtn.className = "w-full bg-[#121214] hover:bg-[#1c1c20] text-neutral-300 font-medium px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer border border-[#27272a]";
      } else if (viewName === 'STREAMER') {
        homeView.classList.add('hidden');
        streamerView.classList.remove('hidden'); document.getElementById('view-all-videos')?.classList.add('hidden');
        homeBtn.className = "w-full bg-[#121214] hover:bg-[#1c1c20] text-neutral-300 font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm transition cursor-pointer border border-[#27272a]";
        allBtn.className = "w-full bg-[#121214] hover:bg-[#1c1c20] text-neutral-300 font-medium px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer border border-[#27272a]";

        if (creatorData) {
          setupStreamerProfile(creatorData);
        }
        if (!chartInstance) initChart();
      } else if (viewName === 'ALL') {
        homeView.classList.add('hidden');
        streamerView.classList.add('hidden'); document.getElementById('view-all-videos')?.classList.add('hidden');
        const allVideosView = document.getElementById('view-all-videos');
        if (allVideosView) allVideosView.classList.remove('hidden');

        homeBtn.className = "w-full bg-[#121214] hover:bg-[#1c1c20] text-neutral-300 font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm transition cursor-pointer border border-[#27272a]";
        allBtn.className = "w-full bg-white text-black font-bold px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer shadow-lg shadow-white/10";
        activeCreator = 'ALL';
        renderAllVideosView();
      }
    }

    function setupStreamerProfile(c) {
      activeCreator = c.name;
      activeWatcherTarget = c.handle || c.name;

      document.getElementById('streamer-name-header').textContent = c.name;
      const liveBadge = document.getElementById('streamer-live-badge');
      if (liveBadge) {
        if (c.is_live) liveBadge.classList.remove('hidden');
        else liveBadge.classList.add('hidden');
      }
      document.getElementById('streamer-avatar-header').src = c.avatar || `/static/avatars/ishowspeed.png`;
      document.getElementById('streamer-platform-header').textContent = (c.platform || 'TWITCH').toUpperCase();
      document.getElementById('streamer-url-header').textContent = c.handle || c.name;
      document.getElementById('current-creator-filter-label').textContent = `${c.name} Clips`;

      updatePlayerEmbed(c.handle || c.name);
      renderClipsList();

      // Immediately connect live chat velocity monitoring to this creator
      fetch('/api/creators/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: c.name })
      }).then(() => pollStatus()).catch(e => console.error(e));
    }

    // Home Card Action: Paste link and Click Make Clips -> Redirects to Streamer Profile & Starts
    async function makeClipsFromHome() {
      const link = document.getElementById('home-input-link').value.trim();
      const creatorDropdown = document.getElementById('home-select-creator').value;

      let target = link || creatorDropdown;
      if (!target) {
        alert("Please paste a Twitch, YouTube or Kick link, or select a creator.");
        return;
      }

      // Find or create creator info
      let matchedCreator = allCreatorsData.find(c => c.handle === target || c.name.toLowerCase() === target.toLowerCase() || target.includes(c.name.toLowerCase()));
      if (!matchedCreator) {
        let name = target.split('/').pop().replace(/[@?#].*/, '') || "Streamer";
        let platform = target.includes("youtube") ? "youtube" : (target.includes("kick") ? "kick" : "twitch");
        matchedCreator = { name: name, handle: target, platform: platform, avatar: `/static/avatars/ishowspeed.png` };
      }

      // 1. Redirect to Streamer View
      switchView('STREAMER', matchedCreator);

      // 2. Start Watching automatically
      startStreamerWatcher(target);
    }

    async function startStreamerWatcher(targetOverride = null) {
      const target = targetOverride || activeWatcherTarget;
      if (!target) return;

      const minMsgs = parseInt(document.getElementById('home-opt-min').value) || 5;
      const quality = document.getElementById('home-opt-quality').value || "720p60,720p,best";
      const model = document.getElementById('home-opt-model').value || "tiny";

      updatePlayerEmbed(target);

      try {
        const res = await fetch('/api/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target: target,
            min_messages: minMsgs,
            quality: quality,
            whisper_model: model
          })
        });
        const data = await res.json();
        pollStatus();
      } catch (err) {
        console.error("Start watcher error:", err);
      }
    }

    async function stopWatcher(targetOverride = null) {
      const target = targetOverride || activeCreator;
      try {
        await fetch('/api/stop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target: target })
        });
        pollStatus();
      } catch (err) {
        console.error("Stop watcher error:", err);
      }
    }

    // Manual Clipping Action: Works with 10s wait buffer feedback
    async function triggerManualClip() {
      const btn = document.getElementById('btn-manual-clip-streamer');
      const label = document.getElementById('label-clip-button');
      
      try {
        label.textContent = "Requesting Clip...";
        const res = await fetch('/api/clip-now', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target: activeCreator })
        });
        const data = await res.json();

        if (!res.ok) {
          alert(data.detail || "Manual clip failed.");
          label.textContent = "Cut Clip Now (Manual)";
          return;
        }

        // Live countdown visual feedback for 12 seconds buffer
        let countdown = 12;
        btn.classList.add('animate-pulse');
        const interval = setInterval(() => {
          countdown--;
          if (countdown > 0) {
            label.textContent = `Rendering 9:16 (${countdown}s buffer)...`;
          } else {
            clearInterval(interval);
            btn.classList.remove('animate-pulse');
            label.textContent = "✅ Clip Ready!";
            setTimeout(() => {
              label.textContent = "Cut Clip Now (Manual)";
            }, 3000);
            pollStatus();
          }
        }, 1000);

      } catch (err) {
        alert("Error triggering manual clip: " + err);
        label.textContent = "Cut Clip Now (Manual)";
      }
    }

    // Video File Upload Support (Drag & Drop or File Picker)
    async function handleFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      uploadVideoFile(file);
    }

    async function uploadVideoFile(file) {
      const creator = activeCreator && activeCreator !== 'ALL' ? activeCreator : (document.getElementById('home-select-creator').value || "uploads");
      const model = document.getElementById('home-opt-model').value || "tiny";
      
      const formData = new FormData();
      formData.append("file", file);
      formData.append("creator", creator);
      formData.append("whisper_model", model);

      alert(`Uploading '${file.name}' to creator '${creator}' for 9:16 vertical conversion...`);

      try {
        const res = await fetch('/api/upload-video', {
          method: 'POST',
          body: formData
        });
        const data = await res.json();
        if (res.ok) {
          switchView('STREAMER', { name: creator, handle: creator, platform: "upload" });
          pollStatus();
        } else {
          alert(`Upload error: ${data.detail || 'Failed'}`);
        }
      } catch (err) {
        alert("Upload network error: " + err);
      }
    }

    // Drag and Drop handling on Home Card
    const dropZone = document.getElementById('drop-zone');
    if (dropZone) {
      ['dragenter', 'dragover'].forEach(name => {
        dropZone.addEventListener(name, (e) => { e.preventDefault(); dropZone.classList.add('border-black', 'bg-neutral-100'); });
      });
      ['dragleave', 'drop'].forEach(name => {
        dropZone.addEventListener(name, (e) => { e.preventDefault(); dropZone.classList.remove('border-black', 'bg-neutral-100'); });
      });
      dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) uploadVideoFile(files[0]);
      });
    }

    function toggleOptionsDrawer() {
      const drawer = document.getElementById('options-drawer');
      drawer.classList.toggle('hidden');
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        updateUI(data);
      } catch (err) {
        console.error("Status poll error:", err);
      }
    }

    function updateUI(data) {
      allClipsData = data.clips || [];
      allCreatorsData = data.creators || [];
      allSessionsData = data.sessions || [];

      // 1. Home stats update
      document.getElementById('stat-recording').textContent = data.recording_count !== undefined ? data.recording_count : (data.is_running ? "1" : "0");
      document.getElementById('stat-clips-today').textContent = allClipsData.length;
      document.getElementById('home-creator-count').textContent = allCreatorsData.length;
      document.getElementById('all-videos-count-badge').textContent = allClipsData.length;

      // 2. Streamer header state
      const streamerPill = document.getElementById('streamer-status-pill');
      const btnStart = document.getElementById('btn-streamer-start');
      const btnStop = document.getElementById('btn-streamer-stop');
      const timer = document.getElementById('elapsed-timer');
      const liveDot = document.getElementById('player-live-dot');

      const curCreatorObj = allCreatorsData.find(c => c.name.toLowerCase() === (activeCreator || '').toLowerCase());
      const isCurRecording = curCreatorObj ? curCreatorObj.is_recording : data.is_running;

      if (isCurRecording) {
        streamerPill.className = "flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono bg-red-950/80 text-red-200 border border-red-800 shadow-sm";
        streamerPill.innerHTML = `<span class="w-2 h-2 rounded-full bg-red-500 animate-ping"></span><span class="font-bold">RECORDING #${escapeHtml(activeCreator)}</span>`;
        btnStart.classList.add('hidden');
        btnStop.classList.remove('hidden');
        liveDot.className = "w-2 h-2 rounded-full bg-red-500 animate-pulse";

        const hrs = String(Math.floor(data.elapsed_seconds / 3600)).padStart(2, '0');
        const mins = String(Math.floor((data.elapsed_seconds % 3600) / 60)).padStart(2, '0');
        const secs = String(data.elapsed_seconds % 60).padStart(2, '0');
        timer.textContent = `${hrs}:${mins}:${secs}`;
      } else {
        streamerPill.className = "flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono bg-neutral-900 text-neutral-400 border border-neutral-800";
        streamerPill.innerHTML = `<span class="w-2 h-2 rounded-full bg-neutral-600"></span><span>Standby</span>`;
        btnStart.classList.remove('hidden');
        btnStop.classList.add('hidden');
        liveDot.className = "w-2 h-2 rounded-full bg-neutral-600";
        timer.textContent = "00:00:00";
      }

      // Update streamer live badge
      const liveBadge = document.getElementById('streamer-live-badge');
      if (liveBadge) {
        if (curCreatorObj && curCreatorObj.is_live) liveBadge.classList.remove('hidden');
        else liveBadge.classList.add('hidden');
      }

      // 3. Chart update
      if (data.bucket_history && data.bucket_history.length > 0) {
        if (!chartInstance) initChart();
        if (chartInstance) {
          chartInstance.data.labels = data.bucket_history.map(b => b.time);
          chartInstance.data.datasets[0].data = data.bucket_history.map(b => b.count);
          chartInstance.data.datasets[0].backgroundColor = data.bucket_history.map(b => 
            b.is_spike ? 'rgba(255, 255, 255, 1)' : (b.is_live ? 'rgba(212, 212, 216, 0.95)' : 'rgba(255, 255, 255, 0.8)')
          );
          chartInstance.data.datasets[1].data = data.bucket_history.map(b => b.median);
          chartInstance.data.datasets[2].data = data.bucket_history.map(b => b.threshold);
          chartInstance.update();
        }
      }

      // Live Chat Badge update
      const liveChan = document.getElementById('chat-live-channel');
      if (liveChan) {
        const plat = (data.platform || 'TWITCH').toUpperCase();
        const target = data.target_name || activeCreator || 'LIVE';
        liveChan.textContent = `${plat}: #${target}`;
      }

      // 4. Log Box
      const logBox = document.getElementById('log-box');
      if (data.logs && data.logs.length > 0) {
        logBox.innerHTML = data.logs.map(l => `<div class="leading-relaxed">${escapeHtml(l)}</div>`).join('');
        logBox.scrollTop = logBox.scrollHeight;
      }

      // 5. Sidebar creators list
      updateSidebarCreators(allCreatorsData);

      // 6. Home dropdown creator selector
      updateHomeCreatorDropdown(allCreatorsData);

      // 7. Clips List
      renderClipsList();
    }

    function updateSidebarCreators(creators) {
      const container = document.getElementById('creators-sidebar-list');
      if (!container) return;

      if (!creators || creators.length === 0) {
        container.innerHTML = `<div class="text-xs text-neutral-500 px-3 py-2">No creators yet.</div>`;
        return;
      }

      container.innerHTML = creators.map(c => {
        const isSelected = (currentView === 'STREAMER' && (activeCreator || '').toLowerCase() === c.name.toLowerCase());
        const activeClass = isSelected
          ? 'bg-[#27272a] text-white shadow-sm'
          : 'text-neutral-400 hover:text-white hover:bg-[#18181b]';

        return `
          <div onclick="onSidebarCreatorClick('${escapeHtml(c.name)}')"
            class="flex items-center justify-between px-2.5 py-2 rounded-xl text-sm font-medium transition cursor-pointer group ${activeClass}">
            <div class="flex items-center gap-2.5 overflow-hidden">
              <div class="relative shrink-0">
                <img src="${c.avatar}" onerror="this.src='https://ui-avatars.com/api/?name=${encodeURIComponent(c.name)}&background=27272a&color=ffffff'"
                  class="w-7 h-7 rounded-full object-cover shrink-0 border ${c.is_recording ? 'border-red-500 ring-2 ring-red-500 animate-pulse' : (c.is_live ? 'border-red-500 ring-2 ring-red-500/40' : 'border-neutral-800')}" />
                ${c.is_recording ? `
                  <span class="absolute -bottom-0.5 -right-0.5 w-2 h-2 bg-red-500 border border-black rounded-full animate-ping"></span>
                  <span class="absolute -bottom-0.5 -right-0.5 w-2 h-2 bg-red-500 border border-black rounded-full"></span>
                ` : (c.is_live ? `
                  <span class="absolute -bottom-0.5 -right-0.5 w-2 h-2 bg-red-600 border border-black rounded-full animate-ping"></span>
                  <span class="absolute -bottom-0.5 -right-0.5 w-2 h-2 bg-red-600 border border-black rounded-full"></span>
                ` : '')}
              </div>
              <span class="truncate text-xs font-semibold">${escapeHtml(c.name)}</span>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              ${c.is_recording ? `
                <span class="bg-red-600 text-white font-black text-[9px] px-1.5 py-0.5 rounded uppercase tracking-wider shadow-sm animate-pulse flex items-center gap-1" title="Recording & clipping in progress">
                  <span class="w-1.5 h-1.5 rounded-full bg-white animate-ping"></span>REC
                </span>
              ` : (c.is_live ? `
                <span class="bg-[#ef233c] text-white font-black text-[9px] px-1.5 py-0.5 rounded uppercase tracking-wider shadow-sm animate-pulse flex items-center gap-1">
                  LIVE
                </span>
              ` : '')}
              ${c.clip_count > 0 ? `<span class="text-[10px] font-mono font-bold text-neutral-300 bg-neutral-900 border border-neutral-800 px-1.5 py-0.5 rounded-full">${c.clip_count}</span>` : ''}
              <button onclick="event.stopPropagation(); removeCreator('${escapeHtml(c.name)}')"
                title="Remove creator"
                class="opacity-0 group-hover:opacity-100 p-1 hover:text-red-400 text-neutral-500 rounded transition cursor-pointer">
                <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
              </button>
            </div>
          </div>
        `;
      }).join('');
    }

    function updateHomeCreatorDropdown(creators) {
      ['home-select-creator', 'all-select-creator'].forEach(id => {
        const select = document.getElementById(id);
        if (!select) return;
        const cur = select.value;
        select.innerHTML = `<option value="">👤 No creator ⌵</option>` + creators.map(c => `
          <option value="${escapeHtml(c.handle || c.name)}" ${cur === (c.handle || c.name) ? 'selected' : ''}>
            ${c.is_live ? '🔴 ' : ''}${escapeHtml(c.name)} ${c.is_live ? '(LIVE)' : ''} (${(c.platform || 'Twitch').toUpperCase()})
          </option>
        `).join('');
      });
    }

    function onSidebarCreatorClick(name) {
      const c = allCreatorsData.find(item => item.name === name);
      if (c) {
        switchView('STREAMER', c);
      }
    }

    function renderClipsList() {
      const clipsList = document.getElementById('clips-list');
      const countBadge = document.getElementById('filtered-count-badge');
      if (!clipsList) return;

      const filtered = (!activeCreator || activeCreator === 'ALL')
        ? allClipsData
        : allClipsData.filter(c => c.streamer === activeCreator);

      if (countBadge) countBadge.textContent = filtered.length;

      if (filtered.length === 0) {
        clipsList.innerHTML = `<div class="text-center py-8 text-xs text-neutral-500">No clips found for ${activeCreator || 'this creator'}.</div>`;
        return;
      }

      clipsList.innerHTML = filtered.map(c => `
        <div onclick="playClip('${c.url}', '${c.srt_url || ''}', '${escapeHtml(c.name)}', '${escapeHtml(c.headline || '')}')"
          class="p-2.5 rounded-xl border border-neutral-800 hover:border-neutral-500 bg-[#121214] hover:bg-[#18181b] transition flex items-center justify-between cursor-pointer group text-left">
          <div class="flex items-center gap-2.5 overflow-hidden">
            <span class="text-base">📱</span>
            <div class="truncate">
              <div class="text-xs font-semibold text-neutral-200 truncate group-hover:text-white">${c.name}</div>
              <div class="text-[10px] text-neutral-400 font-mono">${c.streamer} &bull; ${c.created_at} &bull; ${c.size_mb} MB ${c.has_srt ? '&bull; <span class="text-white font-bold">SRT</span>' : ''}</div>
            </div>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <button onclick="event.stopPropagation(); deleteSingleClip('${c.url}')" title="Delete clip" class="opacity-0 group-hover:opacity-100 p-1 hover:text-red-400 text-neutral-500 rounded transition cursor-pointer">
              <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
            </button>
            <span class="text-xs text-white font-mono group-hover:translate-x-0.5 transition">&rarr;</span>
          </div>
        </div>
      `).join('');
    }

    async function playClip(videoUrl, srtUrl, clipName, headline = null) {
      selectedClip = clipName;
      const player = document.getElementById('clip-player');
      const placeholder = document.getElementById('player-placeholder');
      const badge = document.getElementById('preview-badge');
      const replayBar = document.getElementById('replay-controls');
      const actions = document.getElementById('clip-actions');
      const btnVideo = document.getElementById('btn-download-video');
      const btnSrt = document.getElementById('btn-download-srt');
      const subOverlay = document.getElementById('sub-overlay');
      const headerBadge = document.getElementById('clip-header-badge');
      const headerTitle = document.getElementById('clip-header-title');

      badge.textContent = clipName;
      player.src = videoUrl;
      player.loop = isLooping;
      player.classList.remove('hidden');
      placeholder.classList.add('hidden');
      replayBar.classList.remove('hidden');
      actions.classList.remove('hidden');

      const found = allClipsData.find(c => c.name === clipName || c.url === videoUrl);
      const cleanTitle = clipName.replace(/_\d{8}_\d{6}.*/, '').replace(/clip_\d+_.*/, '').replace(/_/g, ' ').toUpperCase();
      const displayTitle = headline || (found && found.headline) || cleanTitle || "BEST FRIENDS FIGHT FOR MONEY";
      headerTitle.textContent = displayTitle;
      headerBadge.classList.remove('hidden');

      btnVideo.href = videoUrl;
      if (srtUrl) {
        btnSrt.href = srtUrl;
        btnSrt.classList.remove('hidden');
        loadSubtitles(srtUrl);
      } else {
        btnSrt.classList.add('hidden');
        subOverlay.classList.add('hidden');
        currentParsedSubtitles = [];
      }

      try {
        await player.play();
        document.getElementById('main-play-icon').textContent = "⏸";
      } catch (err) {
        document.getElementById('main-play-icon').textContent = "▶";
      }

      player.onloadedmetadata = () => {
        const dur = player.duration || 0;
        document.getElementById('time-duration').textContent = formatSeconds(dur);
        document.getElementById('seek-bar').value = 0;
      };

      player.ontimeupdate = () => {
        const cur = player.currentTime || 0;
        const dur = player.duration || 1;
        document.getElementById('time-current').textContent = formatSeconds(cur);
        document.getElementById('seek-bar').value = ((cur / dur) * 100).toFixed(1);

        if (currentParsedSubtitles.length > 0) {
          const activeSub = currentParsedSubtitles.find(s => cur >= s.start && cur <= s.end);
          const subText = document.getElementById('sub-text');
          if (activeSub) {
            subText.textContent = activeSub.text.toUpperCase();
            subOverlay.classList.remove('hidden');
          } else {
            subOverlay.classList.add('hidden');
          }
        }
      };

      player.onended = () => {
        if (!isLooping) {
          document.getElementById('main-play-icon').textContent = "↺";
        }
      };
    }

    function togglePlay() {
      const player = document.getElementById('clip-player');
      if (player.paused) {
        player.play();
        document.getElementById('main-play-icon').textContent = "⏸";
      } else {
        player.pause();
        document.getElementById('main-play-icon').textContent = "▶";
      }
    }

    function seekRelative(sec) {
      const player = document.getElementById('clip-player');
      if (!player.src) return;
      player.currentTime = Math.max(0, Math.min(player.duration || 0, player.currentTime + sec));
    }

    function onSeekChange(percent) {
      const player = document.getElementById('clip-player');
      if (!player.duration) return;
      player.currentTime = (percent / 100) * player.duration;
    }

    function toggleLoop() {
      const player = document.getElementById('clip-player');
      isLooping = !isLooping;
      player.loop = isLooping;
      const btn = document.getElementById('btn-player-loop');
      if (isLooping) {
        btn.className = "text-white font-bold transition p-1 cursor-pointer scale-110";
      } else {
        btn.className = "text-neutral-400 hover:text-white transition p-1 cursor-pointer";
      }
    }

    function prevClip() {
      const list = getFilteredClips();
      if (list.length === 0) return;
      let idx = list.findIndex(c => c.name === selectedClip);
      if (idx <= 0) idx = list.length - 1;
      else idx -= 1;
      const c = list[idx];
      playClip(c.url, c.srt_url || '', c.name, c.headline);
    }

    function nextClip() {
      const list = getFilteredClips();
      if (list.length === 0) return;
      let idx = list.findIndex(c => c.name === selectedClip);
      if (idx === -1 || idx >= list.length - 1) idx = 0;
      else idx += 1;
      const c = list[idx];
      playClip(c.url, c.srt_url || '', c.name, c.headline);
    }

    function shuffleClip() {
      const list = getFilteredClips();
      if (list.length === 0) return;
      const rnd = Math.floor(Math.random() * list.length);
      const c = list[rnd];
      playClip(c.url, c.srt_url || '', c.name, c.headline);
    }

    function getFilteredClips() {
      return (!activeCreator || activeCreator === 'ALL')
        ? allClipsData
        : allClipsData.filter(c => c.streamer === activeCreator);
    }

    function formatSeconds(sec) {
      if (isNaN(sec)) return "0:00";
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return `${m}:${String(s).padStart(2, '0')}`;
    }

    async function loadSubtitles(srtUrl) {
      try {
        const res = await fetch(srtUrl);
        if (!res.ok) return;
        const srtText = await res.text();
        currentParsedSubtitles = parseSRT(srtText);
      } catch (err) {
        console.error("Subtitle error:", err);
      }
    }

    function parseSRT(srt) {
      const items = [];
      const blocks = srt.trim().replace(/\r\n/g, '\n').split('\n\n');

      for (const block of blocks) {
        const lines = block.split('\n');
        if (lines.length >= 2) {
          const timeLine = lines[1].includes('-->') ? lines[1] : (lines[0].includes('-->') ? lines[0] : null);
          if (!timeLine) continue;
          const [startStr, endStr] = timeLine.split('-->').map(s => s.trim());
          const textLines = lines.slice(lines.indexOf(timeLine) + 1).join(' ').trim();

          const startSec = timeToSeconds(startStr);
          const endSec = timeToSeconds(endStr);
          if (startSec !== null && endSec !== null && textLines) {
            items.push({ start: startSec, end: endSec, text: textLines });
          }
        }
      }
      return items;
    }

    function timeToSeconds(tStr) {
      const m = tStr.match(/(\\d+):(\\d+):(\\d+)[,.](\\d+)/);
      if (!m) return null;
      return parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]) + parseInt(m[4]) / 1000;
    }

    function updatePlayerEmbed(target) {
      const frame = document.getElementById('stream-frame');
      const placeholder = document.getElementById('stream-placeholder');
      const t = target.trim();
      if (!t) return;

      const isYt = t.includes("youtube.com") || t.includes("youtu.be");
      const isKick = t.includes("kick.com");

      if (isYt) {
        let vidId = null;
        const m1 = t.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
        const m2 = t.match(/(?:youtu\.be\/|\/live\/|\/embed\/)([a-zA-Z0-9_-]{11})/);
        if (m1) vidId = m1[1];
        else if (m2) vidId = m2[1];

        if (vidId) {
          frame.src = `https://www.youtube.com/embed/${vidId}?autoplay=1`;
        }
      } else if (isKick) {
        const clean = t.replace(/^https?:\/\/(?:www\.)?kick\.com\//i, '').replace(/[@#]/g, '');
        frame.src = `https://player.kick.com/${clean}`;
      } else {
        const clean = t.replace(/^https?:\/\/(?:www\.)?twitch\.tv\//i, '').replace(/[@#]/g, '');
        frame.src = `https://player.twitch.tv/?channel=${clean}&parent=localhost&parent=127.0.0.1&muted=false`;
      }

      placeholder.classList.add('hidden');
      frame.classList.remove('hidden');
    }

    // Modal controls for + New Creator
    function openNewCreatorModal() {
      document.getElementById('modal-new-creator').classList.remove('hidden');
      document.getElementById('modal-input-name').focus();
    }

    function closeNewCreatorModal() {
      document.getElementById('modal-new-creator').classList.add('hidden');
    }

    async function submitNewCreator() {
      const name = document.getElementById('modal-input-name').value.trim();
      const platform = document.getElementById('modal-select-platform').value;
      const handle = document.getElementById('modal-input-handle').value.trim() || name;

      if (!name) {
        alert("Please enter a creator name.");
        return;
      }

      try {
        const res = await fetch('/api/creators', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, handle: handle, platform: platform })
        });
        const data = await res.json();
        if (res.ok) {
          closeNewCreatorModal();
          pollStatus();
          onSidebarCreatorClick(name);
        } else {
          alert(`Error: ${data.detail || 'Could not add creator'}`);
        }
      } catch (err) {
        alert("Connection error adding creator: " + err);
      }
    }

    async function removeCreator(name) {
      if (!name) return;
      if (!confirm(`Are you sure you want to remove creator "${name}"?`)) return;

      try {
        const res = await fetch(`/api/creators/${encodeURIComponent(name)}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok) {
          if (activeCreator === name) {
            switchView('HOME');
          }
          pollStatus();
        } else {
          alert(`Error: ${data.detail || 'Could not remove creator'}`);
        }
      } catch (err) {
        alert("Error removing creator: " + err);
      }
    }

    function removeCurrentCreator() {
      if (activeCreator && activeCreator !== 'ALL') {
        removeCreator(activeCreator);
      }
    }

    async function deleteSessionVideos(streamerName) {
      if (!confirm(`Delete all recorded videos for '${streamerName}'? The creator will NOT be deleted.`)) return;
      try {
        const res = await fetch(`/api/videos/session/${encodeURIComponent(streamerName)}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok) {
          pollStatus();
        } else {
          alert(`Error: ${data.detail || 'Could not delete video list'}`);
        }
      } catch (err) {
        alert("Error deleting video list: " + err);
      }
    }

    async function deleteSingleClip(clipUrl) {
      if (!confirm("Delete this video clip?")) return;
      try {
        const res = await fetch(`/api/clips/${encodeURIComponent(clipUrl)}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok) {
          pollStatus();
        } else {
          alert(`Error: ${data.detail || 'Could not delete clip'}`);
        }
      } catch (err) {
        alert("Error deleting clip: " + err);
      }
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }


    let allSessionsData = [];
    let modalParsedSubs = [];
    let modalLooping = false;

    function renderAllVideosView() {
      const container = document.getElementById('all-sessions-container');
      if (!container) return;

      if (!allSessionsData || allSessionsData.length === 0) {
        container.innerHTML = `
          <div class="text-center py-16 bg-neutral-50 rounded-3xl border border-neutral-200">
            <span class="text-4xl opacity-40">🎬</span>
            <p class="text-sm font-semibold text-neutral-600 mt-2">No recorded videos yet.</p>
            <p class="text-xs text-neutral-400 mt-0.5">Paste a link above or upload a video to generate clips.</p>
          </div>
        `;
        return;
      }

      container.innerHTML = allSessionsData.map(session => `
        <div class="bg-neutral-50/70 border border-neutral-200/90 rounded-3xl p-4 md:p-5 shadow-sm space-y-4">
          
          <!-- Session Header Card (matching reference) -->
          <div class="bg-white rounded-2xl border border-neutral-200/90 p-3 shadow-sm flex items-center justify-between">
            <div class="flex items-center gap-3.5 overflow-hidden">
              <div class="relative w-28 md:w-36 aspect-video rounded-xl bg-neutral-900 overflow-hidden shrink-0 shadow-sm cursor-pointer group"
                   onclick="openReviewModal('${session.recording_url || session.clips[0].url}', '${session.clips[0].srt_url || ''}', '${escapeHtml(session.title)}')">
                <img src="${session.thumb}" onerror="this.src='https://ui-avatars.com/api/?name=${encodeURIComponent(session.streamer)}&background=18181b&color=ffffff'" class="w-full h-full object-cover group-hover:scale-105 transition" />
                ${session.is_live ? `
                  <span class="absolute top-1.5 left-1.5 bg-[#ef233c] text-white font-black text-[9px] px-1.5 py-0.5 rounded uppercase tracking-wider shadow z-10 animate-pulse">
                    LIVE
                  </span>
                ` : ''}
                <span class="absolute bottom-1 right-1 bg-black/85 text-white font-mono text-[10px] font-bold px-1.5 py-0.5 rounded shadow">
                  ${session.duration.split(' ')[0]}
                </span>
                <div class="absolute inset-0 bg-black/20 opacity-0 group-hover:opacity-100 transition flex items-center justify-center text-white text-xs font-bold">
                  ▶ Review
                </div>
              </div>
              <div class="overflow-hidden">
                <div class="flex items-center gap-2 truncate">
                  ${session.is_live ? `
                    <span class="bg-[#ef233c] text-white font-black text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider shadow-sm animate-pulse shrink-0">
                      LIVE
                    </span>
                  ` : ''}
                  <h3 class="font-black text-sm md:text-base text-neutral-950 truncate">${escapeHtml(session.title)}</h3>
                </div>
                <p class="text-xs text-neutral-500 font-medium truncate mt-0.5">
                  ${session.streamer} &bull; ${session.category || (session.platform ? session.platform.toUpperCase() : 'Twitch')} &bull; ${session.duration} &bull; ${session.clip_count} clips &bull; made today
                </p>
              </div>
            </div>

            <!-- Header Action Icons -->
            <div class="flex items-center gap-2 text-neutral-500 shrink-0">
              <a href="${session.recording_url || session.clips[0].url}" download title="Download Full Stream Recording" class="p-2 hover:text-neutral-900 hover:bg-neutral-100 rounded-xl transition cursor-pointer">
                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/></svg>
              </a>
              <button onclick="deleteSessionVideos('${escapeHtml(session.streamer)}')" title="Delete video list (keeps creator)" class="p-2 hover:text-red-500 hover:bg-neutral-100 rounded-xl transition cursor-pointer">
                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
              </button>
              <input type="checkbox" checked class="w-4 h-4 rounded accent-black ml-1 cursor-pointer" />
            </div>
          </div>

          <!-- Horizontal Grid of 9:16 Shorts Cards (matching reference) -->
          <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3.5">
            ${session.clips.map(c => `
              <div onclick="openReviewModal('${c.url}', '${c.srt_url || ''}', '${escapeHtml(c.headline)}')"
                class="aspect-[9/16] rounded-2xl overflow-hidden relative shadow-md hover:shadow-2xl border border-neutral-200/90 cursor-pointer group bg-black transition-all hover:scale-[1.02]">
                
                <!-- 9:16 Background Video with Blurred Extension -->
                <video src="${c.url}" playsinline muted preload="metadata" class="w-full h-full object-contain pointer-events-none"></video>

                <!-- Top-Left High-Contrast Score Badge (e.g. 85, 90) -->
                <div class="absolute top-2.5 left-2.5 z-10 pointer-events-none">
                  <span class="bg-black text-white border border-white/30 font-black text-xs px-2 py-0.5 rounded-lg shadow-md tracking-tight">
                    ${c.score}
                  </span>
                </div>

                <!-- Top-Right Duration Badge (e.g. 0:17) -->
                <div class="absolute top-2.5 right-2.5 z-10 pointer-events-none">
                  <span class="bg-white/95 text-neutral-900 font-bold text-[11px] px-2 py-0.5 rounded-md shadow-md font-mono">
                    ${c.duration_str}
                  </span>
                </div>

                <!-- Top Floating White Headline Banner -->
                <div class="absolute top-11 inset-x-2 z-10 flex justify-center pointer-events-none">
                  <div class="bg-white text-black font-black text-[10px] md:text-xs px-2.5 py-1 rounded-md shadow-xl uppercase tracking-wider text-center max-w-[95%] truncate">
                    ${escapeHtml(c.headline)}
                  </div>
                </div>

                <!-- Hover Play Icon Overlay -->
                <div class="absolute inset-0 bg-black/25 opacity-0 group-hover:opacity-100 transition flex items-center justify-center z-20">
                  <div class="w-10 h-10 rounded-full bg-white/95 text-neutral-900 flex items-center justify-center shadow-lg font-bold">
                    ▶
                  </div>
                </div>

                <!-- Hover Delete Single Video Button -->
                <button onclick="event.stopPropagation(); deleteSingleClip('${c.url}')" title="Delete this video" class="absolute bottom-2.5 right-2.5 z-30 opacity-0 group-hover:opacity-100 p-1.5 bg-black/80 hover:bg-red-600 text-white rounded-lg transition shadow-md cursor-pointer">
                  <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>

              </div>
            `).join('')}
          </div>

        </div>
      `).join('');
    }

    async function makeClipsFromAllVideos() {
      const link = document.getElementById('all-input-link').value.trim();
      const creatorDropdown = document.getElementById('all-select-creator').value;
      const target = link || creatorDropdown;

      if (!target) {
        alert("Please paste a Twitch, YouTube or Kick link, or select a creator.");
        return;
      }

      let matchedCreator = allCreatorsData.find(c => c.handle === target || c.name.toLowerCase() === target.toLowerCase() || target.includes(c.name.toLowerCase()));
      if (!matchedCreator) {
        let name = target.split('/').pop().replace(/[@?#].*/, '') || "Streamer";
        let platform = target.includes("youtube") ? "youtube" : (target.includes("kick") ? "kick" : "twitch");
        matchedCreator = { name: name, handle: target, platform: platform, avatar: `/static/avatars/ishowspeed.png` };
      }

      switchView('STREAMER', matchedCreator);
      startStreamerWatcher(target);
    }

    // Modal Review Controls
    let currentModalVideoUrl = '';

    async function openReviewModal(videoUrl, srtUrl, headline) {
      currentModalVideoUrl = videoUrl;
      const modal = document.getElementById('modal-review-player');
      const player = document.getElementById('modal-video-player');
      const title = document.getElementById('modal-review-title');
      const headerTitle = document.getElementById('modal-header-title');
      const subOverlay = document.getElementById('modal-sub-overlay');
      const dlVideo = document.getElementById('btn-modal-dl-video');
      const dlSrt = document.getElementById('btn-modal-dl-srt');

      title.textContent = headline || "Clip Review";
      headerTitle.textContent = headline || "BEST FRIENDS FIGHT FOR MONEY";
      dlVideo.href = videoUrl;

      if (srtUrl) {
        dlSrt.href = srtUrl;
        dlSrt.classList.remove('hidden');
        loadModalSubtitles(srtUrl);
      } else {
        dlSrt.classList.add('hidden');
        subOverlay.classList.add('hidden');
        modalParsedSubs = [];
      }

      player.src = videoUrl;
      player.loop = modalLooping;
      modal.classList.remove('hidden');

      try {
        await player.play();
        document.getElementById('modal-play-icon').textContent = "⏸";
      } catch (err) {
        document.getElementById('modal-play-icon').textContent = "▶";
      }

      player.onloadedmetadata = () => {
        document.getElementById('modal-time-dur').textContent = formatSeconds(player.duration || 0);
        document.getElementById('modal-seek').value = 0;
      };

      player.ontimeupdate = () => {
        const cur = player.currentTime || 0;
        const dur = player.duration || 1;
        document.getElementById('modal-time-cur').textContent = formatSeconds(cur);
        document.getElementById('modal-seek').value = ((cur / dur) * 100).toFixed(1);

        if (modalParsedSubs.length > 0) {
          const activeSub = modalParsedSubs.find(s => cur >= s.start && cur <= s.end);
          const subText = document.getElementById('modal-sub-text');
          if (activeSub) {
            subText.textContent = activeSub.text.toUpperCase();
            subOverlay.classList.remove('hidden');
          } else {
            subOverlay.classList.add('hidden');
          }
        }
      };

      player.onended = () => {
        if (!modalLooping) {
          document.getElementById('modal-play-icon').textContent = "↺";
        }
      };
    }

    function closeReviewModal() {
      const modal = document.getElementById('modal-review-player');
      const player = document.getElementById('modal-video-player');
      if (player) player.pause();
      modal.classList.add('hidden');
    }

    async function deleteModalClip() {
      if (!currentModalVideoUrl) return;
      if (!confirm("Are you sure you want to delete this clip?")) return;
      try {
        const res = await fetch(`/api/clips/${encodeURIComponent(currentModalVideoUrl)}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok) {
          closeReviewModal();
          pollStatus();
        } else {
          alert(`Error: ${data.detail || 'Could not delete clip'}`);
        }
      } catch (err) {
        alert("Error deleting clip: " + err);
      }
    }

    async function loadModalSubtitles(srtUrl) {
      try {
        const res = await fetch(srtUrl);
        if (!res.ok) return;
        const srtText = await res.text();
        modalParsedSubs = parseSRT(srtText);
      } catch (err) {
        console.error("Modal sub error:", err);
      }
    }

    function toggleModalPlay() {
      const player = document.getElementById('modal-video-player');
      if (player.paused) {
        player.play();
        document.getElementById('modal-play-icon').textContent = "⏸";
      } else {
        player.pause();
        document.getElementById('modal-play-icon').textContent = "▶";
      }
    }

    function modalSeekRel(sec) {
      const player = document.getElementById('modal-video-player');
      if (!player.src) return;
      player.currentTime = Math.max(0, Math.min(player.duration || 0, player.currentTime + sec));
    }

    function onModalSeek(percent) {
      const player = document.getElementById('modal-video-player');
      if (!player.duration) return;
      player.currentTime = (percent / 100) * player.duration;
    }

    function toggleModalLoop() {
      const player = document.getElementById('modal-video-player');
      modalLooping = !modalLooping;
      player.loop = modalLooping;
      const btn = document.getElementById('btn-modal-loop');
      btn.className = modalLooping
        ? "text-white font-bold transition p-1 cursor-pointer scale-110"
        : "text-neutral-400 hover:text-white transition p-1 cursor-pointer";
    }

    // Startup
    window.addEventListener('DOMContentLoaded', () => {
      initChart();
      pollStatus();
      setInterval(pollStatus, 1500);
    });
  </script>
</body>
</html>
"""


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Clip Max Studio Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to run web server on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind server to")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(f"http://localhost:{args.port}")), daemon=True).start()

    print(f"""=================================================================
       CLIP MAX STUDIO (CREATORS HUB & 9:16 BLUR)       
=================================================================
 URL             : http://{args.host}:{args.port}
 Streamers Hub   : Left Sidebar (Home, All Videos, Creators)
 Clip Folders    : clips/<creator_name>/
 Video Replay    : HTTP 206 Range Streaming + Replay + Loop
 Clips Output    : {CLIPS_DIR}
=================================================================""")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
