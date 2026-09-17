"""`python -m trackiwi` runs the CLI straight from a clone, no install step.

Exercised through a subprocess so it covers the real module-execution path
(`__main__.py` → `cli.main` → `sys.exit`), which `import`-level tests cannot.
"""

import subprocess
import sys


def _run(args):
    return subprocess.run(
        [sys.executable, "-m", "trackiwi", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_module_help_exits_zero_and_prints_usage():
    result = _run(["--help"])
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_module_bad_subcommand_exits_nonzero():
    result = _run(["definitely-not-a-command"])
    assert result.returncode != 0
