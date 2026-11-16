# Run the converter on the cluster's Kubernetes (k3s)

1. Build arm64 on a Pi node (or buildx --platform linux/arm64) and push to the in-cluster registry:
       docker build -t 192.168.86.188:30500/libertas/epub-converter:0.1 ..
       docker push 192.168.86.188:30500/libertas/epub-converter:0.1
2. Output folder on the pool: /srv/bbpool1/epubs, writable by uid 1000. Once the shared
   manuals PVC exists, swap the nfs volume for that persistentVolumeClaim and point args at it.
3. kubectl apply -f namespace.yaml -f pilot-job.yaml   (kubectl lives on cluster1, 192.168.86.188)
   kubectl -n libertas logs -f job/epub-pilot
4. After Kevin approves the pilot book:  kubectl apply -f batch-job.yaml

Storage is NFS from cluster4 (192.168.86.128:/srv/bbpool1), so the Job can run on any node.
