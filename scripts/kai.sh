#!/usr/bin/env bash
# KAI Scheduler on the GPU cluster: GPU sharing, over-quota use and reclaim, priority inside a
# queue, and gang scheduling. Run after scripts/up.sh, on a cluster without Kueue.
# Queues from tenancy.toml: training 16 guaranteed (max 24), research 8 (max 16), inference 8 (max 8).
set -euo pipefail
source "$(dirname "$0")/lib.sh"
: > "$OUT/kai-report.md"

step "install KAI Scheduler $KAI_VERSION"
helm upgrade -i kai-scheduler oci://ghcr.io/kai-scheduler/kai-scheduler/kai-scheduler \
  --version "$KAI_VERSION" --namespace kai-scheduler --create-namespace \
  --values "$ROOT/cluster/kai-values.yaml" --wait --timeout 10m
kubectl -n kai-scheduler wait --for=condition=Available deployment --all --timeout=300s
apply_queues() { kubectl apply -f "$ROOT/manifests/namespaces.yaml" -f "$ROOT/manifests/kai.yaml"; }
wait_for 120 "tenancy.toml applied (namespaces, queues)" apply_queues

no_lab_pods() { [[ -z "$(kubectl get pods -A -l kai.scheduler/queue --no-headers 2>/dev/null)" ]]; }
clean() {  # delete every lab job and wait until no lab pod is left
  for t in training research inference; do kubectl -n "team-$t" delete jobs --all --wait=false >/dev/null 2>&1 || true; done
  wait_for 120 "cluster empty" no_lab_pods
}

step "1. GPU sharing: two inference pods with gpu-fraction 0.5 share one GPU"
submit kai inference shared --pods 2 --gpu-fraction 0.5 --priority inference
wait_for 120 "both pods running" is kai 'd["inference"]["running_pods"]' 2
expect "on the same GPU of the same node" is kai 'sorted(len(v) for v in d["inference"]["shared_gpus"].values())' "[2]"
expect "half a GPU each: 1 GPU in total" is kai 'd["inference"]["gpus_running"]' 1
snapshot kai "1. GPU sharing"
clean

step "2. research uses idle GPUs above its guarantee, up to its limit"
submit kai research research --pods 5 --gpus 4 --priority train
wait_for 120 "4 research pods running: 16 GPUs, 8 above its quota" is kai 'd["research"]["gpus_running"]' 16
expect "the fifth waits at the research limit (16)" is kai 'd["research"]["pending_pods"]' 1
submit kai inference serving --pods 2 --gpus 4 --priority inference
wait_for 120 "inference runs its 8 GPUs" is kai 'd["inference"]["gpus_running"]' 8
snapshot kai "2. over quota"

step "3. training claims its quota: KAI reclaims research's over-quota GPUs"
submit kai training training --pods 4 --gpus 4 --priority train
wait_for 180 "training runs 16 GPUs" is kai 'd["training"]["gpus_running"]' 16
expect "research back to its quota: 8 GPUs" is kai 'd["research"]["gpus_running"]' 8
expect "inference untouched" is kai 'd["inference"]["gpus_running"]' 8
snapshot kai "3. reclaim"

step "4. a build job (non-preemptible) in training preempts a train job of the same queue"
submit kai training build --pods 1 --gpus 4 --priority build
wait_for 180 "build job running" is kai 'd["training"]["jobs_running"].get("build", 0)' 1
wait_for 60 "training still at its 16 GPUs: one train pod gave way" is kai '(d["training"]["gpus_running"], d["training"]["jobs_running"].get("training", 0))' "(16, 3)"
snapshot kai "4. priority inside a queue"
clean

step "5. gang scheduling: all pods of a job start together, or none does"
submit kai inference serving --pods 2 --gpus 4 --priority inference       # 8 GPUs, not preemptible
submit kai research blocker --pods 1 --gpus 8 --priority build            # 8 GPUs, in quota, not preemptible
wait_for 120 "16 GPUs held by work that cannot be preempted" is kai '(d["inference"]["gpus_running"], d["research"]["gpus_running"])' "(8, 8)"
submit kai training gang --pods 3 --gpus 8 --min-member 3 --priority train
sleep 20
expect "gang of 3 x 8 GPUs, 16 free: none of its pods starts" is kai '(d["training"]["jobs_running"].get("gang", 0), d["training"]["jobs_pending"].get("gang", 0))' "(0, 3)"
submit kai training loose --pods 3 --gpus 8 --priority train
wait_for 120 "the same job without a gang: 2 of 3 pods start" is kai 'd["training"]["jobs_running"].get("loose", 0)' 2
snapshot kai "5. gang vs no gang"
kubectl -n team-training delete job loose --wait=true >/dev/null
kubectl -n team-research delete job blocker --wait=true >/dev/null
wait_for 180 "24 GPUs free: the 3 gang pods start together" is kai 'd["training"]["jobs_running"].get("gang", 0)' 3
snapshot kai "5. gang starts"

kubectl get pods -A -o json > "$OUT/kai-pods.json"
tenancy status kai
echo
echo "PASSED: $PASS checks"
