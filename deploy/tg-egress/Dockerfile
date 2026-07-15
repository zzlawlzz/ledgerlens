# SSH-SOCKS egress sidecar for demo Telegram notifications (T-044).
#
# Maintains a SOCKS5 proxy on :1080 that tunnels outbound traffic through the FI
# VPS, so the demo can reach api.telegram.org (blocked from RU IPs) for owner
# notifications. Nothing is installed or changed on the VPS — this is plain
# outbound `ssh -D` dynamic forwarding, exactly like the reverse API tunnel.
FROM alpine:3.20
RUN apk add --no-cache openssh-client
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
