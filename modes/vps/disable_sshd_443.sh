#!/usr/bin/env bash
# Remove ops-content sshd :443 drop-in so sing-box (or another TLS service) can bind :443.
# Keeps sshd on :22 for provider console / out-of-band access.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

DROPIN=/etc/ssh/sshd_config.d/99-ops-content-443.conf

if [[ -f "$DROPIN" ]]; then
  rm -f "$DROPIN"
  echo "Removed $DROPIN"
else
  echo "No $DROPIN (nothing to remove)"
fi

# If someone put Port 443 into the main config, warn (do not edit blindly).
if grep -E '^[[:space:]]*Port[[:space:]]+443' /etc/ssh/sshd_config 2>/dev/null; then
  echo "WARNING: /etc/ssh/sshd_config still has 'Port 443' — remove it manually." >&2
fi

if systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null; then
  echo "sshd restarted"
else
  service ssh restart 2>/dev/null || service sshd restart 2>/dev/null || true
fi

echo "Listening:"
ss -lntp 2>/dev/null | grep -E ':443|:22' || netstat -lntp 2>/dev/null | grep -E ':443|:22' || true

if ss -lntp 2>/dev/null | grep -qE 'sshd.*:443|:443.*sshd'; then
  echo "ERROR: sshd still listening on :443" >&2
  exit 1
fi

echo "OK: sshd is not on :443 (use :22 from provider console)."
