# Template only — the SSH private key is applied to the cluster out-of-band
# and is never committed (repo is code-only; secrets live in the vault/chat).
#
# kubectl -n argocd create secret generic pi-cluster-repo \
#   --from-file=sshPrivateKey=/path/to/deploy-key
# kubectl -n argocd label secret pi-cluster-repo \
#   argocd.argoproj.io/secret-type=repository
# kubectl -n argocd patch secret pi-cluster-repo --type merge \
#   -p '{"stringData":{"url":"git@github.com:MkultraUSA/pi-cluster.git","type":"git"}}'
apiVersion: v1
kind: Secret
metadata:
  name: pi-cluster-repo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
type: Opaque
stringData:
  url: git@github.com:MkultraUSA/pi-cluster.git
  type: git
  # sshPrivateKey: <applied out-of-band — never commit>
