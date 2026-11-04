#!/usr/bin/env python3
"""patch-publisher: renders the public status page and pushes it to GitHub Pages.

Reads from the registry PVC:
  /data/scans/patch-demo-*.json   trivy results per tag (6h cadence)
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
IMAGE = "patch-demo"
STATUS_REPO = "git@github.com:MkultraUSA/pi-cluster-status.git"
STATUS_DIR = "/tmp/status"
GIT_KEY = "/status-git-key/id_ed25519"
GIT_ENV = dict(os.environ, GIT_SSH_COMMAND=f"ssh -i {GIT_KEY} -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes")
SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
LOG = lambda m: print(f"[publish {time.strftime('%H:%M:%S')}] {m}", flush=True)


def scans():
    out = []
    for path in sorted(glob.glob(f"{SCAN_DIR}/{IMAGE}-*.json"), key=os.path.getmtime):
        tag = os.path.basename(path)[len(IMAGE) + 1:-5]
        r = json.load(open(path))
        vulns = [v for res in r.get("Results", []) for v in (res.get("Vulnerabilities") or [])]
        counts = {}
        for v in vulns:
            sev = v.get("Severity", "UNKNOWN").upper()
            counts[sev] = counts.get(sev, 0) + 1
        fixable = sorted({v["VulnerabilityID"] for v in vulns if v.get("FixedVersion")})
        out.append({
            "tag": tag,
            "scanned": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(os.path.getmtime(path))),
            "total": len(vulns),
            "counts": {s: counts.get(s, 0) for s in SEV_ORDER if counts.get(s)},
            "fixable": fixable,
        })
    return out


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
        rows += f'<tr><td><b>{html.escape(s["tag"])}</b></td><td>{sev}</td><td>{fix}</td><td>{verdict}</td><td class="meta">{s["scanned"]}</td></tr>'
    tlrows = ""
    for e in tl:
        cves = ", ".join(e.get("fixable_cves", [])[:6]) or "drill"
        tlrows += (f'<tr><td>{e.get("when","?")}</td><td>{html.escape(e.get("old_tag","?"))} → '
                   f'<b>{html.escape(e.get("new_tag","?"))}</b></td><td>{html.escape(cves)}</td>'
                   f'<td>{"drill" if e.get("forced_drill") else "advisory-driven"}</td></tr>')
    if not tlrows:
        tlrows = '<tr><td colspan="4" class="meta">no rebuilds yet — scans verdict-tracked</td></tr>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pi-cluster — live security posture</title><style>
body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:60rem;background:#0d1117;color:#e6edf3;padding:0 1rem}}
h1{{color:#58a6ff}} h2{{color:#7ee787;margin-top:2rem}}
table{{border-collapse:collapse;margin:.5rem 0;width:100%}} td,th{{border:1px solid #30363d;padding:.45rem .8rem;text-align:left}}
th{{background:#161b22}} .ok{{color:#3fb950}} .bad{{color:#f85149}} .meta{{color:#8b949e;font-size:.85rem}}
.sev{{margin-right:.4rem}} .sev.critical{{color:#f85149;font-weight:700}} .sev.high{{color:#f0883e}} .sev.medium{{color:#d29922}} .sev.low{{color:#8b949e}}
code{{background:#161b22;padding:.1rem .35rem;border-radius:4px}}
</style></head><body>
<h1>pi-cluster — live security posture</h1>
<p class="meta">A 4-node Raspberry Pi Kubernetes cluster that rebuilds and replaces its own
images when CVE advisories make fixes available. Rendered {now} by the cluster itself.</p>
<h2>How it works</h2>
<p>Every 6 hours the cluster scans every image it runs (trivy, DB auto-refreshed from CVE feeds).
A verdict engine marks images <i>REBUILD+REPLACE DUE</i> when upstream fixes exist. The actuator
then rebuilds the image in-cluster with Debian security patches baked in, commits the new tag to
git, and ArgoCD rolls the fleet behind readiness gates — with a timestamped evidence record for
every action. Humans gate only the risky parts: k3s upgrades and major version bumps.</p>
<h2>Images under watch</h2>
<table><tr><th>image:tag</th><th>open CVEs</th><th>fixable upstream</th><th>verdict</th><th>scanned</th></tr>
{rows}</table>
<h2>Patch timeline — when fixes were deployed</h2>
<table><tr><th>when (UTC)</th><th>tag change</th><th>advisories addressed</th><th>trigger</th></tr>
{tlrows}</table>
<p class="meta">Generated by the patch-publisher Job inside the cluster · machine-readable:
<a href="status.json" style="color:#58a6ff">status.json</a> · source cluster: private repo, pipeline code on request</p>
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
