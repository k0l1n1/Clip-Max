# clip-watcher 🎬⚡ (Twitch & YouTube Live)

Autonomous clip creator triggered by live chat velocity spikes, now supporting **both Twitch and YouTube Live**.

With a single command:
```bash
# In Git Bash / Linux / macOS
./watch.sh <twitch_channel_or_youtube_url>

# In Windows PowerShell
.\watch.ps1 <twitch_channel_or_youtube_url>

# In Windows CMD
watch.bat <twitch_channel_or_youtube_url>
```

Or launch the visual web dashboard:
```powershell
.\web.ps1
```

---

## ⚡ How It Works

1. **Live Recording (`streamlink`)**: Records the live broadcast (Twitch or YouTube) to `rec/<name>_<timestamp>.mp4`.
2. **Real-time Live Chat Listener**:
   - **Twitch**: Connects anonymously to Twitch IRC (`irc.chat.twitch.tv:6667`, `NICK justinfan12345`, no token required).
   - **YouTube**: Connects to the YouTube Live Chat stream via `pytchat` (no API key or OAuth needed).
3. **Spike Detection Engine**: Aggregates chat message counts in **10-second buckets**. A spike is detected when:
   $$\text{Bucket Messages} \ge 1.75 \times \text{Median(last 3 minutes)} \quad \text{AND} \quad \text{Bucket Messages} \ge \text{--min}$$
4. **9:16 Vertical Clip Cutter (`ffmpeg`)**: Automatically cuts **20 seconds before + 10 seconds after** (30s total) into `clips/`, center-cropped to vertical **9:16** format (ready for TikTok, YouTube Shorts, and Instagram Reels).
5. **Whisper Subtitles (`openai-whisper`)**: Transcribes the audio of each generated clip using OpenAI Whisper and writes synchronized `.srt` subtitles alongside the video.

---

## 🌐 Web Studio Dashboard

Open **[http://localhost:8000](http://localhost:8000)** in your browser:
- 📺 **Watch live streams** with embedded Twitch & YouTube players.
- 📈 **Real-time chat frequency timeline** tracking message velocity, rolling 3-min median, and spike thresholds.
- ✂️ **Manual "Cut Clip Now" button**: Instantly cut the last 30 seconds on demand.
- 📱 **Integrated 9:16 vertical video player**: Play clips directly in the browser and download `.mp4` and `.srt` subtitles.
- ⚙️ **One-click Start / Stop controls** for any Twitch or YouTube stream.

---

## 📦 Prerequisites & Installation

### 1. FFmpeg
Make sure FFmpeg and FFprobe are installed and available on your system path.
- **Windows**: `winget install Gyan.FFmpeg` or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
- **Linux / Ubuntu**: `sudo apt update && sudo apt install -y ffmpeg`
- **macOS**: `brew install ffmpeg`

### 2. Python Packages
Install the required Python packages:
```bash
pip install streamlink openai-whisper
```
*(Or install via `pip install -r requirements.txt`)*

---

## 🚀 Usage

### Basic Usage
```bash
# Linux / macOS / Git Bash
./watch.sh shroud

# Windows Command Prompt / PowerShell
watch.bat shroud
```

You can pass the channel name or the full Twitch URL:
```bash
./watch.sh https://twitch.tv/xqc
```

### CLI Options

| Option | Default | Description |
|---|---|---|
| `<channel>` | *(Required)* | Twitch username or channel URL |
| `--min <N>` | `5` | Minimum messages in a 10s bucket to qualify as a spike. |
| `--quality <quality>` | `best` | Streamlink stream quality (e.g. `best`, `1080p60`, `720p60`). |
| `--model <model>` | `base` | Whisper speech-to-text model (`tiny`, `base`, `small`, `medium`). |
| `--cooldown <sec>` | `30` | Cooldown period in seconds to prevent duplicate clips during prolonged hype bursts. |
| `--rec-dir <dir>` | `rec` | Destination directory for the full live recording. |
| `--clips-dir <dir>` | `clips` | Destination directory for the 9:16 clips and `.srt` subtitles. |
| `--no-whisper` | `false` | Disable Whisper subtitle generation (video clips only). |
| `--nick <nick>` | `justinfan12345` | Anonymous IRC nickname. |

### Examples

```bash
# Quiet chat threshold adjustment
./watch.sh smallstreamer --min 5

# High-traffic streamer with 720p60 recording
./watch.sh tarik --min 20 --quality 720p60

# Lightweight Whisper model for faster transcription on CPU
./watch.sh shroud --model tiny

# Video clips only (skip Whisper transcription)
./watch.sh shroud --no-whisper
```

---

## 💡 Important Tips

- **Quiet chats need `--min 5`**: On channels with lower viewership, chat baseline may be 0-1 messages. Setting `--min 5` ensures random single chatter messages don't trigger false positives.
- **Only works while live**: Twitch does not broadcast live HLS streams or active IRC chat when a streamer is offline.
- **Streamer permissions**: Only clip streamers who permit recording and clipping of their content.

---

## 🛠️ Verification & Testing

To run the automated unit test suite (testing bucket aggregation, rolling median, spike detection math, cooldown debouncing, 9:16 center-crop filter, and clip generation):

```bash
python test_clip_watcher.py
```
