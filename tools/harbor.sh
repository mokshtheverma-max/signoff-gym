#!/usr/bin/env bash
# Harbor, the runner of record (PLAN.md R2-H1): telemetry off, folders inside
# the repo, only `harbor run` with the oracle, nop or terminus-2 agent. The
# checks live in sg/harbor.py, which refuses upload, launch, registry, skill
# and remote options. The API key reaches Harbor from the environment, never
# from an option here.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HARBOR_TELEMETRY=off
exec "$ROOT/.venv/bin/python" -B -m sg.harbor "$@"
