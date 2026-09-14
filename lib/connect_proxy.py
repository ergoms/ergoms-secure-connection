#!/usr/bin/env python3
"""HTTP CONNECT shim for OpenSSH ProxyCommand through a corporate proxy.

Usage:
  ssh -o ProxyCommand="python connect_proxy.py %h %p" ...

Env:
  ERGOMS_SC_HTTP_PROXY   default: 192.0.2.10:3128
"""

from __future__ import annotations

import sys

try:
    from lib.connect import main as connect_main
except ImportError:
    from connect import main as connect_main


def main() -> int:
    return connect_main(sys.argv[1:], via="http")


if __name__ == "__main__":
    raise SystemExit(main())
