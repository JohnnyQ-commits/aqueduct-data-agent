"""Allow running as `python -m aqueduct`."""

import sys

from .cli.main import main

sys.exit(main())
