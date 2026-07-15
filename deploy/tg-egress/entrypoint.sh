#!/bin/sh
# Keep a SOCKS5 proxy on 0.0.0.0:1080 alive, egressing via the FI VPS. The app
# reaches it as socks5h://tg-egress:1080 for Telegram delivery only (T-044).
set -eu
: "${FI_SSH_TARGET:?FI_SSH_TARGET is required, e.g. root@104.238.24.196}"

# The bind-mounted key is world-readable on a Windows host; ssh refuses that, so
# copy it to a private 0600 path before use.
mkdir -p /root/.ssh
cp /key /root/.ssh/id
chmod 600 /root/.ssh/id

while true; do
  ssh -N -D 0.0.0.0:1080 \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/root/.ssh/known_hosts \
    -i /root/.ssh/id "$FI_SSH_TARGET" || true
  echo "tg-egress: ssh tunnel dropped, reconnecting in 10s" >&2
  sleep 10
done
