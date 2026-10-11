#!/usr/bin/env bash
# Runs the unit suite with temp files kept inside the repo (PLAN.md S0.2).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export TMPDIR="$ROOT/.tmp"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m pytest "$@"
