# pi-cluster

A 4-node Raspberry Pi Kubernetes cluster operated like a corporate environment:
GitOps deployment, an advisory-driven self-patching pipeline, RBAC, TLS, and an
evidence trail for every change.

## Why

Most homelabs prove you can install software. This one is built to prove the
things that are important:

1. **One machine, many workloads.** Pods schedule freely across the 4 nodes —
   storage and images are node-agnostic. Single-node pinning is treated as a
   design defect.
2. **Deploys are git commits.** ArgoCD (the engine behind Red Hat's OpenShift
   GitOps) syncs this repo to the cluster with self-heal on. Drift from any
   other source is corrected or flagged.
3. **The cluster patches itself when advisories drop.** See the pipeline below.
4. **Every change is evidenced.** Scans, verdicts, patch actions and health
   gates produce timestamped artifacts, not anecdotes.

## Architecture

```
                 ┌────────────────────────────────────────────┐
                 │            k3s v1.36 (arm64)               │
  git push ───▶  │  ArgoCD ──▶ apps: desktop fleet, gateway,  │
  (main branch)  │             smoke, patch pipeline          │
                 ├────────────────────────────────────────────┤
                 │  Traefik ingress + cert-manager (LE TLS)   │
                 │  in-cluster registry (kaniko targets)      │
                 │  NFS storage pool (dynamic provisioning)   │
                 │  Guacamole desktop gateway (8 desktops)    │
                 │  Grafana Cloud observability (no new SaaS) │
                 └────────────────────────────────────────────┘
                   cluster1        cluster2   cluster3   cluster4
                   192.168.86.188  .132       .251       .128
```

## Repo layout

```
apps/<name>/base/            workload manifests (kustomize)
infrastructure/<system>/     registry, monitoring, storage, patch pipeline
infrastructure/argocd/       AppProject (source/destination RBAC), Applications
bootstrap/                   app-of-apps root (as app count grows)
```

Environment overlays split out of `base/` when a second environment exists —
the seam is already in place.

## The self-patching pipeline

```
CVE intelligence (trivy DB, auto-refresh)
   → scheduled scan of every image in the in-cluster registry
   → verdict: unpatchable-upstream (track) | fixable>0 (REBUILD+REPLACE DUE)
   → kaniko in-cluster arm64 rebuild (Debian security fixes baked at build)
   → canary node → health gate → fleet rollout
   → evidence: before/after versions + gate results, timestamped
```

Current verdicts render live on the LAN status page. The verdict→rebuild step
is the actuator being wired next: the scan verdict triggers the rebuild Job
through a least-privilege ServiceAccount (AppProject RBAC above), keeping the
loop human-gated where it matters (major bumps, k3s upgrades) and automatic
where it doesn't.

## Guardrails

- Nothing exposed off-LAN; NodePorts are LAN-only by design.
- $0 recurring cloud cost — anything that costs money needs a human OK.
- k3s upgrades and major version bumps are manually gated.
- Secrets never enter this repo: deploy keys and credentials are applied
  out-of-band and mirrored to the vault, not git.
- Clean shutdown before any physical power movement.
