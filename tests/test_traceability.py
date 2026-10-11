"""Enforce the requirements <-> tests traceability in code, not just in Sphinx.

The sphinx-needs gate only checks that each ``req`` has an incoming
``verifies`` link; the ``tests/...::name`` node ids inside a ``test`` need are
free text to Sphinx, so a renamed or deleted test leaves the build green
(#29, and exactly what slipped past the build on PR #83). These checks run in
the normal test job and fail the moment a citation drifts.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "docs" / "requirements.rst"
TRACEABILITY = ROOT / "docs" / "traceability.rst"

_NODE_ID = re.compile(r"``(tests/[\w/]+\.py::\w+)``")


def _requirement_ids() -> set[str]:
    return set(re.findall(r"^\s*:id:\s*(REQ_\w+)\s*$", REQUIREMENTS.read_text(), re.M))


def _verifies() -> dict[str, list[str]]:
    """Each REQ_ id -> the test node ids cited for it in traceability.rst."""
    blocks = re.split(r"^\.\. test::", TRACEABILITY.read_text(), flags=re.M)[1:]
    mapping: dict[str, list[str]] = {}
    for block in blocks:
        node_ids = _NODE_ID.findall(block)
        for req in re.findall(r":verifies:\s*(REQ_\w+)", block):
            mapping.setdefault(req, []).extend(node_ids)
    return mapping


def test_every_requirement_has_a_verifying_test():
    missing = _requirement_ids() - set(_verifies())
    assert not missing, f"requirements with no verifying test: {sorted(missing)}"


def test_every_verifies_target_is_a_real_requirement():
    unknown = set(_verifies()) - _requirement_ids()
    assert not unknown, f"traceability verifies requirements that do not exist: {sorted(unknown)}"


def test_every_cited_test_node_id_resolves_to_a_real_test():
    missing = []
    for node_ids in _verifies().values():
        for node_id in node_ids:
            rel, name = node_id.split("::")
            path = ROOT / rel
            found = path.exists() and re.search(
                rf"^\s*def {re.escape(name)}\(", path.read_text(), re.M
            )
            if not found:
                missing.append(node_id)
    assert not missing, f"traceability cites tests that do not exist: {sorted(set(missing))}"


def test_every_requirement_a_source_docstring_claims_exists():
    referenced: set[str] = set()
    for path in sorted((ROOT / "trackiwi").glob("*.py")):
        referenced |= set(re.findall(r"REQ_\w+", path.read_text()))
    unknown = referenced - _requirement_ids()
    assert not unknown, f"source files reference unknown requirements: {sorted(unknown)}"
