#!/usr/bin/env bash
# Launcher for macOS/Linux (development). Windows users: double-click start.bat.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python -m salesbot
