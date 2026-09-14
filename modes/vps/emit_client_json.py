#!/usr/bin/env python3
"""Write client.json + creds/awg/*.conf from credentials.env / peers.json."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from awg_clients import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["emit"]))
