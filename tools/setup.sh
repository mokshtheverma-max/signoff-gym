#!/usr/bin/env bash
# One-time setup (PLAN.md section 9). Everything stays inside the repo:
# the venv in .venv, the uv cache in .cache, temp files in .tmp.
# Needs: Docker usable without sudo, and uv (https://docs.astral.sh/uv/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export UV_CACHE_DIR="$ROOT/.cache/uv" TMPDIR="$ROOT/.tmp"
mkdir -p "$TMPDIR"

IMAGE="$(python3 -c "import json; print(json.load(open('pins.json'))['orfs_image'])")"
docker image inspect "$IMAGE" > /dev/null 2>&1 || docker pull "$IMAGE"

[ -x .venv/bin/python ] || uv venv .venv --python "$(command -v python3)"
uv pip install --python .venv/bin/python -e . "pytest==9.1.1"
git config core.hooksPath .githooks

tools/test.sh
echo "setup done: run .venv/bin/sg --help"
