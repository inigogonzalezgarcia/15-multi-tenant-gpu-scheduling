#!/usr/bin/env bash
# What to look at when a scenario fails (CI runs it on failure).
set +e
source "$(dirname "$0")/lib.sh"
kubectl get nodes -o wide
kubectl get nodes -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'
kubectl get pods -A -o wide
kubectl get clusterqueues,localqueues,workloads -A 2>/dev/null
kubectl get queues.scheduling.run.ai,podgroups -A 2>/dev/null
tenancy status kueue 2>/dev/null
tenancy status kai 2>/dev/null
kubectl get events -A --sort-by=.lastTimestamp | tail -n 60
for ns in kueue-system kai-scheduler gpu-operator kubevirt; do
  for d in $(kubectl -n "$ns" get deploy -o name 2>/dev/null); do
    echo "--- $ns $d"; kubectl -n "$ns" logs "$d" --tail=40 2>/dev/null
  done
done
