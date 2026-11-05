#!/usr/bin/env python3
"""agent-analyze: the tier-2 analyst. Runs on the WORKSTATION (where the
model subscriptions live), not in the cluster. The cluster emits signals
(KEV matches, scan verdicts, evidence JSON on its PVC — read here over
kubectl); this script hands them + the playbook to a frontier model and
records the verdict. The model's ONLY write path is this script's git
commit — blast radius: a commit, same rails as everything else.

Model chain (failover in order, first that answers wins):
  claude -> gemini -> codex -> opencode
Each is an authenticated subscription with an independent rate-limit pool.
"""
import glob
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAYBOOK = f"{REPO}/infrastructure/patch-pipeline/agent/playbook.md"
NS = "registry"
LOG = lambda m: print(f"[agent {time.strftime('%H:%M:%S')}] {m}", flush=True)


def run(argv, timeout=240, input_text=None):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input_text)
    return r


def read_pvc(path):
    """Read a file off the registry PVC via a one-off busybox pod."""
    name = f"agent-reader-{int(time.time())}"
    pod = {
        "apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NS},
        "spec": {"restartPolicy": "Never", "containers": [{
            "name": "reader", "image": "docker.io/library/busybox:1.36",
            "command": ["cat", path],
            "volumeMounts": [{"name": "data", "mountPath": "/data"}]}],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "registry-data"}}]},
    }
    run(["kubectl", "apply", "-n", NS, "-f", "-"], input_text=json.dumps(pod))
    for _ in range(20):
        time.sleep(3)
        logs = run(["kubectl", "logs", name, "-n", NS])
        if logs.returncode == 0 and logs.stdout:
            run(["kubectl", "delete", "pod", name, "-n", NS, "--force", "--grace-period=0",
                 "--ignore-not-found"])
            return logs.stdout.strip()
        if "Completed" in run(["kubectl", "get", "pod", name, "-n", NS]).stdout:
            break
    run(["kubectl", "delete", "pod", name, "-n", NS, "--force", "--grace-period=0", "--ignore-not-found"])
    return ""


def inventory():
    r = run(["kubectl", "get", "deploy", "sts", "ds", "-A", "-o", "json"])
    out = []
    for w in json.loads(r.stdout or "{}").get("items", []):
        for c in w["spec"].get("template", {}).get("spec", {}).get("containers", []):
            out.append(f"{w['kind']} {w['metadata']['namespace']}/{w['metadata']['name']} -> {c['image']}")
    return "\n".join(out)


def ask(model, prompt):
    if model == "claude":
        # prompt via stdin: argv empty-string args confuse the -p parser;
        # token from setup-token (valid 1 year) — never printed, file is 0600
        env = dict(os.environ)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = open(os.path.expanduser(
            "~/.config/claude-oauth-token")).read().strip()
        r = subprocess.run(["claude", "-p", "--allowedTools", "none"],
                           capture_output=True, text=True, timeout=300,
                           input=prompt, env=env)
    elif model == "gemini":
        r = run(["gemini", "-p", prompt], timeout=300)
    elif model == "codex":
        r = run(["codex", "exec", "--skip-git-repo-check", prompt], timeout=300)
    elif model == "opencode":
        r = run(["opencode", "run", prompt], timeout=300)
    else:
        return None
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    LOG(f"{model} failed: {(r.stderr or r.stdout)[:120]}")
    return None


def main():
    case_study = "--case-study" in sys.argv
    force = "--force" in sys.argv
    kev = read_pvc("/data/kev-state.json")
    inbox = read_pvc("/data/agent-inbox.json")

    # No-op guard: the timer fires every 30 min; the model only wakes when
    # something actually changed since the last processed run. Subscriptions
    # are rate-limit pools, not free firehoses.
    marker_path = os.path.expanduser("~/.config/pi-cluster-agent-state.json")
    try:
        marker = json.load(open(marker_path))
    except Exception:
        marker = {}
    try:
        kev_release = json.loads(kev).get("dateReleased") if kev else None
    except Exception:
        kev_release = None
    signals = []
    try:
        inbox_items = json.loads(inbox) if inbox.strip() else []
    except Exception:
        inbox_items = []
    if kev_release and kev_release != marker.get("kev_release"):
        signals.append(f"KEV feed updated to {kev_release}")
    if len(inbox_items) != marker.get("inbox_len", 0):
        signals.append(f"novel-event inbox at {len(inbox_items)} items")
    if not signals and not case_study and not force:
        LOG(f"nothing new since last run (KEV {kev_release}, inbox {len(inbox_items)}) — no model call")
        json.dump({"kev_release": kev_release, "inbox_len": len(inbox_items),
                   "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  open(marker_path, "w"), indent=2)
        return
    if signals:
        LOG("signals: " + "; ".join(signals))
    playbook_path = PLAYBOOK
    playbook = open(playbook_path).read() if os.path.exists(playbook_path) else "(empty)"
    task = ("Case study: record the PHP2Shell incident (CVE-2026-109618 / CVE-2026-109619) "
            "as playbook entry — the verified timeline, what our loop WOULD have done at "
            "each stage, and which signals we now watch every 15 minutes. Concise, factual."
            if case_study else
            "Assess the current KEV state and recent evidence against our inventory. "
            "Give a verdict (ACT NOW / TRACK / MITIGATE) and short reasoning.")
    prompt = (
        "You are the security analyst for a 4-node Raspberry Pi k3s cluster that "
        "self-patches via git commits. Read our playbook, then the task, then the "
        "current state. Respond with 'VERDICT: <ACT NOW|TRACK|MITIGATE>' followed by "
        "short reasoning suitable for an evidence record.\n\n"
        f"=== PLAYBOOK ===\n{playbook[-4000:]}\n\n=== TASK ===\n{task}\n\n"
        f"=== CURRENT STATE ===\nKEV: {kev[:1200] or 'no match recorded'}\n\n"
        f"=== NOVEL EVENTS (unfamiliar — adapt, then write the standing rule "
        f"into the playbook so tier-1 handles it next time) ===\n"
        f"{inbox[:1200] or 'none'}\n\n"
        f"=== RUNNING WORKLOADS ===\n{inventory()}\n")
    for model in ("claude", "gemini", "codex", "opencode"):
        LOG(f"asking {model}...")
        answer = ask(model, prompt)
        if answer:
            LOG(f"{model} answered ({len(answer)} chars)")
            break
    else:
        raise SystemExit("all models in the chain failed")
    os.makedirs(os.path.dirname(playbook_path), exist_ok=True)
    with open(playbook_path, "a") as f:
        f.write(f"\n\n## {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} — {model}\n\n"
                f"**Task:** {task}\n\n{answer}\n")
    for cmd in (["git", "-C", REPO, "add", "-A"],
                ["git", "-C", REPO, "-c", "user.name=security-agent", "-c",
                 "user.email=agent@pi-cluster.lan", "commit", "-q", "-m",
                 "agent playbook: analysis recorded"],
                ["git", "-C", REPO, "push", "-q", "origin", "HEAD"]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode and "nothing to commit" not in r.stderr:
            raise SystemExit(f"git failed: {cmd}\n{r.stderr}")
    LOG("playbook committed and pushed")


if __name__ == "__main__":
    main()
