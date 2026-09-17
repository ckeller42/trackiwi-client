"""Project-wide invariants that back the traceability requirements.

These verify requirements the rest of the suite did not already pin directly:
zero runtime dependencies (REQ_ZERO_DEPS), the server command header being
ignored (REQ_IGNORE_APP_COMMAND), and the one and only state-changing request
being ``DELETE /api/v2/session`` (REQ_READONLY).
"""

import ast
import pathlib
import sys

from conftest import FakeOpener, FakeResponse

import trackiwi
from trackiwi.client import Client


def _top_level_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_package_imports_only_stdlib():
    """Every top-level import in the package resolves to the stdlib (REQ_ZERO_DEPS)."""
    package_dir = pathlib.Path(trackiwi.__file__).parent
    allowed = set(sys.stdlib_module_names) | {"trackiwi"}
    offenders = {}
    for module in sorted(package_dir.glob("*.py")):
        third_party = _top_level_imports(module) - allowed
        if third_party:
            offenders[module.name] = sorted(third_party)
    assert not offenders, f"non-stdlib imports found: {offenders}"


def test_app_command_response_header_is_ignored():
    """A `trackiwi-app-command` response header is returned unread (REQ_IGNORE_APP_COMMAND)."""
    opener = FakeOpener(
        FakeResponse(
            b'[{"id":7,"name":"Bus"}]',
            headers={"trackiwi-app-command": "wipe_local_cache"},
        )
    )
    client = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    # The command is never executed: the call simply returns the parsed body.
    assert client.trackers() == [{"id": 7, "name": "Bus"}]


def test_logout_uses_only_the_session_delete(tmp_path, monkeypatch):
    """The only state-changing request is DELETE /api/v2/session (REQ_READONLY)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    opener = FakeOpener(FakeResponse(b"", status=200))
    client = Client(api_base="https://api.example.invalid", token="tok", user_id=1, opener=opener)
    assert client.logout() is True
    assert len(opener.calls) == 1
    request = opener.calls[0]
    assert request.method == "DELETE"
    assert request.full_url == "https://api.example.invalid/api/v2/session"
