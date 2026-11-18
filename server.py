#!/usr/bin/env python3
"""
clip-watcher Studio Web Dashboard:
- Sidebar styled exactly like the user's reference:
  - Orange 'Home' button
  - 'All videos' with clip count badge
  - 'Creators' section with circular avatars
  - '⊕ New creator' button and modal to add streamers
- Per-streamer clip folders (clips/<creator>/)
- 9:16 vertical blurred background layout matching TikTok/Shorts
- Full replay controls (↺ Replay, Loop, Play/Pause, Bold Yellow Subtitles)
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
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
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

# Mount static files for /clips and /static so HTML5 video players support HTTP 206 Range requests
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class WatcherManager:
    """Manages recording, chat monitoring, creators list, and per-creator clipping."""

    def __init__(self):
        self.lock = threading.RLock()
        self.is_running = False
        self.platform = "twitch"
        self.target_name = ""
        self.stream_url = ""
        self.youtube_video_id = None
        self.min_messages = 5
        self.quality = "720p60,best"
        self.whisper_model = "tiny"
        self.cooldown = 30
        self.chat_listener = None
        self.recorder = None
        self.detector = None
        self.clip_processor = None
        self.bucket_history = collections.deque(maxlen=30)
        self.log_messages = collections.deque(maxlen=60)
        self.monitor_thread = None

        # Load creators list
        self.creators = self._load_creators()

    def _load_creators(self) -> dict:
        """Loads creators matching UI reference and merges with creators.json and folders in clips/."""
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
                    default_creators.update(saved)
            except Exception:
                pass

        # Also add any folders existing in clips/ if they have content
        for item in CLIPS_DIR.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                name = item.name
                if name not in default_creators:
                    default_creators[name] = {
                        "name": name,
                        "platform": "youtube" if "youtube" in name.lower() else "twitch",
                        "handle": name,
                        "avatar": f"https://unavatar.io/twitch/{name}",
                    }

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

            # Ensure per-streamer folder exists in clips/
            creator_folder = CLIPS_DIR / clean_name
            creator_folder.mkdir(parents=True, exist_ok=True)

            if not avatar:
                if platform == "youtube" or "youtube" in handle:
                    avatar = f"https://unavatar.io/youtube/{clean_name}"
                else:
                    avatar = f"https://unavatar.io/twitch/{clean_name}"

            self.creators[clean_name] = {
                "name": clean_name,
                "platform": platform,
                "handle": handle.strip(),
                "avatar": avatar,
            }
            self._save_creators()
            self.add_log(f"Added creator '{clean_name}' (clips/{clean_name}/)")
            return True, f"Creator '{clean_name}' added successfully."

    def add_log(self, text: str):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.log_messages.append(f"[{now_str}] {text}")

    def start(self, target: str, min_messages: int = 5, quality: str = "720p60,best", whisper_model: str = "tiny"):
        with self.lock:
            if self.is_running:
                return False, "Watcher is already running. Stop it first."

            target_info = resolve_stream_target(target)
            self.platform = target_info["platform"]
            self.target_name = target_info["display_name"]
            self.stream_url = target_info["stream_url"]
            self.min_messages = min_messages
            self.quality = quality
            self.whisper_model = whisper_model
            self.bucket_history.clear()
            self.is_running = True

            # Ensure creator is in creators list
            if self.target_name not in self.creators:
                self.add_creator(name=self.target_name, handle=self.stream_url, platform=self.platform)

            self.add_log(f"Starting watcher for {self.platform.upper()}: {self.stream_url}...")

            # If YouTube target has no direct video ID, probe streamlink
            if self.platform == "youtube" and not target_info.get("youtube_video_id"):
                self.add_log(f"Resolving YouTube live video ID for {self.stream_url}...")
                try:
                    probe = subprocess.run(["streamlink", "--json", self.stream_url], capture_output=True, text=True)
                    meta = json.loads(probe.stdout)
                    yt_id = meta.get("metadata", {}).get("id")
                    if yt_id:
                        target_info["youtube_video_id"] = yt_id
                        self.add_log(f"Found active YouTube live ID: {yt_id}")
                except Exception as e:
                    self.add_log(f"Probe error: {e}")

            self.youtube_video_id = target_info.get("youtube_video_id")

            # 1. Start Chat Listener
            if self.platform == "youtube":
                if not self.youtube_video_id:
                    self.is_running = False
                    return False, "Could not determine YouTube live video ID for chat."
                self.chat_listener = YouTubeChatListener(video_id=self.youtube_video_id)
            else:
                self.chat_listener = TwitchIRCListener(channel=target_info["channel"])

            self.chat_listener.start()

            # 2. Start Streamlink
            self.recorder = StreamRecorder(target_info=target_info, quality=self.quality, rec_dir=REC_DIR)
            if not self.recorder.start():
                self.is_running = False
                self.chat_listener.stop()
                return False, f"Failed to start streamlink. Stream may be offline."

            # 3. Setup Clip Processor with per-creator folder
            self.clip_processor = ClipProcessor(
                rec_path=self.recorder.rec_path,
                streamer_name=self.target_name,
                clips_dir=CLIPS_DIR,
                whisper_model=self.whisper_model,
                enable_whisper=True,
            )
            self.detector = SpikeDetector(
                min_messages=self.min_messages,
                bucket_seconds=10,
                window_minutes=3,
                cooldown_seconds=self.cooldown,
            )

            # 4. Start background monitoring loop
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="WatcherMonitor")
            self.monitor_thread.start()

            return True, f"Started watching {self.platform.upper()} stream: {self.target_name}"

    def stop(self):
        with self.lock:
            if not self.is_running:
                return False, "Watcher is not running."

            self.is_running = False
            self.add_log("Stopping watcher...")
            if self.chat_listener:
                self.chat_listener.stop()
            if self.recorder:
                self.recorder.stop()
            if self.clip_processor:
                self.clip_processor.stop()
            self.add_log("Watcher stopped.")
            return True, "Watcher stopped successfully."

    def manual_clip(self):
        with self.lock:
            if not self.is_running or not self.recorder:
                return False, "Watcher must be running to cut a clip."

            stream_time = self.recorder.get_elapsed_stream_time()
            if stream_time < 5:
                return False, "Stream has just started, wait at least 5 seconds before clipping."

            spike_center = max(0.0, stream_time - 5.0)
            self.add_log(f"✂️ Manual clip triggered for {self.target_name} at stream time {stream_time:.1f}s!")
            self.clip_processor.schedule_clip(spike_center, trigger_wall_time=time.time())
            return True, f"Manual clip scheduled at stream time {stream_time:.1f}s."

    def _monitor_loop(self):
        self.add_log("Waiting 5s for recording buffer & chat connection to stabilize...")
        time.sleep(5)

        if not self.recorder or not self.recorder.is_alive():
            self.add_log("Notice: Stream appears offline or streamlink exited.")
            self.is_running = False
            return

        self.add_log("Live monitoring active.")
        bucket_interval = 10.0
        next_bucket_time = time.time() + bucket_interval

        while self.is_running:
            if self.recorder and not self.recorder.is_alive():
                self.add_log("Streamlink process has finished or disconnected.")
                self.is_running = False
                break

            now = time.time()
            sleep_time = next_bucket_time - now
            if sleep_time > 0:
                time.sleep(min(0.5, sleep_time))
                continue

            bucket_end = next_bucket_time
            bucket_start = bucket_end - bucket_interval
            next_bucket_time += bucket_interval

            msg_count = self.chat_listener.get_messages_in_interval(bucket_start, bucket_end)
            stream_time = self.recorder.get_elapsed_stream_time()

            is_spike, med, threshold = self.detector.evaluate_bucket(msg_count, stream_time)

            bucket_entry = {
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "stream_time": int(stream_time),
                "count": msg_count,
                "median": round(med, 1),
                "threshold": round(threshold, 1),
                "is_spike": is_spike,
            }

            with self.lock:
                self.bucket_history.append(bucket_entry)

            if is_spike:
                self.add_log(f"🚨 CHAT SPIKE! ({msg_count} msgs >= {threshold:.1f} thresh). Auto-cutting 9:16 clip into clips/{self.target_name}/...")
                spike_center = max(0.0, stream_time - 5.0)
                self.clip_processor.schedule_clip(spike_center, trigger_wall_time=time.time())

    def get_status(self):
        with self.lock:
            elapsed = self.recorder.get_elapsed_stream_time() if (self.recorder and self.is_running) else 0.0

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

                        clips.append({
                            "name": mp4.name,
                            "streamer": c_name,
                            "url": f"/clips/{rel_path}",
                            "size_mb": round(mp4.stat().st_size / (1024 * 1024), 2),
                            "created_at": datetime.datetime.fromtimestamp(mp4.stat().st_mtime).strftime("%H:%M:%S"),
                            "has_srt": srt_file.exists(),
                            "srt_url": f"/clips/{srt_rel}" if srt_rel else None,
                        })

            clips.sort(key=lambda c: c["created_at"], reverse=True)

            # Build creators list
            creators_list = []
            for c_name, info in self.creators.items():
                is_active = (self.is_running and self.target_name == c_name)
                creators_list.append({
                    "name": info["name"],
                    "platform": info.get("platform", "twitch"),
                    "handle": info.get("handle", c_name),
                    "avatar": info.get("avatar", f"https://unavatar.io/twitch/{c_name}"),
                    "clip_count": creator_counts.get(c_name, 0),
                    "is_active": is_active,
                })

            return {
                "is_running": self.is_running,
                "platform": self.platform,
                "target_name": self.target_name,
                "stream_url": self.stream_url,
                "youtube_video_id": self.youtube_video_id,
                "min_messages": self.min_messages,
                "quality": self.quality,
                "whisper_model": self.whisper_model,
                "elapsed_seconds": int(elapsed),
                "bucket_history": list(self.bucket_history),
                "logs": list(self.log_messages),
                "creators": creators_list,
                "clips": clips,
            }


manager = WatcherManager()


class StartRequest(BaseModel):
    target: str
    min_messages: int = 5
    quality: str = "720p60,best"
    whisper_model: str = "tiny"


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
def api_stop():
    success, msg = manager.stop()
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/clip-now")
def api_clip_now():
    success, msg = manager.manual_clip()
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


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


@app.get("/api/status")
def api_status():
    return manager.get_status()


@app.get("/", response_class=HTMLResponse)
def index_page():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>clip-watcher Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { background-color: #0d0c0b; color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .custom-scroll::-webkit-scrollbar { width: 5px; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #292524; border-radius: 3px; }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- Header -->
  <header class="border-b border-[#221f1c] bg-[#141210]/95 backdrop-blur px-6 py-2.5 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center gap-3">
      <div class="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 to-orange-500 flex items-center justify-center font-bold text-slate-950 shadow-md shadow-orange-500/20 text-base">
        ⚡
      </div>
      <div>
        <h1 class="font-bold text-sm tracking-wide text-white flex items-center gap-2">
          <span>clip-watcher Studio</span>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-orange-500/20 text-orange-400 border border-orange-500/30">9:16 Shorts</span>
        </h1>
        <p class="text-[11px] text-stone-400">Twitch &amp; YouTube Live &bull; Real-time Chat Velocity &bull; Whisper Subtitles</p>
      </div>
    </div>

    <div class="flex items-center gap-3">
      <div id="status-pill" class="flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono bg-stone-900 text-stone-400 border border-stone-800">
        <span class="w-2 h-2 rounded-full bg-stone-600"></span>
        <span>Idle</span>
      </div>
      <span id="elapsed-timer" class="text-xs font-mono text-stone-400">00:00:00</span>
    </div>
  </header>

  <!-- Controls Bar -->
  <div class="border-b border-[#221f1c] bg-[#171512] px-6 py-2 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-2.5 flex-wrap">
      
      <!-- Input for Twitch channel or YouTube URL -->
      <div class="relative">
        <input id="input-target" type="text" placeholder="Enter Twitch channel or YouTube URL..." value=""
          class="bg-[#0f0e0c] border border-stone-700/80 rounded-xl px-3 py-1.5 text-xs text-white placeholder-stone-500 focus:outline-none focus:border-orange-500 w-80 font-mono" />
      </div>

      <div class="flex items-center gap-2 text-xs text-stone-300 bg-[#0f0e0c] px-3 py-1.5 rounded-xl border border-stone-800">
        <span>Min Spike:</span>
        <input id="input-min" type="number" min="1" max="100" value="5" class="w-10 bg-transparent font-mono text-orange-400 focus:outline-none" />
      </div>

      <div class="flex items-center gap-2 text-xs text-stone-300 bg-[#0f0e0c] px-3 py-1.5 rounded-xl border border-stone-800">
        <span>Quality:</span>
        <select id="select-quality" class="bg-transparent text-orange-400 focus:outline-none text-xs">
          <option value="720p60,720p,best">720p60</option>
          <option value="480p30,480p,best">480p</option>
          <option value="1080p60,1080p,best">1080p60</option>
          <option value="best">Best</option>
        </select>
      </div>

      <div class="flex items-center gap-2 text-xs text-stone-300 bg-[#0f0e0c] px-3 py-1.5 rounded-xl border border-stone-800">
        <span>Whisper:</span>
        <select id="select-model" class="bg-transparent text-amber-400 focus:outline-none text-xs">
          <option value="tiny">Tiny (Fastest)</option>
          <option value="base">Base</option>
          <option value="small">Small</option>
        </select>
      </div>

      <button id="btn-start" onclick="startWatcher()" class="bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-400 hover:to-amber-400 text-slate-950 font-bold px-4 py-1.5 rounded-xl text-xs shadow-md shadow-orange-500/20 transition cursor-pointer flex items-center gap-1.5">
        <span>▶ Start Watcher</span>
      </button>

      <button id="btn-stop" onclick="stopWatcher()" class="hidden bg-red-600 hover:bg-red-500 text-white px-4 py-1.5 rounded-xl font-bold text-xs transition cursor-pointer flex items-center gap-1.5">
        <span>⏹ Stop Watcher</span>
      </button>
    </div>

    <!-- Manual Clipping Action Button -->
    <div>
      <button id="btn-clip-now" onclick="triggerManualClip()" class="bg-stone-800 hover:bg-stone-700 border border-stone-700 text-amber-400 font-bold px-4 py-1.5 rounded-xl text-xs transition flex items-center gap-2 cursor-pointer shadow-sm">
        <span class="text-sm">✂️</span>
        <span>Cut Clip Now (Manual)</span>
      </button>
    </div>
  </div>

  <!-- Studio Layout: Sidebar + Main Studio -->
  <div class="flex-1 flex overflow-hidden">

    <!-- LEFT SIDEBAR: Styled exactly as user reference image -->
    <aside class="w-64 bg-[#140e0a] border-r border-[#241c16] flex flex-col p-3 space-y-3.5 select-none shrink-0 overflow-y-auto custom-scroll">
      
      <!-- Top Action: Home Button (Vibrant Orange with Dark Text & Icon) -->
      <button onclick="goHome()" id="nav-btn-home"
        class="w-full bg-[#f97316] hover:bg-[#ea580c] text-[#0d0c0a] font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm shadow-lg shadow-orange-500/20 transition cursor-pointer">
        <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>
        <span class="text-sm font-bold tracking-tight">Home</span>
      </button>

      <!-- Second Action: All Videos Button with Count Badge -->
      <button onclick="filterByCreator('ALL')" id="nav-btn-all"
        class="w-full bg-[#201a15] hover:bg-[#2c241e] text-white font-medium px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer border border-[#2b221a]">
        <div class="flex items-center gap-3">
          <svg class="w-5 h-5 fill-stone-300" viewBox="0 0 24 24"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>
          <span class="font-semibold text-sm">All videos</span>
        </div>
        <span id="all-videos-count-badge" class="font-mono text-xs bg-[#392f26] text-stone-200 font-bold px-2.5 py-0.5 rounded-full">0</span>
      </button>

      <!-- Section: Creators -->
      <div class="pt-2 space-y-2">
        <div class="px-2 text-[#8b8075] text-xs font-semibold tracking-wide">
          Creators
        </div>

        <!-- Dynamic Creators List -->
        <div id="creators-sidebar-list" class="space-y-1.5">
          <!-- Populated via JS -->
        </div>

        <!-- + New Creator Button -->
        <button onclick="openNewCreatorModal()"
          class="w-full text-left px-2.5 py-2.5 rounded-xl text-sm font-semibold text-white hover:bg-[#201a15] transition flex items-center gap-3.5 cursor-pointer group">
          <div class="w-7 h-7 rounded-full border border-stone-500 flex items-center justify-center text-stone-400 group-hover:border-orange-400 group-hover:text-orange-400 transition">
            <span class="text-sm font-light leading-none mb-0.5">⊕</span>
          </div>
          <span>New creator</span>
        </button>
      </div>

    </aside>

    <!-- MAIN STUDIO AREA -->
    <main class="flex-1 p-6 grid grid-cols-1 xl:grid-cols-12 gap-6 overflow-y-auto custom-scroll">

      <!-- Left Column: Video & Chat Frequency (7.5 Cols) -->
      <div class="xl:col-span-8 flex flex-col gap-5">
        
        <!-- Live Stream Player -->
        <div class="bg-[#171512] border border-[#221f1c] rounded-2xl overflow-hidden shadow-xl flex flex-col">
          <div class="px-4 py-2.5 bg-[#141210] border-b border-[#221f1c] flex items-center justify-between text-xs">
            <div class="flex items-center gap-2 font-medium text-stone-300">
              <span id="player-live-dot" class="w-2 h-2 rounded-full bg-stone-600"></span>
              <span>Live Stream:</span>
              <span id="player-title" class="font-mono text-stone-400">Standby (No Stream Active)</span>
              <span id="platform-badge" class="hidden px-2 py-0.5 rounded text-[10px] font-mono bg-orange-500/20 text-orange-400 uppercase">TWITCH</span>
            </div>
            <span class="text-stone-500 text-[11px]" id="player-provider-label">Ready</span>
          </div>
          
          <div class="relative w-full aspect-video bg-black flex items-center justify-center">
            <div id="stream-placeholder" class="text-center p-8 text-stone-500 flex flex-col items-center gap-2">
              <span class="text-4xl opacity-30">📺</span>
              <p class="text-xs text-stone-400">Select a creator from the left or enter a URL above and click <strong class="text-orange-400 font-semibold">▶ Start Watcher</strong>.</p>
            </div>
            <iframe id="stream-frame"
              src=""
              class="w-full h-full border-0 hidden"
              allowfullscreen="true">
            </iframe>
          </div>
        </div>

        <!-- Real-Time Chat Frequency Chart -->
        <div class="bg-[#171512] border border-[#221f1c] rounded-2xl p-4 shadow-xl flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-base">📈</span>
              <h3 class="font-semibold text-xs text-stone-200">Chat Velocity (10s Buckets)</h3>
            </div>
            <div class="flex items-center gap-3 text-xs font-mono text-[11px]">
              <div class="flex items-center gap-1">
                <span class="w-2.5 h-2.5 rounded-sm bg-orange-500"></span>
                <span class="text-stone-400">Msgs</span>
              </div>
              <div class="flex items-center gap-1">
                <span class="w-3 h-0.5 bg-emerald-400"></span>
                <span class="text-stone-400">Median</span>
              </div>
              <div class="flex items-center gap-1">
                <span class="w-3 h-0.5 bg-red-500"></span>
                <span class="text-stone-400">Spike</span>
              </div>
            </div>
          </div>

          <div class="h-40 w-full">
            <canvas id="chatChart"></canvas>
          </div>
        </div>

        <!-- Terminal Activity Log -->
        <div class="bg-[#171512] border border-[#221f1c] rounded-2xl p-3.5 shadow-xl flex flex-col gap-2">
          <div class="flex items-center justify-between text-xs text-stone-400 border-b border-[#221f1c] pb-1.5">
            <span class="font-medium text-stone-200">Terminal Activity</span>
            <span class="font-mono text-[10px] text-stone-500">Auto-scrolling</span>
          </div>
          <div id="log-box" class="h-24 overflow-y-auto font-mono text-xs text-stone-300 space-y-1 custom-scroll bg-[#0e0d0b] p-2 rounded-xl border border-stone-800/80">
            <div class="text-stone-500">[System] Ready to clip. Select a creator to start.</div>
          </div>
        </div>

      </div>

      <!-- Right Column: 9:16 Vertical Video Player (4.5 Cols) -->
      <div class="xl:col-span-4 flex flex-col gap-5">

        <!-- 9:16 Vertical Video Player with Custom Media Controller -->
        <div class="bg-[#171512] border border-[#221f1c] rounded-2xl p-4 shadow-xl flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <h3 class="font-semibold text-xs text-stone-200 flex items-center gap-2">
              <span>🎬</span>
              <span>9:16 Vertical Player</span>
            </h3>
            <span id="preview-badge" class="text-xs font-mono text-orange-400 truncate max-w-[160px]">Select a clip</span>
          </div>

          <!-- Vertical Video Container (9:16) -->
          <div class="relative w-full aspect-[9/16] max-h-[410px] bg-black rounded-2xl border border-stone-800 overflow-hidden mx-auto flex items-center justify-center group shadow-2xl">
            <video id="clip-player" playsinline class="w-full h-full object-contain hidden"></video>

            <div id="player-placeholder" class="text-center p-6 text-stone-500 flex flex-col items-center gap-2">
              <span class="text-3xl opacity-30">📱</span>
              <p class="text-xs text-stone-400">Select any clip below to play &amp; replay.</p>
            </div>

            <!-- Top Floating Header Badge (matching reference style) -->
            <div id="clip-header-badge" class="hidden absolute top-6 inset-x-3 flex justify-center z-20 pointer-events-none transition-all">
              <div id="clip-header-title" class="bg-white text-black font-black text-xs px-3.5 py-1.5 rounded-lg shadow-xl tracking-wide uppercase text-center truncate max-w-[90%] drop-shadow-md">
                BEST FRIENDS FIGHT FOR MONEY
              </div>
            </div>

            <!-- Subtitle Banner Overlay (Bold Yellow with Heavy Black Outline in lower blur area) -->
            <div id="sub-overlay" class="hidden absolute bottom-6 inset-x-2 text-center pointer-events-none z-20 transition-all">
              <span id="sub-text" class="inline-block text-[#ffe500] font-black text-base tracking-wider uppercase px-2 py-0.5"
                    style="text-shadow: 2px 2px 0 #000, -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 0 3px 8px rgba(0,0,0,0.95);">
                OH!
              </span>
            </div>
          </div>

          <!-- Full Media Player Controls Bar (Seekbar + Replay/Play/Skip/Loop Buttons) -->
          <div id="replay-controls" class="hidden flex flex-col gap-2.5 pt-1">
            
            <!-- Seekbar & Time Display -->
            <div class="flex items-center gap-2 text-[10px] font-mono text-stone-400 px-1">
              <span id="time-current">0:00</span>
              <input type="range" id="seek-bar" min="0" max="100" value="0" step="0.1" oninput="onSeekChange(this.value)"
                class="flex-1 accent-orange-500 h-1.5 bg-[#25201b] rounded-lg cursor-pointer" />
              <span id="time-duration">0:00</span>
            </div>

            <!-- Playback Control Icons matching reference bottom bar -->
            <div class="flex items-center justify-between px-2 pt-0.5">
              
              <!-- Shuffle -->
              <button onclick="shuffleClip()" title="Shuffle Clips" class="text-stone-400 hover:text-white transition p-1 cursor-pointer">
                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>
              </button>

              <!-- Previous Clip -->
              <button onclick="prevClip()" title="Previous Clip" class="text-stone-300 hover:text-white transition p-1 cursor-pointer">
                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
              </button>

              <!-- Rewind 10s -->
              <button onclick="seekRelative(-10)" title="Rewind 10s" class="text-stone-300 hover:text-white flex items-center gap-0.5 text-[11px] font-mono font-bold transition p-1 cursor-pointer">
                <span>⟲</span><span>10</span>
              </button>

              <!-- Center Glowing Play/Pause Button (Purple-to-Orange Gradient Circle) -->
              <button onclick="togglePlay()" id="btn-main-play" title="Play/Pause"
                class="w-11 h-11 rounded-full bg-gradient-to-tr from-[#9333ea] via-[#ec4899] to-[#f97316] hover:scale-105 active:scale-95 transition-transform flex items-center justify-center text-white shadow-lg shadow-pink-500/25 cursor-pointer">
                <span id="main-play-icon" class="text-base font-bold ml-0.5">▶</span>
              </button>

              <!-- Forward 30s -->
              <button onclick="seekRelative(30)" title="Forward 30s" class="text-stone-300 hover:text-white flex items-center gap-0.5 text-[11px] font-mono font-bold transition p-1 cursor-pointer">
                <span>30</span><span>⟳</span>
              </button>

              <!-- Next Clip -->
              <button onclick="nextClip()" title="Next Clip" class="text-stone-300 hover:text-white transition p-1 cursor-pointer">
                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
              </button>

              <!-- Repeat / Loop Toggle -->
              <button onclick="toggleLoop()" id="btn-player-loop" title="Toggle Loop" class="text-stone-400 hover:text-orange-400 transition p-1 cursor-pointer">
                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>
              </button>

            </div>

          </div>

          <!-- Download Action Links -->
          <div id="clip-actions" class="hidden flex items-center justify-between gap-2 pt-1 border-t border-stone-800/80">
            <a id="btn-download-video" href="#" download class="flex-1 bg-orange-500/20 hover:bg-orange-500/30 border border-orange-500/40 text-orange-300 text-xs py-1.5 rounded-xl text-center font-medium transition">
              ⬇ Video (.mp4)
            </a>
            <a id="btn-download-srt" href="#" download class="flex-1 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-300 text-xs py-1.5 rounded-xl text-center font-medium transition">
              ⬇ Subtitles (.srt)
            </a>
          </div>
        </div>

        <!-- Generated Clips List (Filtered by Creator) -->
        <div class="bg-[#171512] border border-[#221f1c] rounded-2xl p-4 shadow-xl flex flex-col gap-3 flex-1">
          <div class="flex items-center justify-between border-b border-[#221f1c] pb-2">
            <div class="flex items-center gap-2">
              <span>📁</span>
              <h3 id="clips-heading" class="font-semibold text-xs text-stone-200">Clips</h3>
              <span id="filtered-count-badge" class="px-2 py-0.5 rounded-full text-[10px] font-mono bg-orange-500/20 text-orange-400">0</span>
            </div>
            <span id="current-creator-filter-label" class="text-xs font-mono text-orange-400">All Creators</span>
          </div>

          <div id="clips-list" class="space-y-2 overflow-y-auto max-h-[300px] custom-scroll pr-1">
            <div class="text-center py-8 text-xs text-stone-500">
              No clips found.
            </div>
          </div>
        </div>

      </div>

    </main>

  </div>

  <!-- MODAL: + New Creator -->
  <div id="modal-new-creator" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="bg-[#171512] border border-stone-800 rounded-2xl p-6 w-full max-w-md shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-stone-800 pb-3">
        <h3 class="font-bold text-base text-white flex items-center gap-2">
          <span class="text-orange-400">⊕</span>
          <span>Add New Creator</span>
        </h3>
        <button onclick="closeNewCreatorModal()" class="text-stone-400 hover:text-white text-lg">&times;</button>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-xs font-semibold text-stone-300 mb-1">Creator Name:</label>
          <input id="modal-input-name" type="text" placeholder="e.g. IShowSpeed, KaiCenat, Tarik"
            class="w-full bg-[#0e0d0b] border border-stone-700 rounded-xl px-3 py-2 text-xs text-white placeholder-stone-500 focus:outline-none focus:border-orange-500" />
        </div>

        <div>
          <label class="block text-xs font-semibold text-stone-300 mb-1">Platform:</label>
          <select id="modal-select-platform" class="w-full bg-[#0e0d0b] border border-stone-700 rounded-xl px-3 py-2 text-xs text-stone-300 focus:outline-none focus:border-orange-500">
            <option value="twitch">Twitch</option>
            <option value="youtube">YouTube</option>
          </select>
        </div>

        <div>
          <label class="block text-xs font-semibold text-stone-300 mb-1">Channel Handle or URL:</label>
          <input id="modal-input-handle" type="text" placeholder="e.g. shroud or https://youtube.com/@Channel/live"
            class="w-full bg-[#0e0d0b] border border-stone-700 rounded-xl px-3 py-2 text-xs text-white placeholder-stone-500 focus:outline-none focus:border-orange-500" />
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-2 border-t border-stone-800">
        <button onclick="closeNewCreatorModal()" class="px-4 py-2 rounded-xl text-xs font-medium text-stone-400 hover:text-white transition">Cancel</button>
        <button onclick="submitNewCreator()" class="bg-[#f97316] hover:bg-[#ea580c] text-slate-950 font-bold px-4 py-2 rounded-xl text-xs shadow-md shadow-orange-500/20 transition">Add Creator</button>
      </div>
    </div>
  </div>

  <script>
    let chartInstance = null;
    let selectedClip = null;
    let activeCreatorFilter = 'ALL';
    let currentParsedSubtitles = [];
    let isLooping = false;
    let allClipsData = [];
    let allCreatorsData = [];

    function initChart() {
      const ctx = document.getElementById('chatChart').getContext('2d');
      chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: [],
          datasets: [
            {
              type: 'bar',
              label: 'Messages',
              data: [],
              backgroundColor: 'rgba(249, 115, 22, 0.7)',
              borderColor: 'rgba(249, 115, 22, 1)',
              borderWidth: 1,
              borderRadius: 4,
              order: 2
            },
            {
              type: 'line',
              label: '3m Median',
              data: [],
              borderColor: 'rgba(52, 211, 153, 1)',
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
              borderColor: 'rgba(239, 68, 68, 0.9)',
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
          animation: { duration: 300 },
          scales: {
            x: {
              grid: { color: 'rgba(255, 255, 255, 0.04)' },
              ticks: { color: '#78716c', font: { size: 10 } }
            },
            y: {
              beginAtZero: true,
              grid: { color: 'rgba(255, 255, 255, 0.04)' },
              ticks: { color: '#78716c', font: { size: 10 } }
            }
          },
          plugins: {
            legend: { display: false }
          }
        }
      });
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

      // 1. Status Pill & Timer
      const pill = document.getElementById('status-pill');
      const timer = document.getElementById('elapsed-timer');
      const btnStart = document.getElementById('btn-start');
      const btnStop = document.getElementById('btn-stop');
      const platformBadge = document.getElementById('platform-badge');
      const playerProvider = document.getElementById('player-provider-label');
      const playerTitle = document.getElementById('player-title');
      const liveDot = document.getElementById('player-live-dot');

      if (data.is_running) {
        pill.className = `flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono bg-emerald-950 text-emerald-400 border border-emerald-800 shadow-sm shadow-emerald-500/20`;
        pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-500 animate-ping"></span><span>Recording #${data.target_name}</span>`;
        btnStart.classList.add('hidden');
        btnStop.classList.remove('hidden');

        platformBadge.textContent = data.platform.toUpperCase();
        platformBadge.classList.remove('hidden');
        playerProvider.textContent = data.platform === 'youtube' ? 'YouTube Embed' : 'Twitch Embed';
        playerTitle.textContent = data.target_name;
        liveDot.className = "w-2 h-2 rounded-full bg-red-500 animate-pulse";

        const hrs = String(Math.floor(data.elapsed_seconds / 3600)).padStart(2, '0');
        const mins = String(Math.floor((data.elapsed_seconds % 3600) / 60)).padStart(2, '0');
        const secs = String(data.elapsed_seconds % 60).padStart(2, '0');
        timer.textContent = `${hrs}:${mins}:${secs}`;
      } else {
        pill.className = "flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono bg-stone-900 text-stone-400 border border-stone-800";
        pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-stone-600"></span><span>Idle</span>`;
        btnStart.classList.remove('hidden');
        btnStop.classList.add('hidden');
        timer.textContent = "00:00:00";
      }

      // 2. Chart Update
      if (chartInstance && data.bucket_history) {
        const labels = data.bucket_history.map(b => b.time);
        const counts = data.bucket_history.map(b => b.count);
        const medians = data.bucket_history.map(b => b.median);
        const thresholds = data.bucket_history.map(b => b.threshold);
        const colors = data.bucket_history.map(b => b.is_spike ? 'rgba(239, 68, 68, 0.9)' : 'rgba(249, 115, 22, 0.7)');

        chartInstance.data.labels = labels;
        chartInstance.data.datasets[0].data = counts;
        chartInstance.data.datasets[0].backgroundColor = colors;
        chartInstance.data.datasets[1].data = medians;
        chartInstance.data.datasets[2].data = thresholds;
        chartInstance.update();
      }

      // 3. Activity Logs
      const logBox = document.getElementById('log-box');
      if (data.logs && data.logs.length > 0) {
        logBox.innerHTML = data.logs.map(l => `<div class="leading-relaxed">${escapeHtml(l)}</div>`).join('');
        logBox.scrollTop = logBox.scrollHeight;
      }

      // 4. Update Sidebar: Creators List
      updateCreatorsSidebar(data.creators || [], data.clips || []);

      // 5. Update Clips Gallery with Current Filter
      renderClipsList();
    }

    function updateCreatorsSidebar(creators, clips) {
      const container = document.getElementById('creators-sidebar-list');
      const allVideosBadge = document.getElementById('all-videos-count-badge');
      allVideosBadge.textContent = clips.length;

      if (!creators || creators.length === 0) {
        container.innerHTML = `<div class="text-xs text-stone-500 px-3 py-2">No creators yet.</div>`;
        return;
      }

      container.innerHTML = creators.map(c => {
        const isSelected = activeCreatorFilter === c.name;
        const activeClass = isSelected
          ? 'bg-[#28221c] text-white shadow-sm'
          : 'text-stone-300 hover:text-white hover:bg-[#1d1915]';

        return `
          <div onclick="selectCreator('${escapeHtml(c.name)}', '${escapeHtml(c.handle)}')"
            class="flex items-center justify-between px-2.5 py-2 rounded-xl text-sm font-medium transition cursor-pointer group ${activeClass}">
            <div class="flex items-center gap-3 overflow-hidden">
              <img src="${c.avatar}" onerror="this.src='https://ui-avatars.com/api/?name=${encodeURIComponent(c.name)}&background=292524&color=f97316'"
                class="w-7 h-7 rounded-full object-cover shrink-0 border border-stone-800" />
              <span class="truncate text-xs font-semibold">${escapeHtml(c.name)}</span>
            </div>
            ${c.clip_count > 0 ? `<span class="text-[10px] font-mono font-bold text-stone-400 bg-stone-900 px-1.5 py-0.5 rounded-full">${c.clip_count}</span>` : ''}
          </div>
        `;
      }).join('');
    }

    function selectCreator(name, handle) {
      activeCreatorFilter = name;
      document.getElementById('input-target').value = handle || name;
      updatePlayerEmbed(handle || name);
      renderClipsList();
      highlightActiveNav(name);
    }

    function filterByCreator(creatorName) {
      activeCreatorFilter = creatorName;
      renderClipsList();
      highlightActiveNav(creatorName);
    }

    function goHome() {
      activeCreatorFilter = 'ALL';
      renderClipsList();
      highlightActiveNav('HOME');
    }

    function highlightActiveNav(target) {
      const homeBtn = document.getElementById('nav-btn-home');
      const allBtn = document.getElementById('nav-btn-all');

      if (target === 'HOME') {
        homeBtn.className = "w-full bg-[#f97316] text-slate-950 font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm shadow-lg shadow-orange-500/20 transition cursor-pointer";
        allBtn.className = "w-full bg-[#201c18] hover:bg-[#2c2621] text-white font-medium px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer border border-[#2b2723]";
      } else if (target === 'ALL') {
        homeBtn.className = "w-full bg-[#201c18] hover:bg-[#2c2621] text-white font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm transition cursor-pointer border border-[#2b2723]";
        allBtn.className = "w-full bg-[#f97316] text-slate-950 font-bold px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer shadow-lg shadow-orange-500/20";
      } else {
        homeBtn.className = "w-full bg-[#201c18] hover:bg-[#2c2621] text-white font-bold px-4 py-3 rounded-2xl flex items-center gap-3.5 text-sm transition cursor-pointer border border-[#2b2723]";
        allBtn.className = "w-full bg-[#201c18] hover:bg-[#2c2621] text-white font-medium px-4 py-2.5 rounded-2xl flex items-center justify-between text-sm transition cursor-pointer border border-[#2b2723]";
      }
    }

    function renderClipsList() {
      const clipsList = document.getElementById('clips-list');
      const countBadge = document.getElementById('filtered-count-badge');
      const label = document.getElementById('current-creator-filter-label');

      const filtered = (activeCreatorFilter === 'ALL')
        ? allClipsData
        : allClipsData.filter(c => c.streamer === activeCreatorFilter);

      countBadge.textContent = filtered.length;
      label.textContent = activeCreatorFilter === 'ALL' ? 'All Creators' : activeCreatorFilter;

      if (filtered.length === 0) {
        clipsList.innerHTML = `<div class="text-center py-8 text-xs text-stone-500">No clips found for ${activeCreatorFilter}.</div>`;
        return;
      }

      clipsList.innerHTML = filtered.map(c => `
        <div onclick="playClip('${c.url}', '${c.srt_url || ''}', '${escapeHtml(c.name)}') text-left"
          class="p-2.5 rounded-xl border border-stone-800/80 hover:border-orange-500/60 bg-[#12100e] hover:bg-[#1c1814] transition flex items-center justify-between cursor-pointer group">
          <div class="flex items-center gap-2.5 overflow-hidden">
            <span class="text-base">📱</span>
            <div class="truncate">
              <div class="text-xs font-semibold text-stone-200 truncate group-hover:text-orange-300">${c.name}</div>
              <div class="text-[10px] text-stone-400 font-mono">${c.streamer} &bull; ${c.created_at} &bull; ${c.size_mb} MB ${c.has_srt ? '&bull; <span class="text-amber-400 font-bold">SRT</span>' : ''}</div>
            </div>
          </div>
          <span class="text-xs text-orange-400 font-mono group-hover:translate-x-0.5 transition">&rarr;</span>
        </div>
      `).join('');
    }

    async function playClip(videoUrl, srtUrl, clipName) {
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

      // Set clean title on top floating badge
      const cleanTitle = clipName.replace(/_\\d{8}_\\d{6}.*/, '').replace(/_/g, ' ').toUpperCase();
      headerTitle.textContent = cleanTitle || "CLIP PREVIEW";
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

        // Subtitles display (Bold Yellow with black outline in lower blur area)
        if (currentParsedSubtitles.length > 0) {
          const activeSub = currentParsedSubtitles.find(s => cur >= s.start && cur <= s.end);
          const subText = document.getElementById('sub-text');
          if (activeSub) {
            subText.textContent = activeSub.text;
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
        btn.className = "text-orange-400 font-bold transition p-1 cursor-pointer scale-110";
      } else {
        btn.className = "text-stone-400 hover:text-orange-400 transition p-1 cursor-pointer";
      }
    }

    function prevClip() {
      const list = getFilteredClips();
      if (list.length === 0) return;
      let idx = list.findIndex(c => c.name === selectedClip);
      if (idx <= 0) idx = list.length - 1;
      else idx -= 1;
      const c = list[idx];
      playClip(c.url, c.srt_url || '', c.name);
    }

    function nextClip() {
      const list = getFilteredClips();
      if (list.length === 0) return;
      let idx = list.findIndex(c => c.name === selectedClip);
      if (idx === -1 || idx >= list.length - 1) idx = 0;
      else idx += 1;
      const c = list[idx];
      playClip(c.url, c.srt_url || '', c.name);
    }

    function shuffleClip() {
      const list = getFilteredClips();
      if (list.length === 0) return;
      const rnd = Math.floor(Math.random() * list.length);
      const c = list[rnd];
      playClip(c.url, c.srt_url || '', c.name);
    }

    function getFilteredClips() {
      return (activeCreatorFilter === 'ALL')
        ? allClipsData
        : allClipsData.filter(c => c.streamer === activeCreatorFilter);
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
        console.error("Subtitle load error:", err);
      }
    }

    function parseSRT(srt) {
      const items = [];
      const blocks = srt.trim().replace(/\\r\\n/g, '\\n').split('\\n\\n');

      for (const block of blocks) {
        const lines = block.split('\\n');
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

      if (isYt) {
        let vidId = null;
        const m1 = t.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
        const m2 = t.match(/(?:youtu\\.be\\/|\\/live\\/|\\/embed\\/)([a-zA-Z0-9_-]{11})/);
        if (m1) vidId = m1[1];
        else if (m2) vidId = m2[1];

        if (vidId) {
          frame.src = `https://www.youtube.com/embed/${vidId}?autoplay=1`;
        }
      } else {
        const clean = t.replace(/^https?:\\/\\/(?:www\\.)?twitch\\.tv\\//i, '').replace(/[@#]/g, '');
        frame.src = `https://player.twitch.tv/?channel=${clean}&parent=localhost&parent=127.0.0.1&muted=false`;
      }

      placeholder.classList.add('hidden');
      frame.classList.remove('hidden');
      document.getElementById('platform-badge').classList.remove('hidden');
      document.getElementById('player-live-dot').className = "w-2 h-2 rounded-full bg-red-500 animate-pulse";
      document.getElementById('player-title').textContent = t;
      document.getElementById('player-title').className = "font-mono text-orange-400";
    }

    async function startWatcher() {
      const target = document.getElementById('input-target').value.trim();
      const minMsgs = parseInt(document.getElementById('input-min').value) || 5;
      const quality = document.getElementById('select-quality').value;
      const model = document.getElementById('select-model').value;

      if (!target) {
        alert("Please enter a Twitch channel or YouTube URL.");
        return;
      }

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
        if (!res.ok) {
          alert(`Error: ${data.detail || 'Could not start watcher'}`);
        }
        pollStatus();
      } catch (err) {
        alert("Connection error starting watcher: " + err);
      }
    }

    async function stopWatcher() {
      try {
        const res = await fetch('/api/stop', { method: 'POST' });
        const data = await res.json();
        pollStatus();
      } catch (err) {
        alert("Error stopping watcher: " + err);
      }
    }

    async function triggerManualClip() {
      try {
        const res = await fetch('/api/clip-now', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) {
          alert(data.detail || "Manual clip failed.");
        } else {
          const btn = document.getElementById('btn-clip-now');
          const orig = btn.innerHTML;
          btn.innerHTML = `<span class="text-orange-400">✅ Clip Scheduled!</span>`;
          setTimeout(() => btn.innerHTML = orig, 2000);
        }
        pollStatus();
      } catch (err) {
        alert("Error triggering manual clip: " + err);
      }
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
          body: JSON.stringify({ name, handle, platform })
        });
        const data = await res.json();
        if (!res.ok) {
          alert(data.detail || "Failed to add creator.");
          return;
        }

        closeNewCreatorModal();
        document.getElementById('modal-input-name').value = '';
        document.getElementById('modal-input-handle').value = '';
        selectCreator(name, handle);
        pollStatus();
      } catch (err) {
        alert("Error adding creator: " + err);
      }
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    window.onload = () => {
      initChart();
      pollStatus();
      setInterval(pollStatus, 1500);
    };
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


def main():
    parser = argparse.ArgumentParser(description="clip-watcher Studio Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print("=" * 65)
    print("      CLIP-WATCHER STUDIO (CREATORS HUB & 9:16 BLUR)       ")
    print("=" * 65)
    print(f" URL             : {url}")
    print(f" Streamers Hub   : Left Sidebar (Home, All Videos, Creators)")
    print(f" Clip Folders    : clips/<creator_name>/")
    print(f" Video Replay    : HTTP 206 Range Streaming + Replay ↺ + Loop")
    print(f" Clips Output    : {CLIPS_DIR}")
    print("=" * 65)

    if not args.no_browser:
        def _open_tab():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open_tab, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
