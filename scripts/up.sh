#!/usr/bin/env bash
# Create the kind cluster with simulated GPUs: 4 workers x 8 GPUs (fake-gpu-operator).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

step "kind cluster ($KIND_NODE_IMAGE)"
kind create cluster --name lab --image "$KIND_NODE_IMAGE" --config "$ROOT/cluster/kind-gpu.yaml" --wait 3m

step "workload image, loaded once instead of pulled by every node"
docker pull -q busybox:1.36 >/dev/null
kind load docker-image busybox:1.36 --name lab

step "fake-gpu-operator $FAKE_GPU_OPERATOR_VERSION"
helm upgrade -i gpu-operator oci://ghcr.io/run-ai/fake-gpu-operator/fake-gpu-operator \
  --version "$FAKE_GPU_OPERATOR_VERSION" --namespace gpu-operator --create-namespace \
  --values "$ROOT/cluster/fake-gpu-values.yaml" --wait --timeout 5m
gpus_ready() { [[ "$(gpus_on_nodes)" == "32" ]]; }
wait_for 180 "32 simulated GPUs allocatable on 4 nodes" gpus_ready
