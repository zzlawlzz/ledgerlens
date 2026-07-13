# Remote worker node (T-031, gate G3) — as-built runbook

The second LedgerLens compute node runs the ReAct **worker** as an A2A service on
a VPS; its tools live on the home node and are reached over MCP through an
encrypted AmneziaWG tunnel. The worker holds no data — only the A2A token and the
cloud LLM key.

**Live topology (verified 2026-07-13):**
- **Home node** = EPYC (`192.168.1.115`, behind NAT/DPI): runs the orchestrator +
  Postgres/Qdrant/MCP (the `lldemo` demo stack), AmneziaWG address **10.9.0.1**.
- **VPS** = `104.238.24.196` (Ubuntu 22.04, KVM, 1 vCPU / 957 MB): runs only the
  worker container, AmneziaWG address **10.9.0.2**. SSH key `~/.ssh/id_ed25519_worker_node`.
- Mesh subnet **10.9.0.0/24**, AmneziaWG UDP port **51821** (the node's own
  Amnezia VPN uses 45332 — kept separate).

## Why AmneziaWG in userspace

AmneziaWG (WireGuard + DPI-resistant obfuscation) survives the RU home uplink's
DPI. The **kernel DKMS module fails to build** on both nodes' kernels, so the
tunnel runs the **userspace** implementation: `awg-quick` with
`WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go`. Private/RFC-1918 addresses mean
plain http rides an already-encrypted link, satisfying the "TLS for non-localhost"
rule; the `A2A_TOKEN` bearer check stays mandatory on top.

## 1. AmneziaWG tunnel (both nodes, userspace)

Install the tools (`awg`, `awg-quick`) + the `amneziawg-go` userspace binary. The
`amneziawg-tools` apt package installs even when the DKMS module fails; grab
`amneziawg-go` from an existing amnezia container (`docker cp <c>:/usr/bin/amneziawg-go
/usr/local/bin/`) or copy all three binaries from a node that has them.

```bash
awg genkey | tee node.key | awg pubkey > node.pub    # on each node
```

Config `/etc/amnezia/amneziawg/awgll.conf` — identical `Jc/Jmin/Jmax/S1/S2/H1..H4`
obfuscation block on both peers:

```ini
# --- VPS (server, public endpoint) ---
[Interface]
Address = 10.9.0.2/24
ListenPort = 51821
PrivateKey = <vps-priv>
Jc = 4
Jmin = 40
Jmax = 70
S1 = 66
S2 = 56
# H1..H4: pick four DISTINCT random integers > 4 (these are placeholders — the
# live tunnel uses its own set; the values only need to match on both peers).
H1 = 1111111111
H2 = 2222222222
H3 = 3333333333
H4 = 4444444444
[Peer]                       # the home node dials in (it is behind NAT)
PublicKey = <epyc-pub>
AllowedIPs = 10.9.0.1/32

# --- home node (client) ---
[Interface]
Address = 10.9.0.1/24
PrivateKey = <epyc-priv>
# ... identical Jc..H4 block ...
[Peer]
PublicKey = <vps-pub>
Endpoint = 104.238.24.196:51821
AllowedIPs = 10.9.0.2/32
PersistentKeepalive = 25
```

Open UDP 51821 on the VPS (`iptables -I INPUT -p udp --dport 51821 -j ACCEPT`),
then bring the tunnel up on both:

```bash
WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go awg-quick up awgll
# verify: from the VPS `curl http://10.9.0.1:8767/mcp` (406 = MCP reachable);
# handshake shows under `awg show awgll`.
```

TCP works both ways over the tunnel (A2A/MCP is HTTP; ICMP may be asymmetric in
userspace — a red herring). Persist with a systemd unit `awg-ll.service`
(`Type=oneshot`, `RemainAfterExit=yes`, the `WG_QUICK_USERSPACE_IMPLEMENTATION`
env, `ExecStart=awg-quick up awgll`) — **enabled on both nodes** so the tunnel
returns after a reboot.

## 2. Worker container (VPS)

The worker uses the same `ledgerlens` image as the app (no models — rag goes over
MCP). The VPS is too small to build; transfer the image from the home node
(`docker save lldemo-worker:latest | gzip` → relay → `docker load`). Then:

```bash
docker run -d --name ll-worker --restart unless-stopped \
  -p 10.9.0.2:8081:8081 --memory 512m \
  --entrypoint /app/.venv/bin/uvicorn \
  -e A2A_TOKEN=<same as home node> -e DEEPSEEK_API_KEY=<key> \
  -e WORKER_NODE_NAME=vps-fi -e LOCAL_MODEL= \
  -e WORKER_BASE_URL=http://10.9.0.2:8081 \
  -e MCP_SQL_URL=http://10.9.0.1:8765/mcp \
  -e MCP_RAG_URL=http://10.9.0.1:8766/mcp \
  -e MCP_ENRICH_URL=http://10.9.0.1:8767/mcp \
  lldemo-worker:latest \
  workers.a2a_server:app --host 0.0.0.0 --port 8081
```

Two things matter: `--entrypoint /app/.venv/bin/uvicorn` **bypasses the image's
DB-migration entrypoint** (the worker needs no database), and `-p 10.9.0.2:8081`
binds the A2A port to the **tunnel address only** — never the public IP.

## 3. Home node: expose MCP on the tunnel + register the worker

Bring the demo stack up with the two extra overlays:

```bash
docker compose -p lldemo -f docker-compose.yml \
  -f deploy/demo/docker-compose.demo.yml -f deploy/demo/compose.epyc.yml \
  -f deploy/worker-node/compose.mcp-awg.yml \
  -f deploy/worker-node/compose.orchestrator-vps.yml up -d --force-recreate mcp-sql mcp-rag mcp-enrich app
```

- `compose.mcp-awg.yml` publishes the MCP servers on `10.9.0.1:8765-8767` only.
- `compose.orchestrator-vps.yml` mounts `workers.vps.yaml` over the baked-in
  single-node registry (no rebuild), adding `worker-vps` at `http://10.9.0.2:8081`.

The dispatcher round-robins the two skill-matching workers and fails over to the
local one if the VPS is unreachable (`orchestrator/graph.py`, Q-20).

## 4. Acceptance (gate G3) — verified live 2026-07-13

- **Distributed step:** "Compare Apple and NVIDIA FY2025 revenue" → `step_2` ran on
  `worker_node=vps-fi` and returned NVIDIA $130,497,000,000 (the VPS worker reached
  the home MCP over the tunnel). ✅
- **Failover:** `docker stop ll-worker` → the same question still succeeded on the
  local worker, with a `worker_failover` / `degradation=worker_unreachable` trace. ✅
- **Security:** the public `104.238.24.196:8081` is unreachable (HTTP 000 — the
  worker binds the tunnel IP only); the A2A endpoint returns **401 without the
  token** (the agent card is public, 200). ✅

## Token rotation / revocation

Change `A2A_TOKEN` on the home node and the VPS `docker run`, then restart both the
`app` (home) and `ll-worker` (VPS). A leaked token is revoked the moment the home
node's value changes — the VPS worker then 401s until updated.
