#!/usr/bin/env python3
"""patch-publisher: renders the public status page and pushes it to GitHub Pages.

Reads from the registry PVC:
  /data/scans/<repo>-<tag>.json   trivy results for every repo and tag in the
                                  registry (dynamic catalog enumeration)
  /data/patched-state/*.json      actuator evidence (when tags were deployed)
Renders a dark, self-contained index.html + machine-readable status.json and
pushes them to the gh-pages branch of the PUBLIC status repo. The cluster
publishes its own security posture; nothing sensitive leaves the LAN
(node names and public CVE ids only — no addresses, no credentials).
"""
import glob
import html
import json
import os
import subprocess
import time

SCAN_DIR = "/data/scans"
EVIDENCE_DIR = "/data/patched-state"
REGISTRY_HOST = "registry.registry.svc:5000/"
STATUS_REPO = "git@github.com:MkultraUSA/pi-cluster-status.git"
STATUS_DIR = "/tmp/status"
GIT_KEY = "/status-git-key/id_ed25519"
GIT_ENV = dict(os.environ, GIT_SSH_COMMAND=f"ssh -i {GIT_KEY} -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes")
SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
LOG = lambda m: print(f"[publish {time.strftime('%H:%M:%S')}] {m}", flush=True)


def scans():
    out = []
    for path in sorted(glob.glob(f"{SCAN_DIR}/*.json"), key=os.path.getmtime):
        r = json.load(open(path))
        results = r.get("Results", [])
        vulns = [v for res in results for v in (res.get("Vulnerabilities") or [])]
        counts = {}
        for v in vulns:
            sev = v.get("Severity", "UNKNOWN").upper()
            counts[sev] = counts.get(sev, 0) + 1
        fixable = sorted({v["VulnerabilityID"] for v in vulns if v.get("FixedVersion")})
        ref, os_name = "", ""
        for res in results:
            target = res.get("Target") or ""
            if REGISTRY_HOST in target:
                ref = target.split(REGISTRY_HOST, 1)[1]
            md = (res.get("Metadata") or {}).get("OS") or {}
            if md.get("Name") and not os_name:
                os_name = f"{md.get('Family', '')} {md.get('Name')}".strip()
        if not ref:
            # fallback: scan filenames are <repo>-<tag>.json
            name = os.path.basename(path)[:-5]
            idx = name.find("-v")
            ref = f"{name[:idx]}:{name[idx + 1:]}" if idx > 0 else name
        repo, tag = ref.partition(":")[0], ref.partition(":")[2]
        out.append({
            "repo": repo,
            "tag": tag,
            "os": os_name,
            "scanned": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(os.path.getmtime(path))),
            "total": len(vulns),
            "counts": {s: counts.get(s, 0) for s in SEV_ORDER if counts.get(s)},
            "fixable": fixable,
        })
    return sorted(out, key=lambda s: (s["repo"], s["tag"]))


def timeline():
    rows = []
    for path in sorted(glob.glob(f"{EVIDENCE_DIR}/*.json"), key=os.path.getmtime, reverse=True):
        e = json.load(open(path))
        rows.append(e)
    return rows


def render(scans_data, tl):
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    rows = ""
    for s in scans_data:
        sev = " ".join(f'<span class="sev {k.lower()}">{k}:{v}</span>' for k, v in s["counts"].items()) or '<span class="ok">clean</span>'
        verdict = ('<span class="bad">REBUILD+REPLACE DUE</span>' if s["fixable"]
                   else '<span class="ok">NO ACTION REQUIRED</span>')
        fix = f'fixable: {len(s["fixable"])}' + (f' <span class="meta">({", ".join(s["fixable"][:6])}{"…" if len(s["fixable"]) > 6 else ""})</span>' if s["fixable"] else "")
        label = html.escape(f'{s["repo"]}:{s["tag"]}')
        if s["os"]:
            label += f' <span class="meta">({html.escape(s["os"])})</span>'
        rows += f'<tr><td><b>{label}</b></td><td>{sev}</td><td>{fix}</td><td>{verdict}</td><td class="meta">{s["scanned"]}</td></tr>'
    tlrows = ""
    for e in tl:
        cves = ", ".join(e.get("fixable_cves", [])[:6]) or "drill"
        tlrows += (f'<tr><td>{e.get("when","?")}</td><td>{html.escape(e.get("old_tag","?"))} → '
                   f'<b>{html.escape(e.get("new_tag","?"))}</b></td><td>{html.escape(cves)}</td>'
                   f'<td>{"drill" if e.get("forced_drill") else "advisory-driven"}</td></tr>')
    if not tlrows:
        tlrows = '<tr><td colspan="4" class="meta">no rebuilds yet — scans verdict-tracked</td></tr>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My Pi cluster patches itself now</title><style>
