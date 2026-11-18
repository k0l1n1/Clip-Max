#!/usr/bin/env bash
# clip-watcher Studio Bash Launcher: ./web.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/AppData/Local/Programs/Python/Python312/Scripts:$HOME/AppData/Local/Programs/Python/Python312:$PATH"
export PYTHONUNBUFFERED=1

PYTHON_BIN=""
if [ -f "$LOCALAPPDATA/Programs/Python/Python312/python.exe" ]; then
    PYTHON_BIN="$LOCALAPPDATA/Programs/Python/Python312/python.exe"
elif [ -f "/c/Users/BIJU/AppData/Local/Programs/Python/Python312/python.exe" ]; then
    PYTHON_BIN="/c/Users/BIJU/AppData/Local/Programs/Python/Python312/python.exe"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

echo "Starting clip-watcher Studio at http://127.0.0.1:8000 ..."
exec "$PYTHON_BIN" "$SCRIPT_DIR/server.py" "$@"
