#!/usr/bin/env python3
"""SSH ProxyCommand via local SOCKS5 (ERGOMS SECURE CONNECTION :1080).

Usage:
  ssh -o ProxyCommand="python connect_socks.py %h %p" ...

Env:
  ERGOMS_SC_SOCKS   default: 127.0.0.1:1080
"""

from __future__ import annotations

import sys

try:
    from lib.connect import main as connect_main
except ImportError:
    from connect import main as connect_main


def main() -> int:
    return connect_main(sys.argv[1:], via="socks")


if __name__ == "__main__":
    raise SystemExit(main())
