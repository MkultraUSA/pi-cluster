#!/usr/bin/env bash
# Rebuild the pipeline tool image from source. Source of truth: the .py files
# beside this script — no embedded duplicates, no splicing.
set -euo pipefail
cd "$(dirname "$0")"
kubectl -n registry create cm actuator-build-files \
  --from-file=Dockerfile=Dockerfile \
  --from-file=actuate.py=actuate.py \
  --from-file=publish.py=publish.py \
  --from-file=kev-poll.py=kev-poll.py \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n registry delete job kaniko-build-actuator --ignore-not-found
kubectl apply -f job.yaml -n registry
kubectl wait --for=condition=complete job/kaniko-build-actuator -n registry --timeout=420s
echo "actuator:v1 rebuilt — CronJobs re-pull it on their next run"
