"""Per-team view of the cluster, from the scheduler's own objects.

    kueue  ClusterQueues (GPUs in use and borrowed) and Workloads (admitted, pending, preempted)
    kai    Pods addressed to KAI (running and pending GPUs per queue, shared GPUs)

Reads live through kubectl, or from saved `kubectl get ... -o json` files (the tests use those).
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path


def kubectl_json(*args: str) -> dict:
    out = subprocess.run(["kubectl", "get", *args, "-o", "json"], capture_output=True, text=True,
                         check=True, timeout=60).stdout
    return json.loads(out)


def qty(v) -> int:
    """'16' / 16 -> 16. GPU quantities are whole numbers here."""
    return int(str(v)) if v not in (None, "") else 0


def cond(obj: dict, ctype: str) -> dict | None:
    return next((c for c in obj.get("status", {}).get("conditions", []) if c.get("type") == ctype), None)


@dataclass
class KueueTeam:
    team: str
    guaranteed: int = 0
    borrow_limit: int | None = None
    gpus_in_use: int = 0
    gpus_borrowed: int = 0
    admitted: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    preemptions: dict[str, int] = field(default_factory=dict)  # cause -> count
    pending_reason: dict[str, str] = field(default_factory=dict)


def kueue(cqs: dict, workloads: dict, gpu: str = "nvidia.com/gpu") -> dict[str, KueueTeam]:
    teams: dict[str, KueueTeam] = {}
    for cq in cqs.get("items", []):
        name = cq["metadata"]["name"]
        t = teams[name] = KueueTeam(name)
        for rg in cq["spec"].get("resourceGroups", []):
            for fl in rg.get("flavors", []):
                for r in fl.get("resources", []):
                    if r["name"] == gpu:
                        t.guaranteed += qty(r.get("nominalQuota"))
                        if "borrowingLimit" in r:
                            t.borrow_limit = (t.borrow_limit or 0) + qty(r["borrowingLimit"])
        for fl in cq.get("status", {}).get("flavorsUsage", []):
            for r in fl.get("resources", []):
                if r["name"] == gpu:
                    t.gpus_in_use += qty(r.get("total"))
                    t.gpus_borrowed += qty(r.get("borrowed"))
    for wl in workloads.get("items", []):
        owner = next(iter(wl["metadata"].get("ownerReferences", [])), {}).get("name", wl["metadata"]["name"])
        cq = (wl.get("status", {}).get("admission") or {}).get("clusterQueue") or wl["spec"].get("queueName", "")
        t = teams.setdefault(cq, KueueTeam(cq))
        admitted = cond(wl, "Admitted")
        if cond(wl, "Finished") and cond(wl, "Finished").get("status") == "True":
            continue
        if admitted and admitted.get("status") == "True":
            t.admitted.append(owner)
        else:
            t.pending.append(owner)
            qr = cond(wl, "QuotaReserved")
            if qr and qr.get("message"):
                t.pending_reason[owner] = qr["message"]
        # how often it was preempted, and why: the eviction counter says how often; the reason
        # (InCohortReclamation, InClusterQueue...) is on the Preempted condition while it is True.
        # Once the workload is admitted again the condition turns False and the cause is gone.
        pre = cond(wl, "Preempted") or {}
        why = pre.get("reason") if pre.get("status") == "True" else None
        for ev in wl.get("status", {}).get("schedulingStats", {}).get("evictions", []):
            if ev.get("reason") == "Preempted":
                cause = ev.get("underlyingCause") or why or "Preempted"
                t.preemptions[cause] = t.preemptions.get(cause, 0) + int(ev.get("count", 1))
    for t in teams.values():
        t.admitted.sort()
        t.pending.sort()
    return teams


@dataclass
class KaiTeam:
    team: str
    running_pods: int = 0
    pending_pods: int = 0
    gpus_running: str = "0"   # a string: fractions add up to non-integers ("1/2")
    jobs_running: dict[str, int] = field(default_factory=dict)  # job -> running pods
    jobs_pending: dict[str, int] = field(default_factory=dict)
    shared_gpus: dict[str, list[str]] = field(default_factory=dict)  # node/gpu -> pods sharing it


def kai(pods: dict, gpu: str = "nvidia.com/gpu") -> dict[str, KaiTeam]:
    teams: dict[str, KaiTeam] = {}
    gpu_sum: dict[str, Fraction] = defaultdict(Fraction)
    for p in pods.get("items", []):
        labels = p["metadata"].get("labels", {})
        q = labels.get("kai.scheduler/queue")
        if not q or p.get("status", {}).get("phase") in ("Succeeded", "Failed") or p["metadata"].get("deletionTimestamp"):
            continue
        t = teams.setdefault(q, KaiTeam(q))
        job = labels.get("batch.kubernetes.io/job-name") or labels.get("job-name") or p["metadata"]["name"]
        ann = p["metadata"].get("annotations", {})
        frac = ann.get("gpu-fraction")
        g = Fraction(frac) if frac else Fraction(sum(qty(c.get("resources", {}).get("limits", {}).get(gpu))
                                                  for c in p["spec"].get("containers", [])))
        if p.get("status", {}).get("phase") == "Running":
            t.running_pods += 1
            t.jobs_running[job] = t.jobs_running.get(job, 0) + 1
            gpu_sum[q] += g
            if frac:
                # KAI labels a shared pod with the GPU group it was placed on (one group per device)
                where = labels.get("runai-gpu-group", "?")
                t.shared_gpus.setdefault(f"{p['spec'].get('nodeName', '?')}/{where}", []).append(p["metadata"]["name"])
        else:
            t.pending_pods += 1
            t.jobs_pending[job] = t.jobs_pending.get(job, 0) + 1
    for q, t in teams.items():
        t.gpus_running = str(gpu_sum[q])
    return teams


def table_kueue(teams: dict[str, KueueTeam]) -> str:
    rows = [f"{'team':<10} {'guaranteed':>10} {'in use':>7} {'borrowed':>8}  admitted / pending / preempted"]
    for t in sorted(teams.values(), key=lambda x: x.team):
        pre = ", ".join(f"{k}={v}" for k, v in sorted(t.preemptions.items())) or "-"
        rows.append(f"{t.team:<10} {t.guaranteed:>10} {t.gpus_in_use:>7} {t.gpus_borrowed:>8}  "
                    f"{len(t.admitted)} / {len(t.pending)} / {pre}")
    return "\n".join(rows)


def table_kai(teams: dict[str, KaiTeam]) -> str:
    rows = [f"{'queue':<10} {'GPUs running':>12} {'pods running':>12} {'pods pending':>12}  jobs"]
    for t in sorted(teams.values(), key=lambda x: x.team):
        jobs = ", ".join(f"{j} {t.jobs_running.get(j, 0)}/{t.jobs_running.get(j, 0) + t.jobs_pending.get(j, 0)}"
                         for j in sorted(set(t.jobs_running) | set(t.jobs_pending)))
        rows.append(f"{t.team:<10} {t.gpus_running:>12} {t.running_pods:>12} {t.pending_pods:>12}  {jobs}")
    return "\n".join(rows)


def to_json(teams: dict) -> str:
    return json.dumps({k: asdict(v) for k, v in sorted(teams.items())}, indent=1, sort_keys=True)


def load_saved(directory: str | Path, name: str) -> dict:
    return json.loads((Path(directory) / f"{name}.json").read_text())
