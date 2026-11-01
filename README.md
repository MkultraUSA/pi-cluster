# pi-cluster — self-patching image pipeline (M4 Track B)

Advisory-driven container patching for the 4-node Raspberry Pi k3s cluster.

**Loop:** CVE intelligence (trivy DB, auto-updated) → scan images in the
in-cluster registry → kaniko rebuild with Debian security fixes baked in
(`apt -y upgrade` at build) → canary node → health gate → fleet → evidence.

## Components

| File | What |
|---|---|
| `registry.yaml` | registry:2.8.3 + PVC + NodePort 30500 (LAN-only) |
| `build-v1.yaml` / `build-v2.yaml` | kaniko Jobs (in-cluster, arm64, gcr.io mirror — ghcr is 403-blocked on this network) |
| `scan-v1.yaml` | trivy 0.58.2 scan Job — DB via `public.ecr.aws/aquasecurity/trivy-db` mirror |
| `scan-cronjob.yaml` | every 6h: scans all `patch-demo` tags → JSON to the registry PVC |
| `status-page.yaml` + `page-server.py` | LAN status page (NodePort 30800) rendering live scan JSON; verdict logic: fixable>0 → REBUILD+REPLACE DUE |
| `node-registries-canary.yaml` | the one-line containerd trust file (canary: cluster2) |

## First drill (2026-11-01)

- `patch-demo:v1` (plain `python:3.11-slim-bookworm`): 55 HIGH/CRITICAL,
  **0 fixable upstream** — fresh 2026 CVEs awaiting Debian patches.
- `:v2` (security upgrade baked): only tzdata moved; still 55 — correct
  verdict: **unpatchable-upstream, track**. The scan flips entries to
  FIXABLE when Debian ships; rebuild+replace clears them then.
- Canary pull verified on cluster2 (`registries.yaml` + k3s-agent restart).

## Guardrails (from PLAN.md)

- $0 recurring cloud cost; nothing exposed off-LAN.
- Replace = canary first, health gate (smoke + workload consistency),
  `rollout undo` on failure.
- k3s upgrades (Track C) stay manual-gated.
- Evidence per patch action: STATE.md row + JSON patched-state (vault only —
  never pushed; this repo is code-only by design).
