#!/usr/bin/env bash
# Helpers shared by the scenario scripts.
# shellcheck disable=SC2034
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../cluster/versions.env
source "$ROOT/cluster/versions.env"
OUT=${OUT:-$ROOT/out}
mkdir -p "$OUT"

PASS=0
ok()   { PASS=$((PASS + 1)); echo "  ok  $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

wait_for() {  # wait_for <seconds> <description> <command...>
  local t=$1 what=$2; shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    (( SECONDS - start >= t )) && fail "$what (after ${t}s)"
    sleep 2
  done
  ok "$what ($((SECONDS - start))s)"
}
expect() {    # expect <description> <command...>
  local what=$1; shift
  if "$@"; then ok "$what"; else fail "$what"; fi
}
eq() { [[ "$1" == "$2" ]]; }

tenancy() { (cd "$ROOT" && python3 -m tenancy "$@"); }
submit()  { tenancy job "$@" | kubectl apply -f - >/dev/null; }

# q <kueue|kai> <python expression on d, the status JSON>   e.g. q kai 'd["research"]["running_pods"]'
q() { tenancy status "$1" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$2"; }
is() { [[ "$(q "$1" "$2" 2>/dev/null)" == "$3" ]]; }   # is <scheduler> <expr> <value>

snapshot() {  # snapshot <kueue|kai> <name>: keep the per-team table for the report
  { echo "### $2"; echo '```'; tenancy status "$1"; echo '```'; } >> "$OUT/$1-report.md"
}

gpus_on_nodes() {  # total nvidia.com/gpu allocatable on the GPU workers
  kubectl get nodes -l run.ai/simulated-gpu-node-pool=default \
    -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' | awk '{s+=$1} END {print s+0}'
}
