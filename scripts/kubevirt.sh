#!/usr/bin/env bash
# KubeVirt on kind with software emulation: a VM runs as a team's workload and counts against the
# team's ResourceQuota like any pod. Creates its own small cluster (no GPUs needed).
set -euo pipefail
source "$(dirname "$0")/lib.sh"
NS=team-research

step "kind cluster ($KIND_NODE_IMAGE)"
kind create cluster --name vms --image "$KIND_NODE_IMAGE" --config "$ROOT/cluster/kind-kubevirt.yaml" --wait 3m

step "KubeVirt $KUBEVIRT_VERSION with useEmulation"
kubectl apply -f "https://github.com/kubevirt/kubevirt/releases/download/$KUBEVIRT_VERSION/kubevirt-operator.yaml" >/dev/null
kubectl apply -f "$ROOT/kubevirt/kubevirt-cr.yaml"
kubectl -n kubevirt wait kv kubevirt --for condition=Available --timeout=15m
kvm=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.allocatable.devices\.kubevirt\.io/kvm}{" "}{end}')
if [[ "$kvm" =~ [1-9] ]]; then mode="KVM (the runner exposes /dev/kvm)"; else mode="software emulation (QEMU TCG)"; fi
echo "  virtualization: $mode"
if [[ -n "${REQUIRE_SOFTWARE_EMULATION:-}" && "$mode" == KVM* ]]; then fail "KVM is available; expected software emulation"; fi
kubectl apply -f "$ROOT/manifests/namespaces.yaml" >/dev/null

vm() { sed -e "s/NAME/$1/g" -e "s/VERSION/$KUBEVIRT_VERSION/" "$ROOT/kubevirt/vm.yaml" | kubectl apply -f - >/dev/null; }
launcher() { kubectl -n "$NS" get pods -l "kubevirt.io/vm=$1" -o name 2>/dev/null | head -1; }

step "1. a VM in the research namespace boots"
vm vm-a
vmi_running() { [[ "$(kubectl -n "$NS" get vmi "$1" -o jsonpath='{.status.phase}' 2>/dev/null)" == Running ]]; }
wait_for 600 "VMI vm-a running" vmi_running vm-a
booted() { kubectl -n "$NS" logs "$(launcher vm-a)" -c guest-console-log 2>/dev/null | grep -q "login"; }
wait_for 900 "guest reached its login prompt (serial console)" booted
kubectl -n "$NS" logs "$(launcher vm-a)" -c guest-console-log | tail -n 5 | sed 's/^/    | /'

step "2. the VM counts against the team's quota"
mem=$(kubectl -n "$NS" get "$(launcher vm-a)" -o json | python3 -c '
import json, sys
units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "k": 10**3, "M": 10**6, "G": 10**9}
def b(q):
    for u, f in units.items():
        if q.endswith(u): return int(float(q[:-len(u)]) * f)
    return int(q)
p = json.load(sys.stdin)
print(sum(b(c["resources"].get("requests", {}).get("memory", "0")) for c in p["spec"]["containers"]) // 2**20)')
limit=$(( mem * 3 / 2 ))
echo "  vm-a's launcher pod requests ${mem}Mi; quota for the namespace: ${limit}Mi (room for one VM, not two)"
kubectl -n "$NS" create quota research-vms --hard="requests.memory=${limit}Mi" >/dev/null
quota_used() { [[ -n "$(kubectl -n "$NS" get quota research-vms -o jsonpath='{.status.used.requests\.memory}')" ]]; }
wait_for 60 "quota tracks vm-a" quota_used
vm vm-b
refused() { kubectl -n "$NS" get events --field-selector reason=FailedCreate -o jsonpath='{.items[*].message}' | grep -q "exceeded quota"; }
wait_for 120 "vm-b refused: exceeded quota" refused
expect "vm-a still running" vmi_running vm-a
kubectl -n "$NS" get quota research-vms
{ echo "### KubeVirt"; echo "virtualization: $mode"; echo '```'; kubectl -n "$NS" get vm,vmi; kubectl -n "$NS" get quota; echo '```'; } > "$OUT/kubevirt-report.md"
echo
echo "PASSED: $PASS checks"
