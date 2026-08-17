#!/usr/bin/env bash
# Cross-platform launcher for PeerDrop LAN (Linux/macOS).
# On Windows, use run.bat instead.
set -e

cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    exec python3 app.py
elif command -v python >/dev/null 2>&1; then
    exec python app.py
else
    echo "Python 3 is required to run PeerDrop LAN." >&2
    exit 1
fi
