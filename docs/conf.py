"""Sphinx configuration for the trackiwi-client requirements-traceability build.

This build has one job the README cannot do: prove that every project
requirement is verified by a test. Requirements live as ``sphinx-needs`` ``req``
objects (``requirements.rst``); each implementing function's docstring references
the requirement it satisfies with the ``:need:`` role; and each requirement is
traced to its verifying test via a ``test`` need that ``:verifies:`` it
(``traceability.rst``). The ``req_without_test`` warning below turns "a
requirement with no verifying test" into a build warning, and ``sphinx-build -W``
turns that warning into a non-zero exit — so CI fails on an untraced requirement.

Zero runtime dependencies still holds: sphinx, sphinx-needs and interrogate are
dev-only tooling and the package itself imports stdlib only.
"""

from __future__ import annotations

import os
import sys

# autodoc imports the package, so it must be importable from the docs build.
sys.path.insert(0, os.path.abspath(".."))

project = "trackiwi-client"
author = "Christoph Keller"
project_copyright = "2026, Christoph Keller"

extensions = [
    "sphinx_needs",
    "sphinxcontrib.mermaid",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
]

# Furo (dev-only docs dependency, pinned in the dev extra) — same theme as open-california.
html_theme = "furo"
html_static_path = ["_static"]
html_css_files = ["diagram-card.css"]

# nitpicky is deliberately OFF. Under `-W` it would promote every unresolved
# autodoc cross-reference to stdlib types (pathlib.Path, sqlite3.Row,
# argparse.Namespace, ...) into a build failure that has nothing to do with
# requirements traceability. `-W` on its own still fails the build on the one
# warning class this build exists to catch: an unresolved `:need:` reference
# and the `needs_warnings` rule below.
nitpicky = False

# Keep autodoc quiet and deterministic.
autodoc_member_order = "bysource"
add_module_names = False

# --- sphinx-needs (pinned 8.5.0) -------------------------------------------
#
# `verifies` is a custom link type. A `test` need with `:verifies: REQ_X` puts
# an outgoing "verifies" link on the test and, because sphinx-needs mirrors
# every link, an incoming link on REQ_X exposed on the field `verifies_back`.
# The `req_without_test` rule below filters on exactly that field.
#
# `needs_links` (not the deprecated `needs_extra_links`) is the current option
# in the pinned sphinx-needs 8.5.0 — under `-W` the deprecation warning would
# otherwise fail this build. The schema is unchanged.
needs_links = {
    "verifies": {
        "incoming": "is verified by",
        "outgoing": "verifies",
    }
}

# A requirement with an empty `verifies_back` has no test pointing at it. The
# filter MATCHING a need is what raises the warning, so this selects precisely
# the untraced requirements. `sphinx-build -W` then makes that warning fatal.
needs_warnings = {
    "req_without_test": "type == 'req' and not verifies_back",
}

# Always evaluate the warning rules and log which needs failed, even on a build
# with no other needs warnings — otherwise the check can be skipped silently.
needs_warnings_always_warn = True