body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:60rem;background:#0d1117;color:#e6edf3;padding:0 1rem}}
h1{{color:#58a6ff}} h2{{color:#7ee787;margin-top:2rem}}
table{{border-collapse:collapse;margin:.5rem 0;width:100%}} td,th{{border:1px solid #30363d;padding:.45rem .8rem;text-align:left}}
th{{background:#161b22}} .ok{{color:#3fb950}} .bad{{color:#f85149}} .meta{{color:#8b949e;font-size:.85rem}}
.sev{{margin-right:.4rem}} .sev.critical{{color:#f85149;font-weight:700}} .sev.high{{color:#f0883e}} .sev.medium{{color:#d29922}} .sev.low{{color:#8b949e}}
code{{background:#161b22;padding:.1rem .35rem;border-radius:4px}}
</style></head><body>
<h1>My Raspberry Pi cluster patches itself now</h1>
<p>I run a 4-node Raspberry Pi Kubernetes cluster in my house. I got tired of reading CVE
advisories and wondering which of my machines they applied to, so I taught the cluster to do
that reading itself. This page is generated by the cluster. Every hour it scans every image it
runs, publishes what it found, and if a fix exists upstream — it patches itself and you can
read about it in the timeline below.</p>
<h2>How it maps to what companies run</h2>
<p>Same tools, same practices, real hardware. Deploys are ArgoCD git commits
with self-heal — the same engine OpenShift ships. Vulnerability management is
trivy, scanned hourly against an auto-refreshed database. Prioritization comes
from the CISA KEV feed, polled every 15 minutes: a known-exploited CVE in a
running image skips the queue. Patches are built in-cluster by kaniko with the
security fixes baked in, committed to git by a service account that has no
other permissions, and rolled out behind readiness gates. The database backs
itself up nightly to an offsite VPS, and I've restored from that copy to prove
it works. An AI analyst reviews anything unusual and writes what it decided
into a versioned playbook. Its only write path is a git commit.</p>
<p>Why the urgency? In October 2026 two critical PHP remote-code-execution bugs (nicknamed
PHP2Shell) were being exploited <i>four days before the fix was released</i>. Working exploits
showed up within hours of the patch, and a week later most sites still hadn't updated. The bad
guys work in hours now. So does this cluster.</p>
<h2>PHP2Shell: the timeline, compared</h2>
<p>What actually happened, from public reporting:</p>
<table><tr><th>when</th><th>the real world</th></tr>
<tr><td>Oct 17</td><td>exploitation seen in the wild — <b>four days before any fix existed</b></td></tr>
<tr><td>Oct 21, 07:43 UTC</td><td>attacks observed the morning of patch day</td></tr>
<tr><td>Oct 21, 14:00 UTC</td><td>PHP ships the fix; working exploits circulate within hours</td></tr>
<tr><td>Oct 26</td><td>CISA adds both CVEs to the known-exploited catalog</td></tr>
<tr><td>+ 1 week</td><td>about half of modern PHP sites still unpatched, under 1% of legacy sites</td></tr></table>
<p>What a network running this setup would have done, from measured runs:</p>
<table><tr><th>when</th><th>this cluster</th></tr>
<tr><td>fix hits the trivy database</td><td>the hourly scan flags the running image, verdict flips to REBUILD+REPLACE DUE (≤ 1 hour)</td></tr>
<tr><td>+ 5 minutes</td><td>the actuator rebuilds the image with the fixes baked in and commits the new tag</td></tr>
<tr><td>+ 10 minutes</td><td>ArgoCD has rolled the fleet behind readiness gates; evidence is on this page</td></tr></table>
<p>Two honest limits. First, the Oct 17–21 window: exploitation before any fix exists is not a
patching problem — exposure control is, and this cluster's answer is LAN-only with one watched
door. Second, my cluster runs no PHP, so I tested the mechanics on the images I do run. The
pipeline is distro- and language-agnostic on purpose: the CVEs it tracked tonight were in
Python packaging tools, one of which survived my first fix by hiding inside setuptools' vendored
copies. The scanner caught that. That's the part I like most.</p>
<h2>Images under watch</h2>
<table><tr><th>image:tag</th><th>open CVEs</th><th>fixable upstream</th><th>verdict</th><th>scanned</th></tr>
{rows}</table>
<h2>Patch timeline</h2>
<table><tr><th>when (UTC)</th><th>tag change</th><th>advisories addressed</th><th>trigger</th></tr>
{tlrows}</table>
<h2>What I'm still figuring out</h2>
<p>The first rebuild I did tonight didn't actually clear the two fixable CVEs. The scanner
caught it — turns out they were hiding inside setuptools' own vendored copy of wheel, not the
packages I upgraded. Recipe v4 upgrades setuptools too. I left the failed attempt in the
timeline on purpose. A scanner that can't tell you your patch didn't work is worse than no
scanner.</p>
<p class="meta">Rendered {now} by a Job inside the cluster · machine-readable:
<a href="status.json" style="color:#58a6ff">status.json</a> · 4 Raspberry Pis, $0 of cloud</p>
</body></html>"""


def main():
    scans_data, tl = scans(), timeline()
    os.makedirs("/tmp/render", exist_ok=True)
    open("/tmp/render/index.html", "w").write(render(scans_data, tl))
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "images": scans_data, "patch_timeline": tl}, open("/tmp/render/status.json", "w"), indent=2)
    subprocess.run(["rm", "-rf", STATUS_DIR], check=True)
    subprocess.run(["git", "clone", "--depth", "1", "--branch", "gh-pages", STATUS_REPO, STATUS_DIR],
                   env=GIT_ENV, check=True, capture_output=True)
    subprocess.run(["cp", "/tmp/render/index.html", "/tmp/render/status.json", STATUS_DIR + "/"], check=True)
    cmds = [["git", "-C", STATUS_DIR, "add", "-A"],
                ["git", "-C", STATUS_DIR, "-c", "user.name=patch-publisher", "-c", "user.email=publisher@pi-cluster.lan",
                 "commit", "-q", "-m", f"status update {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"],
            ["git", "-C", STATUS_DIR, "push", "-q", "origin", "gh-pages"]]
    for cmd in cmds:
        r = subprocess.run(cmd, env=GIT_ENV, capture_output=True, text=True)
        if r.returncode and "nothing to commit" not in r.stderr:
            raise SystemExit(f"git failed: {cmd}\n{r.stderr}")
    LOG(f"published: {len(scans_data)} tags, {len(tl)} timeline entries")


if __name__ == "__main__":
    main()
