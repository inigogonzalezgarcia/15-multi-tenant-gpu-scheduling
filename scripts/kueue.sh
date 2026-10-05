#!/usr/bin/env bash
# Kueue on the GPU cluster: quotas, borrowing, lending limits, reclaim and priority preemption.
# Run after scripts/up.sh. Every number comes from tenancy.toml:
#   training 16 guaranteed (+8 borrow), research 8 (+8), inference 8 (never lent); 32 GPUs in total.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
: > "$OUT/kueue-report.md"

step "install Kueue $KUEUE_VERSION"
kubectl apply --server-side -f "https://github.com/kubernetes-sigs/kueue/releases/download/$KUEUE_VERSION/manifests.yaml" >/dev/null
kubectl -n kueue-system wait --for=condition=Available deployment/kueue-controller-manager --timeout=300s
apply_queues() { kubectl apply -f "$ROOT/manifests/namespaces.yaml" -f "$ROOT/manifests/kueue.yaml"; }
wait_for 120 "tenancy.toml applied (namespaces, ClusterQueues, LocalQueues)" apply_queues
cq_active() { [[ "$(kubectl get clusterqueues -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Active")].status}{end}')" == "TrueTrueTrue" ]]; }
wait_for 60 "3 ClusterQueues active" cq_active

running_pods() { [[ "$(kubectl -n "team-$1" get pods --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')" == "$2" ]]; }

step "1. research asks for 20 GPUs: 8 of its own + 8 borrowed from training, the rest waits"
for i in 1 2 3 4 5; do submit kueue research "research-$i" --pods 1 --gpus 4 --priority batch; done
wait_for 90 "4 research jobs admitted" is kueue 'len(d["research"]["admitted"])' 4
wait_for 60 "research uses 16 GPUs, 8 of them borrowed" is kueue '(d["research"]["gpus_in_use"], d["research"]["gpus_borrowed"])' "(16, 8)"
wait_for 60 "the fifth waits: research reached its borrowing limit" is kueue 'len(d["research"]["pending"])' 1
wait_for 90 "the 4 admitted jobs run on simulated GPUs" running_pods research 4
snapshot kueue "1. research borrows"

step "2. inference uses its own 8 GPUs: they were never lent"
for i in 1 2; do submit kueue inference "inference-$i" --pods 1 --gpus 4 --priority batch; done
wait_for 90 "2 inference jobs admitted without preempting anyone" is kueue 'len(d["inference"]["admitted"])' 2
wait_for 60 "nobody preempted yet" is kueue 'sum(sum(t["preemptions"].values()) for t in d.values())' 0
wait_for 60 "inference uses its 8 GPUs" is kueue 'd["inference"]["gpus_in_use"]' 8
snapshot kueue "2. inference"

step "3. training wants its 16 guaranteed GPUs back: Kueue reclaims what research borrowed"
for i in 1 2 3 4; do submit kueue training "training-$i" --pods 1 --gpus 4 --priority batch; done
wait_for 120 "4 training jobs admitted" is kueue 'len(d["training"]["admitted"])' 4
wait_for 60 "research back to its guarantee: 8 GPUs, nothing borrowed" is kueue '(d["research"]["gpus_in_use"], d["research"]["gpus_borrowed"])' "(8, 0)"
wait_for 60 "2 research jobs preempted, cause InCohortReclamation" is kueue 'd["research"]["preemptions"].get("InCohortReclamation", 0)' 2
wait_for 60 "3 research jobs waiting" is kueue 'len(d["research"]["pending"])' 3
snapshot kueue "3. reclaim"

step "4. an urgent training job: the cohort is full, so it preempts a batch job of its own team"
submit kueue training training-urgent --pods 1 --gpus 4 --priority urgent
wait_for 120 "training-urgent admitted" is kueue '"training-urgent" in d["training"]["admitted"]' True
wait_for 60 "1 training batch job preempted, cause InClusterQueue" is kueue 'd["training"]["preemptions"].get("InClusterQueue", 0)' 1
wait_for 60 "research and inference untouched" is kueue '(d["research"]["gpus_in_use"], d["inference"]["gpus_in_use"])' "(8, 8)"
snapshot kueue "4. priority within a team"

step "5. training finishes: research borrows again, up to its limit"
kubectl -n team-training delete jobs --all --wait=true >/dev/null
wait_for 120 "research back to 16 GPUs (8 borrowed)" is kueue '(d["research"]["gpus_in_use"], d["research"]["gpus_borrowed"])' "(16, 8)"
wait_for 60 "one research job still waits at the borrowing limit" is kueue 'len(d["research"]["pending"])' 1
wait_for 60 "inference still holds exactly its 8" is kueue 'd["inference"]["gpus_in_use"]' 8
snapshot kueue "5. borrowing again"

step "6. a job bigger than research could ever get is never started"
submit kueue research research-huge --pods 5 --gpus 4 --priority batch
sleep 10
wait_for 60 "research-huge (20 GPUs > 16 max) pending" is kueue '"research-huge" in d["research"]["pending"]' True
expect "and none of its 5 pods exists (all or nothing)" eq "$(kubectl -n team-research get pods -l batch.kubernetes.io/job-name=research-huge --no-headers 2>/dev/null | wc -l | tr -d ' ')" 0
snapshot kueue "6. never fits"

kubectl get clusterqueues -o json > "$OUT/kueue-clusterqueues.json"
kubectl get workloads -A -o json > "$OUT/kueue-workloads.json"
tenancy status kueue
echo
echo "PASSED: $PASS checks"
