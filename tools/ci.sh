#!/usr/bin/env bash
# Local gate. Mirrors CI exactly — if this passes, CI passes.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pre-commit run --all-files
.venv/bin/pytest -v
