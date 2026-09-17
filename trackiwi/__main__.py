"""Entry point for `python -m trackiwi`.

Lets the tool run straight from a clone with no install step — the payoff of
being pure standard library. Behaviour is identical to the `trackiwi` console
script: both call `trackiwi.cli.main`.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
