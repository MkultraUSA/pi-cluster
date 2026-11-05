# pi-cluster

A 4-node Raspberry Pi Kubernetes cluster in my house that patches itself when
security advisories drop. Deploys are git commits. Every scan, verdict and
patch action lands on a public page the cluster writes itself:
**[mkultrausa.github.io/pi-cluster-status](https://mkultrausa.github.io/pi-cluster-status/)**

## Why

Most homelabs prove you can install software. I built this one to prove the
things employers actually screen for:

1. **One machine, many workloads.** Pods schedule freely across the 4 nodes —
   storage and images are node-agnostic. Single-node pinning is a design
   defect, not a convenience.
2. **Deploys are git commits.** ArgoCD (the engine behind Red Hat's OpenShift
   GitOps) syncs this repo to the cluster with self-heal on. If something
   drifts from git, it gets corrected or flagged. I never kubectl-apply to
   production by hand.
3. **The cluster patches itself when advisories drop.** Not "checks often" —
   acts. See the pipeline below.
4. **Every change is evidenced.** Scans, verdicts, patch actions and health
   gates produce timestamped artifacts. When something breaks, the record
   shows what I knew and when.

## Where it stands (2026-11-04)

- 4 nodes Ready, k3s v1.36 on arm64, Traefik ingress with Let's Encrypt certs
- ArgoCD v3.6.1 manages its own config and the workloads (app-of-apps)
- Hourly CVE scans, verdict engine, actuator, publisher — all CronJobs
- CISA KEV feed polled every 15 minutes; a match on a running image fires the
  actuator directly
- A security analyst agent (Claude / Gemini / Codex / OpenCode with automatic
  failover) wakes on real signals, reasons over the playbook, and commits its
  conclusions — it has no cluster credentials at all
- First real advisory-driven patch ran the same night the loop went live:
  `v2 → v20261104-2332` in four iterations, with the scanner catching that my
  first rebuild didn't actually clear the CVEs (see "the honest part")

## The self-patching pipeline

```
CVE intelligence (trivy DB, auto-refreshed hourly)
   → scan of every image in the in-cluster registry
   → verdict: unpatchable-upstream (track) | fixable>0 (REBUILD+REPLACE DUE)
   → actuator rebuilds in-cluster with kaniko (Debian + pip fixes baked)
   → new tag committed to git by a least-privilege ServiceAccount
   → ArgoCD rolls the fleet behind readiness gates
   → evidence written to the storage pool, published to the status page
```

The CISA KEV feed is the fast signal: it's polled every 15 minutes, and a
match on a running image skips the schedule and fires the actuator directly.
Target: under 2 hours from KEV listing to patched fleet.

### Why the urgency

In October 2026 two critical PHP remote-code-execution bugs (nicknamed
PHP2Shell) were being exploited **four days before the fix was released**.
Working exploits appeared within hours of the patch, and a week later most
sites still hadn't updated. AI compressed exploit development from weeks to
hours. Defenders get the same tools — a cluster that responds in hours, on
$400 of hardware.

### The honest part

The first rebuild tonight didn't actually clear the CVEs. The scanner caught
it: the vulnerable copies were vendored inside setuptools itself, one layer
deeper than my fix. Recipe v4 upgrades setuptools too. I kept the failed
attempt in the patch timeline on purpose — a scanner that can't tell you
your patch didn't work is worse than no scanner.

The agent's write path is a git commit. That's the whole blast radius. When
the scanner disagreed with the running container over whether a patch
landed, that disagreement went on the public page instead of being buried.

## Repo layout

```
apps/<name>/base/            workload manifests (kustomize)
infrastructure/patch-pipeline/
  base/                      scan CronJob, actuator, publisher (ArgoCD-synced)
  recipes/                   image build recipes + the scripts they bake in
  agent/                     the analyst: model chain + playbook
infrastructure/argocd/       AppProject (source/destination RBAC), Applications
infrastructure/registry/     the in-cluster registry
infrastructure/monitoring/   alloy + Grafana dashboard
apps/patch-demo/base/        the reference workload the pipeline patches
```

## Guardrails

- Nothing exposed off-LAN; NodePorts are LAN-only by design.
- $0 recurring cloud cost — anything that costs money needs a human OK.
- k3s upgrades and major version bumps are manually gated.
- Secrets never enter this repo: deploy keys and credentials are applied
  out-of-band. The agent's model tokens live on the workstation, not here.
- The analyst agent has no cluster credentials. Its only write path is a git
  commit, and its reasoning is recorded in the playbook next to the code.
- Clean shutdown before any physical power movement.
