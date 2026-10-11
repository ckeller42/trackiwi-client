"""`python -m trackiwi` runs the CLI straight from a clone, no install step.

Exercised through a subprocess so it covers the real module-execution path
(`__main__.py` → `cli.main` → `sys.exit`), which `import`-level tests cannot.
"""

import os
import subprocess
import sys


def test_a_dead_pipe_on_export_does_not_exit_120(tmp_path):
    """`trackiwi export --format csv | <reader that leaves>`: the buffered
    output's exit flush used to print a traceback and exit 120 (#37). With the
    fix the writer treats the dead pipe as success and prints nothing."""
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
    }
    seed = (
        "from trackiwi.store import Store\n"
        "with Store() as s:\n"
        "    s.upsert([(1,7,1700000000,1,31.0,-41.0,12,0.0,0,0,-71,9,98,4120)])\n"
    )
    subprocess.run([sys.executable, "-c", seed], env=env, check=True)
    cmd = f"set -o pipefail; {sys.executable} -m trackiwi export --format csv | true"
    result = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
    assert result.returncode != 120
    assert "Exception ignored" not in result.stderr


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
