# Migration: backend EPYC → workstation; VPS as thin public door (2026-07)

## Decision

Retire the EPYC home node from LedgerLens. Its uplink is the single root of a
whole class of failures seen repeatedly — connect-blackholes to cloud APIs,
Cloudflare-tunnel truncation of large responses, and unlogged hard freezes. The
cause is that node's line, **not** the region: the workstation on the same
project has good wired internet and none of these symptoms.

New topology:

```
user → GitHub Pages (frontend + site) → API → VPS (public door) → tunnel → workstation (backend)
```

- **Workstation** (this Windows PC — good wired internet, RX 6900XT, E: disk,
  Docker) = **full backend**: Postgres + Qdrant (data on E:), MCP tools,
  embeddings, orchestrator + workers, local GPU LLM tier.
- **VPS** (small, public IP) = **thin public door**: reverse-tunnel endpoint +
  nginx reverse-proxy to the workstation. The PC dials out (it is behind home
  NAT); no chatty logic on the VPS.
- **GitHub Pages** = frontend (`ledgerlens.space/app/`) + presentation site —
  unchanged, reliable. The frontend calls the API at the VPS address.
- **LLM tiering** (router already supports it, T-016/T-017): local GPU (6900XT
  via Ollama) for the light, frequent task classes (route/extract/guard);
  DeepSeek cloud for the strong ones (plan/synthesize/judge). Good wired internet
  makes the cloud tier reliable from here.

### Why orchestrator on the workstation, not the VPS

One run makes dozens of orchestrator↔LLM/MCP/DB calls. Keeping the orchestrator
next to the data and the LLM makes those local (fast, robust); only the public
API/SSE crosses the tunnel. Putting the orchestrator on the VPS would push every
one of those calls back across the tunnel, and the small VPS cannot host the data
or embeddings anyway.

## Phases (keep the current EPYC demo up until cutover)

1. **Backend on the workstation** (parallel to EPYC): move the Docker disk image
   to E: (Docker Desktop → Settings → Resources → Advanced → Disk image location)
   so volumes are fast and on the spacious disk; `docker compose` the stack up;
   ingest the corpus from EDGAR (good internet reaches sec.gov) or transfer the
   EPYC volumes; local smoke test (numbers from DB, RAG citations).
2. **Local GPU LLM**: install Ollama (Windows, ROCm — 6900XT is gfx1030, supported);
   pull a model sized to 16 GB VRAM; wire the router `local` tier (OLLAMA_BASE_URL,
   model); re-run the inference benchmark (T-037) on GPU; set which task classes
   route local vs cloud.
3. **VPS public door**: PC→VPS reverse tunnel (WireGuard / frp / SSH -R — pick one);
   VPS nginx reverse-proxy of the API hostname → tunnel → workstation orchestrator;
   TLS (Let's Encrypt on the VPS, or Cloudflare in front).
4. **Cutover**: point the API DNS at the VPS; set the frontend `VITE_API_BASE_URL`
   to the VPS address and redeploy Pages; verify end-to-end (Pages → VPS → PC);
   stop the EPYC demo stack.
5. **Eval / CI + docs**: move the self-hosted eval runner to the workstation;
   update ARCHITECTURE.md / README / deploy runbooks to the new topology; retire
   the EPYC-specific artifacts.

## Status (2026-07-15) — demo LIVE on the new stack

- **Phase 1 (backend on workstation): done.** Docker disk moved to E:; the stack
  runs here; corpus survived the move (4546 facts, no re-ingest); DeepSeek is
  clean and fast from this PC's wired link (5/5 ~1s) — the whole EPYC egress class
  of failures was that node's line, not the region. Local `local` tier disabled
  for now (dead CPU-ollama); DeepSeek-only run = 22s clean.
- **Phase 2 (GPU LLM): deferred** — DeepSeek is reliable from here, so not urgent.
- **Phase 3 (VPS door): done.** `deploy/pc-vps-tunnel.ps1` holds an SSH -R from the
  PC (:8000) to the VPS (:18000); VPS nginx reverse-proxies :80 and :443 (self-
  signed origin cert — CF zone is Full, accepts it) to it. CORS_ALLOW_ORIGINS set
  on the PC. Persistence: Startup-folder shim + VPS sshd ClientAliveInterval so
  stale forwards clear.
- **Phase 4 (cutover): done.** `api.ledgerlens.space` (CF-proxied → VPS) is the API;
  the Pages frontend (site.yml VITE_API_BASE_URL) points at it. Verified end-to-end:
  browser → Pages → CF → VPS:443 → tunnel → workstation → **full run in 18s**, correct
  answer, 0 errors (vs 60–150s with breaks on EPYC).
- **Phase 5 (retire EPYC + docs): pending.**

## Retires

EPYC demo stack, EPYC self-hosted GitHub runner, the AmneziaWG mesh (EPYC↔VPS),
and the Cloudflare tunnel to the EPYC. The EPYC stays as the owner's trading-bot
host — out of LedgerLens scope.

## Open items to decide during execution

- **Reverse-tunnel mechanism** (WireGuard vs frp vs SSH -R vs cloudflared-from-PC).
- **VPS choice** (the 957 MB worker VPS is enough for nginx + tunnel; confirm or
  pick a bigger one).
- **Local model** (e.g. qwen2.5:14b vs 7b) — decided by the GPU benchmark.
- **Workstation-as-server** posture (kept on when the demo should be reachable;
  Docker autostart; the PC↔VPS tunnel as a persistent service).
