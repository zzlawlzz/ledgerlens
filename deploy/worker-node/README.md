# Remote worker node (T-031, gate G3)

Provision a clean Ubuntu VPS as a second LedgerLens compute node: it runs the
ReAct **worker** as an A2A service, reaches its tools on the main node over
MCP, and returns results to the orchestrator over A2A. The worker holds no
data — only the A2A token and the cloud LLM key.

Target node (Q-18): `104.238.24.196`, 1 vCPU / 957 MB RAM / Ubuntu 22.04,
Docker installed. SSH key: `~/.ssh/id_ed25519_worker_node`.

## Why WireGuard (not a public domain + Caddy)

The main node runs on the home machine behind NAT / Cloudflare Tunnel, so the
VPS cannot reach the main node's MCP endpoints over the public internet, and
exposing Postgres/MCP publicly would be unsafe. A **WireGuard mesh** gives
both machines private, mutually-reachable addresses and encrypts the link, so
A2A and MCP traffic never crosses the internet in the clear. That satisfies
the "TLS for non-localhost" requirement without a domain or certificates. The
`A2A_TOKEN` bearer check stays mandatory on top. (A Caddy + domain variant is
possible if a public A2A endpoint is ever wanted; WireGuard is the default.)

Addresses used below: main node `10.8.0.1`, VPS `10.8.0.2`.

## 1. WireGuard (both nodes)

```bash
# --- on BOTH nodes ---
sudo apt-get update && sudo apt-get install -y wireguard
wg genkey | tee privatekey | wg pubkey > publickey   # keep privatekey secret

# --- main node: /etc/wireguard/wg0.conf ---
# [Interface] Address=10.8.0.1/24  ListenPort=51820  PrivateKey=<main-priv>
# [Peer]      PublicKey=<vps-pub>  AllowedIPs=10.8.0.2/32
# (main node is behind NAT: no Endpoint here; the VPS dials in)

# --- VPS: /etc/wireguard/wg0.conf ---
# [Interface] Address=10.8.0.2/24  PrivateKey=<vps-priv>
# [Peer]      PublicKey=<main-pub> Endpoint=<main-public-ip-or-cloudflare>:51820
#             AllowedIPs=10.8.0.1/32  PersistentKeepalive=25

sudo systemctl enable --now wg-quick@wg0
# verify: from the VPS, `ping 10.8.0.1` and `curl http://10.8.0.1:8765/mcp`
```

The main node must bind its MCP services so the WireGuard interface can reach
them (they already listen on 0.0.0.0 inside compose; ensure the host firewall
allows 10.8.0.0/24 to 8765-8767, and that the compose ports publish on the
WG-reachable host).

## 2. Firewall (VPS)

```bash
sudo ufw default deny incoming
sudo ufw allow 22/tcp                 # SSH
sudo ufw allow 51820/udp              # WireGuard
sudo ufw enable
# 8081 is NOT opened publicly: the worker binds to 10.8.0.2 and is only
# reachable over the WireGuard tunnel.
```

## 3. Worker container

```bash
# docker is present; add the compose plugin (Q-18: not installed yet)
sudo apt-get install -y docker-compose-plugin

git clone https://github.com/zzlawlzz/ledgerlens.git && cd ledgerlens/deploy/worker-node
cp .env.worker.example .env.worker    # fill A2A_TOKEN (== main node), DEEPSEEK_API_KEY
docker compose -f compose.worker.yml up -d --build --wait
curl http://10.8.0.2:8081/healthz     # {"status":"ok","node":"vps-fi"}
```

## 4. Register the node with the orchestrator (main node)

Add the remote worker to `config/workers.yaml` and restart the app:

```yaml
workers:
  - name: worker-local
    url: ${WORKER_URL:local}
    skills: [financial_sql_analysis, narrative_rag_analysis, price_history_analysis]
  - name: worker-vps
    url: http://10.8.0.2:8081
    skills: [financial_sql_analysis, narrative_rag_analysis, price_history_analysis]
```

The dispatcher prefers the local worker on a skill tie and fails over to it
(with a trace warning) when the remote is unreachable — so losing the VPS
never breaks a run.

## 5. Acceptance (gate G3)

- A live UI question runs ≥1 step on the VPS: the step badge shows `vps-fi`,
  `steps.worker_node='vps-fi'` in the DB (screenshot in the PR).
- Stop the VPS worker → the same question is served by the local worker with
  a warning in the trace (failover).
- From a third network: the A2A endpoint answers 401 without the token and is
  unreachable over plain HTTP without WireGuard.
- This runbook reproduces from a bare VPS in ≤30 min (record the timing).

## Token rotation / revocation

Change `A2A_TOKEN` on the main node and in `.env.worker` on the VPS, then
restart both `app` (main) and `worker` (VPS). A leaked token is revoked the
moment the main node's value changes — the VPS worker then 401s until updated.
