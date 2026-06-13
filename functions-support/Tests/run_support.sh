#!/usr/bin/env bash
# Blueboot Support — bash launcher
# Run from anywhere: bash functions-support/Tests/run_support.sh --stats

set -euo pipefail

# cd to functions-support/ (one level up from Tests/)
cd "$(dirname "$0")/.."

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "[ERROR] No virtual environment found in functions-support/.venv or functions-support/venv"
    echo "Run: cd functions-support && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

python Tests/run_support.py "$@"
