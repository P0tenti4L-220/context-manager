"""Allow ``python -m context_manager`` to share the console entry point."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())