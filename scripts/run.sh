#!/bin/bash
# MeManga launcher for Linux/macOS

# Do not cd: relative paths in user arguments must resolve against the caller's directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use the venv interpreter by absolute path if it exists
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PY="$PROJECT_DIR/venv/bin/python"
else
    PY="python3"
fi

PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m memanga "$@"
