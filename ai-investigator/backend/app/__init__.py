"""BugZero investigator package.

Loads .env BEFORE any submodule is imported. Several modules (logsearch,
codeindex, store, ssh_collector) read os.environ at module import time to build
paths and DSNs, so if the dotenv load happens later — inside a request — those
constants are already frozen with the wrong values and quietly point at the
wrong log root or database.
"""

import os

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _load_env() -> None:
    """Populate os.environ from .env. Existing env vars always win."""
    if not os.path.exists(ENV_PATH):
        return
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                # A real environment variable must not be clobbered by the file.
                if key and key not in os.environ and value:
                    os.environ[key] = value
    except OSError:
        pass


_load_env()