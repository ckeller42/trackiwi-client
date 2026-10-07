"""Sphinx configuration for the trackiwi-client documentation site.

The site has four groups (getting started, how-to guides, reference,
explanation), plus a contributing group. Its architecture page draws Mermaid
diagrams (client-side mermaid.js, no server) from Markdown via myst-parser.

The build also has one job the README cannot do: prove that every project
requirement is verified by a test. Requirements live as ``sphinx-needs`` ``req``
objects (``requirements.rst``); each implementing function's docstring references
the requirement it satisfies with the ``:need:`` role; and each requirement is
traced to its verifying test via a ``test`` need that ``:verifies:`` it
(``traceability.rst``). The ``req_without_test`` warning below turns "a
requirement with no verifying test" into a build warning, and ``sphinx-build -W``
turns that warning into a non-zero exit — so CI fails on an untraced requirement.

Zero runtime dependencies still holds: sphinx, sphinx-needs, myst-parser,
sphinxcontrib-mermaid and interrogate are dev-only tooling and the package
itself imports stdlib only.
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
    "myst_parser",  # the Markdown pages (architecture, how-tos, reference)
    "sphinxcontrib.mermaid",  # C4-styled diagrams; rendered in the browser
    "sphinx_needs",
    "sphinx_likec4",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
]

# Markdown: ```mermaid fences become the mermaid directive; headings get anchors
# down to ### so pages can link to a section.
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 3

# Local-only design records (gitignored) and build output are not site pages.
exclude_patterns = ["_build", "superpowers/**"]

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

likec4_source_dir = "model"
