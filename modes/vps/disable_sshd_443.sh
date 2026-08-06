#!/usr/bin/env bash
# Remove ops-content sshd :443 drop-in so sing-box (or another TLS service) can bind :443.
# Keeps sshd on :22 for provider console / out-of-band access.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

# Canonical name uses hyphen; older hosts may have a dotted variant.
DROPINS=(
  /etc/ssh/sshd_config.d/99-ops-content-443.conf
  /etc/ssh/sshd_config.d/99.ops-content-443.conf
)

removed=0
for DROPIN in "${DROPINS[@]}"; do
  if [[ -f "$DROPIN" ]]; then
    rm -f "$DROPIN"
    echo "Removed $DROPIN"
    removed=1
  fi
done
if [[ "$removed" -eq 0 ]]; then
  echo "No ops-content :443 drop-in (nothing to remove)"
fi

# If someone put Port 443 into the main config, warn (do not edit blindly).
if grep -E '^[[:space:]]*Port[[:space:]]+443' /etc/ssh/sshd_config 2>/dev/null; then
  echo "WARNING: /etc/ssh/sshd_config still has 'Port 443' — remove it manually." >&2
fi

# Ubuntu socket-activated ssh keeps ListenStream in a generator drop-in;
# restarting only ssh.service leaves :443 bound until daemon-reload + socket restart.
if systemctl daemon-reload 2>/dev/null; then
  if systemctl restart ssh.socket ssh.service 2>/dev/null \
    || systemctl restart sshd.socket sshd.service 2>/dev/null \
    || systemctl restart ssh.service 2>/dev/null \
    || systemctl restart sshd.service 2>/dev/null; then
    echo "sshd restarted (socket + service)"
  else
    service ssh restart 2>/dev/null || service sshd restart 2>/dev/null || true
  fi
elif systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null; then
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
