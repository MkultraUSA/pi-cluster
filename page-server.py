#!/usr/bin/env python3
"""Patch-pipeline status page: renders live from /data/scans/*.json (trivy).
Runs as a Deployment on the registry's PVC. Stdlib only, LAN-only by NodePort.
"""
import glob
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SCAN_DIR = "/data/scans"

CSS = """body{font-family:system-ui,sans-serif;margin:2rem;background:#0d1117;color:#e6edf3}
h1{color:#58a6ff}h2{color:#7ee787;margin-top:1.5rem}
table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #30363d;padding:.4rem .8rem;text-align:left}
th{background:#161b22}.ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}.meta{color:#8b949e;font-size:.85rem}"""


def load_scans():
    out = []
    for p in sorted(glob.glob(SCAN_DIR + "/*.json")):
        try:
            d = json.load(open(p))
            res = (d.get("Results") or [{}])[0]
            vulns = res.get("Vulnerabilities") or []
            fixable = [v for v in vulns
                       if v.get("FixedVersion") and v.get("FixedVersion") != v.get("InstalledVersion")]
            sev = {}
            for v in vulns:
                sev[v.get("Severity", "?")] = sev.get(v.get("Severity", "?"), 0) + 1
            out.append({
                "file": os.path.basename(p),
                "target": res.get("Target", "?"),
                "digest": (res.get("Metadata") or {}).get("RepoDigests", [""])[0].split("@")[-1][:19] + "…",
                "created": d.get("CreatedAt", "?")[:19].replace("T", " ") + "Z",
                "count": len(vulns),
                "sev": sev,
                "fixable": len(fixable),
                "top": vulns[:4],
            })
        except Exception as e:
            out.append({"file": os.path.basename(p), "target": "PARSE ERROR", "err": str(e)})
    return out


def row(s):
    if s.get("err"):
        return f'<tr><td>{s["file"]}</td><td class="bad">parse error</td></tr>'
    verdict = ('<span class="bad">REBUILD+REPLACE DUE</span>' if s["fixable"]
               else '<span class="warn">unpatchable-upstream — track</span>')
    top = ", ".join(v["VulnerabilityID"] for v in s["top"])
    return (f'<tr><td>{s["target"]}</td><td class="meta">{s["digest"]}</td>'
            f'<td>{s["count"]}</td><td class="bad">{s["sev"].get("CRITICAL",0)}C / {s["sev"].get("HIGH",0)}H</td>'
            f'<td>{s["fixable"]}</td><td>{verdict}</td>'
            f'<td class="meta">{s["created"]}</td><td class="meta">{top}…</td></tr>')


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        scans = load_scans()
        rows = "\n".join(row(s) for s in scans)
        gen = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Pi Cluster — Patch Pipeline</title><style>{CSS}</style></head><body>
<h1>Pi Cluster — Self-Patching Image Pipeline</h1>
<p class="meta">LAN-only · rendered {gen} · pipeline: advisory (trivy DB) → scan → kaniko rebuild → registry → canary replace → evidence</p>
<h2>Live scan results (auto-updated every 6h by pipeline-scan CronJob)</h2>
<table><tr><th>Image</th><th>Digest</th><th>Vulns</th><th>Crit/High</th><th>Fixable</th><th>Verdict</th><th>Scanned</th><th>Top CVEs</th></tr>
{rows if rows else '<tr><td colspan="8">no scans yet — first CronJob run pending</td></tr>'}</table>
<h2>Design inputs</h2>
<table><tr><th>Input</th><th>Value</th></tr>
<tr><td>Base image</td><td>python:3.11-slim-bookworm (pinned at build)</td></tr>
<tr><td>CVE intelligence</td><td>trivy DB, auto-updated, via public.ecr.aws mirror</td></tr>
<tr><td>Security fixes</td><td>deb.debian.org/debian-security (bookworm-security) — apt -y upgrade baked at build</td></tr>
<tr><td>Build engine</td><td>kaniko v1.23.2, in-cluster, arm64, any node</td></tr>
<tr><td>Registry</td><td>registry.registry.svc:5000 / 192.168.86.188:30500</td></tr>
<tr><td>Canary</td><td>cluster2 — registries.yaml live, pull verified 2026-11-01</td></tr></table>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", 80), H).serve_forever()
