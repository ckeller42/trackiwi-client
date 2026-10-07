"""The Component diagram in docs/architecture.rst must match the real imports.

A diagram that no longer matches the code is worse than none. The arrows are the
package's intra-package imports, the `python -m trackiwi` entry point included; the
shared contracts in `trackiwi/__init__.py` are deliberately not drawn (the page says so).
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _diagram():
    text = (ROOT / "docs" / "architecture.rst").read_text(encoding="utf-8")
    block = text.split("C4Component", 1)[1].split(".. mermaid::", 1)[0]
    labels = dict(re.findall(r'Component\((\w+), "([^"]+)"', block))
    edges = {(labels[a], labels[b]) for a, b in re.findall(r"Rel\((\w+), (\w+),", block)}
    return set(labels.values()), edges


def _package():
    modules = {p.stem for p in (ROOT / "trackiwi").glob("*.py")} - {"__init__"}
    edges = set()
    for name in modules:
        tree = ast.parse((ROOT / "trackiwi" / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                targets = [node.module] if node.module else [a.name for a in node.names]
                edges |= {(name, target) for target in targets if target in modules}
    return modules, edges


def test_the_component_diagram_shows_every_module():
    drawn, _ = _diagram()
    modules, _ = _package()
    assert drawn == modules


def test_the_component_diagram_arrows_are_the_real_imports():
    _, drawn = _diagram()
    _, real = _package()
    assert drawn == real, f"only in the diagram: {drawn - real}; only in the code: {real - drawn}"
