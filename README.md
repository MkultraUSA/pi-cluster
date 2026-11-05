# pi-cluster

A 4-node Raspberry Pi Kubernetes cluster in my house, run the way I'd run
production at a company: GitOps deploys, vulnerability management with a
patch SLA, offsite backups with a rehearsed restore, least-privilege RBAC,
and a public evidence trail. The cluster writes that trail itself:
**[mkultrausa.github.io/pi-cluster-status](https://mkultrausa.github.io/pi-cluster-status/)**

## The corporate mapping

Every piece of this cluster corresponds to something a company runs.
Same tools, same practices, real hardware.

| Corporate practice | What runs here |
|---|---|
| GitOps deploy (OpenShift GitOps / ArgoCD) | ArgoCD v3.6.1, self-heal on, app-of-apps. Production changes are git commits, nothing else. |
| Vulnerability management | trivy scans every image hourly against an auto-refreshed CVE database. Verdicts: TRACK or REBUILD+REPLACE DUE. |
| Threat intel prioritization | CISA KEV feed polled every 15 minutes. A match on a running image skips the queue and fires the patcher directly. |
| Automated remediation | The actuator rebuilds the image in-cluster (kaniko) with security fixes baked in, commits the new tag, and ArgoCD rolls the fleet behind readiness gates. |
| DR: database backups + rehearsed restore | Nightly pg_dump of the gateway database to an NFS pool (7 kept) and an offsite VPS (14 kept). The restore was rehearsed against a scratch database and verified. |
| Load balancing + ingress TLS | MetalLB L2 addresses, Traefik ingress, cert-manager with Let's Encrypt. |
| Least-privilege service accounts | The patcher and the analyst have RBAC roles that cannot touch workloads. Their only write path is a git commit. |
| AI analyst with an audit trail | A model chain (Claude → Gemini → Codex → OpenCode, failover) reviews unusual events and writes its reasoning into a versioned playbook. It has no cluster credentials. |

The last row is the one I'm proudest of. When PHP2Shell (two critical PHP
RCEs) was exploited in the wild **four days before the fix was released**,
and working exploits appeared within hours of the patch, my cluster already
had the loop that answers that: scan, verdict, rebuild, roll out, publish
the evidence. It caught that its own first patch was incomplete — the
vulnerable copies were vendored inside setuptools, one directory deeper than
my fix — and the correction is on the public page.

## Where it stands (2026-11-05)

- 4 nodes Ready, k3s v1.36 on arm64, ~$400 of hardware, $0 of cloud
- ArgoCD v3.6.1 manages its own config and the workloads (app-of-apps)
- Hourly CVE scans, CISA KEV polled every 15 minutes
- One real advisory-driven patch completed end-to-end: `v2 → v20261104-2332`
- MetalLB L2 pool, Traefik ingress with Let's Encrypt certs
- Offsite backups: nightly dump, offsite copy, rehearsed restore
- Home Assistant runs as a container on the cluster
- The status page and every scan verdict: published by the cluster itself

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │            k3s v1.36 (arm64)                │
  git push ───▶  │  ArgoCD ──▶ apps: desktop fleet, gateway,   │
  (main branch)  │             Home Assistant, patch pipeline  │
                 ├─────────────────────────────────────────────┤
                 │  MetalLB .244-.250 → Traefik ingress + TLS  │
                 │  in-cluster registry (kaniko targets)       │
                 │  NFS storage pool (dynamic provisioning)    │
                 │  Guacamole desktop gateway (8 desktops)     │
                 │  Grafana Cloud observability (no new SaaS)  │
                 └─────────────────────────────────────────────┘
                   cluster1        cluster2   cluster3   cluster4
                   192.168.86.188  .132       .251       .128
```

## Repo layout

```
apps/<name>/base/            workload manifests (kustomize)
apps/desktop-gate/backup/    the offsite backup CronJob
infrastructure/patch-pipeline/
  base/                      scan, actuator, publisher CronJobs (ArgoCD-synced)
  recipes/                   image build recipes + the scripts they bake in
  agent/                     the analyst: model chain + playbook
infrastructure/argocd/       AppProject (source/destination RBAC), Applications
infrastructure/metallb/      the LAN address pool
infrastructure/registry/     the in-cluster registry
infrastructure/monitoring/   alloy + Grafana dashboard
```

## The self-patching pipeline

```
CVE intelligence (trivy DB, auto-refreshed hourly)
   → CISA KEV polled every 15 minutes (fast path: match = patch now)
   → scan of every image in the in-cluster registry
   → verdict: unpatchable-upstream (track) | fixable>0 (REBUILD+REPLACE DUE)
   → kaniko in-cluster arm64 rebuild (Debian + pip fixes baked at build)
   → new tag committed to git by a least-privilege ServiceAccount
   → ArgoCD rolls the fleet behind readiness gates
   → evidence written to the storage pool, published to the status page
```

### Why the urgency — the PHP2Shell timeline, compared

In October 2026 two critical PHP remote-code-execution bugs (nicknamed
PHP2Shell) went from leak to mass exploitation in about a day. The real
timeline: exploitation in the wild **four days before any fix existed**
(honeypot telemetry, Oct 17), attacks observed the morning of patch day,
working exploits within hours of the 14:00 UTC release, CISA listing five
days later — and a week on, about half of modern PHP sites were still
unpatched. Under 1% of legacy sites ever moved.

A network running this setup would have been on a different clock, measured
tonight on a real advisory: the hourly scan flags the running image within an
hour of the fix hitting the trivy database, the actuator rebuilds and commits
the new tag in about five minutes, and ArgoCD finishes the rollout behind
readiness gates inside ten. Patched the same afternoon, with the evidence on
the status page.

Two honest limits. Exploitation before a fix exists is not a patching
problem — exposure control is, and this cluster's answer is LAN-only with one
watched door. And the pipeline is deliberately distro- and
language-agnostic: the CVEs it tracked tonight were in Python packaging
tools, one of which survived my first fix by hiding inside setuptools'
vendored copies. The scanner caught that. That's the part I like most.

### The honest part

The first rebuild tonight didn't actually clear the CVEs. The scanner caught
it: the vulnerable copies were vendored inside setuptools itself, one
directory deeper than my fix. Recipe v4 upgrades setuptools too. I kept the
failed attempt in the patch timeline on purpose — a scanner that can't tell
you your patch didn't work is worse than no scanner.

## Guardrails

- Nothing exposed off-LAN; load balancer and NodePorts are LAN-only by design.
- $0 recurring cloud cost — anything that costs money needs a human OK.
- k3s upgrades and major version bumps are manually gated.
- Secrets never enter this repo: deploy keys and credentials are applied
  out-of-band. The analyst's model tokens live on the workstation, not here.
- The analyst agent has no cluster credentials. Its only write path is a git
  commit, and its reasoning is recorded in the playbook next to the code.
- Clean shutdown before any physical power movement.
