#!/usr/bin/env bash
# Local gate. Mirrors CI exactly — if this passes, CI passes.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pre-commit run --all-files
# Static type gate (strict; config in pyproject.toml). Run as a direct `mypy`
# invocation rather than a pre-commit hook: the package is installed in this
# venv, so mypy resolves the real `trackiwi`/`tools` sources and reads the
# `[tool.mypy]` config, which mirrors-mypy's isolated environment cannot do
# reliably. It checks the package, tools and tests in one pass.
.venv/bin/mypy
.venv/bin/pytest -v --cov=trackiwi --cov-report=term-missing --cov-fail-under=95
# Doctests on the pure functions only. `--doctest-modules` runs each module's
# docstring examples in that module's own namespace (unlike `sphinx-build -b
# doctest`, whose per-block namespaces would need explicit testsetup imports).
# Examples live only on side-effect-free functions, so no network/FS code runs.
.venv/bin/pytest --doctest-modules trackiwi -q
# Requirements-traceability gate: `-W` turns "a requirement with no verifying
# test" (and any unresolved `:need:` reference) into a non-zero exit.
.venv/bin/sphinx-build -b html -W docs docs/_build/html
# Docstring-coverage gate (config + threshold in pyproject.toml).
.venv/bin/interrogate trackiwi
