#!/usr/bin/env bash
# clip-watcher launcher: one command ./watch.sh <twitch_channel> [options]

set -e

# Search candidate python paths, prioritizing real python installations over WindowsApps aliases
CANDIDATES=(
    "$LOCALAPPDATA/Programs/Python/Python312/python.exe"
    "/c/Users/BIJU/AppData/Local/Programs/Python/Python312/python.exe"
    "C:/Users/BIJU/AppData/Local/Programs/Python/Python312/python.exe"
    "python3"
    "py"
    "python"
)

PYTHON_BIN=""
for cand in "${CANDIDATES[@]}"; do
    if [ -n "$cand" ]; then
        # Check if binary exists and can actually execute code (bypassing WindowsApp dummy aliases)
        if "$cand" -c "import sys; exit(0)" >/dev/null 2>&1; then
            PYTHON_BIN="$cand"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "[Error] Python 3 not found in PATH or standard directories."
    exit 1
fi

# Ensure Python Scripts directory is on PATH for streamlink / whisper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/AppData/Local/Programs/Python/Python312/Scripts:$HOME/AppData/Local/Programs/Python/Python312:$PATH"
export PYTHONUNBUFFERED=1

if [ -z "$1" ]; then
    echo "Usage: ./watch.sh <twitch_channel> [options]"
    echo ""
    echo "Examples:"
    echo "  ./watch.sh shroud"
    echo "  ./watch.sh xqc --min 5"
    echo "  ./watch.sh tarik --min 10 --quality 720p60 --model base"
    echo ""
    echo "Tips:"
    echo "  - Quiet chats need --min 5"
    echo "  - It only works while the channel is live"
    echo "  - Clip streamers who allow it"
    exit 1
fi

exec $PYTHON_BIN "$SCRIPT_DIR/clip_watcher.py" "$@"
