"""Enable `python -m coldpath ...` in addition to the `coldpath` console script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
