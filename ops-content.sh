#!/usr/bin/env bash
# Legacy alias → ergoms-vpn.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ergoms-vpn.sh" "$@"
