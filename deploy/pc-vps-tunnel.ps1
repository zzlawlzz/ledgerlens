# Persistent reverse tunnel (Phase 3): expose this workstation's orchestrator
# (localhost:8000) on the VPS as localhost:18000, so the VPS nginx can reverse-
# proxy the public API to it. The PC is behind home NAT, so it dials out. SSH
# keepalives detect a dead link; the loop reconnects after a short pause.
# Registered as a scheduled task (LedgerLens-VPS-Tunnel) to run at logon.
$key = "$env:USERPROFILE\.ssh\id_ed25519_worker_node"
$vps = "root@104.238.24.196"
while ($true) {
    ssh -N -R 18000:localhost:8000 `
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes `
        -o StrictHostKeyChecking=no -o BatchMode=yes `
        -i $key $vps
    # Wait before reconnecting. Long enough that if the drop was unclean, the
    # VPS sshd (ClientAliveInterval) has released the stale :18000 forward, so
    # the next -R binds instead of fail-looping and hammering both ends.
    Start-Sleep -Seconds 20
}
