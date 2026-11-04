#!/usr/bin/env python3
"""kev-poll: minutes-tier threat intel. Polls the CISA Known Exploited
Vulnerabilities feed every 15 minutes (plain HTTP, no AI, near-zero cost).
When a KEV entry matches a CVE in the scan of the DEPLOYED patch-demo tag,
it fires the patch-actuator immediately with PRIORITY_CVES — cutting the
detection chain from ~12h worst case to <=1h. PHP2Shell lesson: exploitation
started before disclosure and went broad within hours of the patch.
"""
import glob
import json
import os
import time
import urllib.request

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
STATE = "/data/kev-state.json"
SCAN_DIR = "/data/scans"
EVIDENCE_DIR = "/data/patched-state"
IMAGE = "patch-demo"
NS = "registry"
K8S_TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
K8S_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
LOG = lambda m: print(f"[kev {time.strftime('%H:%M:%S')}] {m}", flush=True)


def k8s(method, path, body=None):
    req = urllib.request.Request(
        f"https://kubernetes.default.svc{path}",
        data=json.dumps(body).encode() if body else None, method=method,
        headers={"Authorization": f"Bearer {K8S_TOKEN}", "Content-Type": "application/json"})
    ctx = __import__("ssl").create_default_context(cafile=K8S_CA)
    with urllib.request.urlopen(req, context=ctx) as r:
        data = r.read()
    return json.loads(data) if data else {}


def deployed_tag():
    dep = k8s("GET", f"/apis/apps/v1/namespaces/{NS}/deployments/{IMAGE}")
    img = dep["spec"]["template"]["spec"]["containers"][0]["image"]
    return img.split(f"/{IMAGE}:")[1]


def kev_cves():
    with urllib.request.urlopen(KEV_URL, timeout=30) as r:
        feed = json.load(r)
    return feed.get("dateReleased", feed.get("catalogVersion", "unknown")), \
        {e["cveID"] for e in feed.get("vulnerabilities", [])}, len(feed.get("vulnerabilities", []))


def main():
    released, cves, total = kev_cves()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"dateReleased": None}
    if state.get("dateReleased") == released:
        LOG(f"KEV unchanged ({released}, {total} entries) — no action")
        return
    LOG(f"KEV feed changed: {released} ({total} entries)")
    tag = deployed_tag()
    scan_path = f"{SCAN_DIR}/{IMAGE}-{tag}.json"
    if not os.path.exists(scan_path):
        LOG(f"no scan for deployed tag {tag} yet — will match on next poll"); return
    report = json.load(open(scan_path))
    vulns = [v for res in report.get("Results", []) for v in (res.get("Vulnerabilities") or [])]
    our = {v["VulnerabilityID"] for v in vulns}
    fixable = {v["VulnerabilityID"] for v in vulns if v.get("FixedVersion")}
    hits = our & cves
    LOG(f"deployed tag {tag}: {len(our)} CVEs, {len(fixable)} fixable, KEV matches: {sorted(hits) or 'none'}")
    if not hits:
        json.dump({"dateReleased": released, "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "matched": []}, open(STATE, "w"), indent=2)
        return
    # fire the actuator immediately, carrying the priority CVEs
    cron = k8s("GET", f"/apis/batch/v1/namespaces/{NS}/cronjobs/patch-actuator")
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": f"patch-actuator-kev-{int(time.time())}", "namespace": NS},
        "spec": cron["spec"]["jobTemplate"]["spec"],
    }
    job["spec"]["template"]["spec"]["containers"][0]["args"] = []
    job["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": "PRIORITY_CVES", "value": ",".join(sorted(hits))}]
    k8s("POST", f"/apis/batch/v1/namespaces/{NS}/jobs", job)
    LOG(f"ACTUATOR FIRED (KEV): {sorted(hits)}")
    json.dump({"dateReleased": released, "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "matched": sorted(hits), "actuator_fired": True}, open(STATE, "w"), indent=2)


if __name__ == "__main__":
    main()
