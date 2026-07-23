"""Entry point for ``python -m embodiment``."""

from __future__ import annotations

import sys

from embodiment.cli import main

if __name__ == "__main__":
    sys.exit(main())
