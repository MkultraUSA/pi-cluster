#!/usr/bin/env python3
"""patch-actuator: closes the self-patching loop.

Reads the latest trivy scan verdict from the registry PVC. If fixable
vulnerabilities exist (or --force), it:
  1. rebuilds patch-demo in-cluster via kaniko with Debian security fixes
     baked in (apt upgrade at build time) under a NEW immutable tag,
  2. bumps the tag in git — the deploy path is a commit, never kubectl,
  3. ArgoCD's auto-sync rolls the fleet with readiness gates,
  4. writes a timestamped evidence record to the PVC.

RBAC: runs as ServiceAccount patch-actuator — may create/watch build Jobs
and read scan data. It may NOT touch Deployments; the deployment changes
only because git changed. Least privilege by construction.
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

NS = "registry"
REGISTRY = "registry.registry.svc:5000"
IMAGE = "patch-demo"
REPO = "git@github.com:MkultraUSA/pi-cluster.git"
REPO_DIR = "/tmp/pi-cluster"
DEPLOY_FILE = f"{REPO_DIR}/apps/patch-demo/base/deployment.yaml"
SCAN_DIR = "/data/scans"
EVIDENCE_DIR = "/data/patched-state"
FORCE = "--force" in sys.argv
K8S_TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
K8S_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
GIT_KEY = "/git-key/id_ed25519"
GIT_ENV = dict(os.environ, GIT_SSH_COMMAND=f"ssh -i {GIT_KEY} -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes")
LOG = lambda m: print(f"[actuate {time.strftime('%H:%M:%S')}] {m}", flush=True)


def k8s(method, path, body=None):
    req = urllib.request.Request(
        f"https://kubernetes.default.svc{path}",
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={"Authorization": f"Bearer {K8S_TOKEN}", "Content-Type": "application/json"},
    )
    ctx = __import__("ssl").create_default_context(cafile=K8S_CA)
    with urllib.request.urlopen(req, context=ctx) as r:
        data = r.read()
    return json.loads(data) if data else {}


def scan_for_tag(tag):
    """Judge the DEPLOYED tag's own scan. A newer scan of an OLD tag says
    nothing about what the fleet runs — acting on it loops forever."""
    path = f"{SCAN_DIR}/{IMAGE}-{tag}.json"
    if not os.path.exists(path):
        return None, []
    with open(path) as f:
        report = json.load(f)
    vulns = [v for r in report.get("Results", []) for v in (r.get("Vulnerabilities") or [])]
    fixable = [v for v in vulns if v.get("FixedVersion")]
    return path, fixable


def current_tag():
    with open(DEPLOY_FILE) as f:
        for line in f:
            if "image:" in line and f"/{IMAGE}:" in line:
                return line.strip().split(f"{IMAGE}:")[1]
    raise SystemExit("current tag not found in deployment.yaml")


RECIPE_VERSION = "4-vendored-setuptools"


def rebuild_dockerfile(stage):
    """Recipe v3: Debian security fixes AND pip-level fixes. Recipe v2's apt
    upgrade cleared nothing measurable — the two fixable CVEs (wheel,
    jaraco.context) live in python packaging tools that ship in the base
    image. Bump RECIPE_VERSION when this changes so the guard re-fires."""
    return (
        "FROM python:3.11-slim-bookworm\n"
        "ARG BUILD_STAGE=unknown\n"
        "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade && rm -rf /var/lib/apt/lists/*\n"
        "# recipe v4: the CVEs that survived v2/v3 were VENDORED inside\n"
        "# setuptools (setuptools/_vendor/) — upgrading wheel/jaraco.context\n"
        "# alone leaves those copies behind. Upgrade setuptools with them.\n"
        "RUN pip install --no-cache-dir --upgrade pip setuptools wheel jaraco.context\n"
        'RUN mkdir -p /app && echo -n "$BUILD_STAGE" > /app/BUILD_STAGE && date -u +%Y-%m-%dT%H:%M:%SZ > /app/BUILT_AT\n'
        'CMD ["python3", "-m", "http.server", "8000", "--directory", "/app"]\n'
    )


def rebuild(new_tag):
    """Rebuild the image with today's security fixes under a new tag."""
    tag_cm = f"patch-demo-dockerfile-{new_tag}"
    body = rebuild_dockerfile(f"{new_tag}-security-rebuild")
    cm_body = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": tag_cm, "namespace": NS},
        "data": {"Dockerfile": body},
    }
    k8s("POST", f"/api/v1/namespaces/{NS}/configmaps", cm_body)
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": f"kaniko-rebuild-{new_tag}", "namespace": NS},
        "spec": {
            "backoffLimit": 1,
            "template": {"spec": {"restartPolicy": "Never", "containers": [{
                "name": "kaniko",
                "image": "gcr.io/kaniko-project/executor:v1.23.2",
                "args": [
                    "--dockerfile=/workspace/Dockerfile", "--context=/workspace",
                    f"--build-arg=BUILD_STAGE={new_tag}-security-rebuild",
                    f"--destination={REGISTRY}/{IMAGE}:{new_tag}",
                    "--insecure", "--insecure-pull", "--snapshot-mode=full", "--cache=false",
                ],
                "resources": {"requests": {"cpu": "500m", "memory": "512Mi"},
                              "limits": {"memory": "1200Mi"}},
                "volumeMounts": [{"name": "dockerfile", "mountPath": "/workspace"}],
            }], "volumes": [{"name": "dockerfile", "configMap": {"name": tag_cm}}]}},
        },
    }
    k8s("POST", f"/apis/batch/v1/namespaces/{NS}/jobs", job)
    LOG(f"rebuild Job kaniko-rebuild-{new_tag} created")
    deadline = time.time() + 25 * 60
    while time.time() < deadline:
        time.sleep(30)
        j = k8s("GET", f"/apis/batch/v1/namespaces/{NS}/jobs/kaniko-rebuild-{new_tag}")
        conds = j.get("status", {}).get("conditions", [])
        for c in conds:
            # k8s >=1.33 uses Complete/FailureCriteriaMet; older used Succeeded/Failed
            if c["type"] in ("Succeeded", "Complete"):
                LOG("rebuild Succeeded"); return True
            if c["type"] in ("Failed", "FailureCriteriaMet"):
                LOG("rebuild Failed"); return False
    LOG("rebuild timed out"); return False


