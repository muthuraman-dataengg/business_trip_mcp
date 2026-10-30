"""`python -m copilot ...` works even before `pip install` (same as the `trip-copilot` command)."""

from .cli import main

raise SystemExit(main())