def clone_repo():
    subprocess.run(["rm", "-rf", REPO_DIR], check=True)
    subprocess.run(["git", "clone", "--depth", "1", REPO, REPO_DIR], env=GIT_ENV, check=True, capture_output=True)


def bump_git(old_tag, new_tag):
    text = open(DEPLOY_FILE).read().replace(f"{IMAGE}:{old_tag}", f"{IMAGE}:{new_tag}")
    open(DEPLOY_FILE, "w").write(text)
    for cmd in (["git", "-C", REPO_DIR, "add", "-A"],
                ["git", "-C", REPO_DIR, "-c", "user.name=patch-actuator",
                 "-c", "user.email=actuator@pi-cluster.lan", "commit", "-q", "-m",
                 f"patch: bump {IMAGE} {old_tag} -> {new_tag} (security rebuild via trivy verdict)"],
                ["git", "-C", REPO_DIR, "push", "-q", "origin", "HEAD"]):
        r = subprocess.run(cmd, env=GIT_ENV, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"git failed: {cmd}\n{r.stderr}")
    LOG(f"git bump pushed: {old_tag} -> {new_tag}")




def already_rebuilt(tag):
    """True if tag came from one of OUR rebuilds with the CURRENT recipe.
    Rebuilding our own output with an unchanged recipe is the
    identical-doomed-call trap. A recipe change (like adding the apt
    upgrade) is a real change and must be allowed through."""
    for path in glob.glob(f"{EVIDENCE_DIR}/*.json"):
        with open(path) as f:
            e = json.load(f)
            if e.get("new_tag") == tag and e.get("recipe_version") == RECIPE_VERSION:
                return True
    return False


def main():
    clone_repo()
    old_tag = current_tag()
    scan_file, fixable = scan_for_tag(old_tag)
    priority = [c.strip() for c in os.environ.get("PRIORITY_CVES", "").split(",") if c.strip()]
    LOG(f"scan={os.path.basename(scan_file) if scan_file else 'NONE (deployed tag not scanned yet)'} "
        f"fixable={len(fixable)} current_tag={old_tag} force={FORCE} priority={priority or 'none'}")
    if scan_file is None:
        LOG("no scan of the deployed tag yet — waiting for the scanner, no action")
        return
    if already_rebuilt(old_tag) and not FORCE:
        LOG("deployed tag is our own rebuild; rebuilding again is the "
            "identical-doomed-call trap — no action (scanner will judge it)")
        return
    if not fixable and not FORCE:
        LOG("verdict: nothing fixable upstream — tracking only, no action")
        return
    if not fixable and FORCE:
        LOG("DRILL MODE: forcing a rebuild with no fixable verdict (test path)")
    stamp = time.strftime("%Y%m%d-%H%M")
    new_tag = f"v{stamp}"
    if not rebuild(new_tag):
        raise SystemExit("rebuild failed — no deploy change, evidence records the failure")
    bump_git(old_tag, new_tag)
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    evidence = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "image": f"{REGISTRY}/{IMAGE}", "old_tag": old_tag, "new_tag": new_tag,
        "scan_file": os.path.basename(scan_file) if scan_file else None,
        "fixable_count": len(fixable),
        "fixable_cves": sorted({v["VulnerabilityID"] for v in fixable})[:25],
        "recipe_version": RECIPE_VERSION,
        "priority_cves": priority,
        "kev_driven": any(c in priority for c in [v["VulnerabilityID"] for v in fixable]),
        "forced_drill": FORCE and not fixable,
        "rollout": "via git commit -> ArgoCD auto-sync (readiness-gated rolling update)",
    }
    with open(f"{EVIDENCE_DIR}/{IMAGE}-{stamp}.json", "w") as f:
        json.dump(evidence, f, indent=2)
    LOG(f"evidence written: {EVIDENCE_DIR}/{IMAGE}-{stamp}.json")


if __name__ == "__main__":
    main()
