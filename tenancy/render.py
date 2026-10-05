"""tenancy.toml -> Kubernetes objects for both schedulers.

    namespaces   one namespace per team, labelled with the team (both schedulers use them)
    kueue        ResourceFlavor, WorkloadPriorityClasses, one ClusterQueue per team in one cohort,
                 one LocalQueue per team namespace
    kai          a parent Queue for the cluster and one leaf Queue per team

The same agreement maps differently onto each scheduler; docs/design.md has the side-by-side table.
"""

from __future__ import annotations

from .config import Config, Team

LABEL = "tenancy.lab/team"
COHORT = "org"
KUEUE_API = "kueue.x-k8s.io/v1beta2"
KAI_API = "scheduling.run.ai/v2"
# CPU and memory are not what this lab rations; the queues give them generous room so that
# only GPUs decide admission.
CPU_PER_TEAM, MEM_PER_TEAM = "64", "256Gi"


def namespaces(cfg: Config) -> list[dict]:
    return [{"apiVersion": "v1", "kind": "Namespace",
             "metadata": {"name": t.namespace, "labels": {LABEL: t.name}}} for t in cfg.teams]


def _gpu_quota(cfg: Config, t: Team) -> dict:
    q = {"name": cfg.cluster.gpu_resource, "nominalQuota": t.gpus}
    if t.can_borrow is not None:
        q["borrowingLimit"] = t.can_borrow
    if t.lends is not None:
        q["lendingLimit"] = t.lends
    return q


def kueue(cfg: Config) -> list[dict]:
    c = cfg.cluster
    docs: list[dict] = [{
        "apiVersion": KUEUE_API, "kind": "ResourceFlavor", "metadata": {"name": c.flavor},
        "spec": {"nodeLabels": {c.node_pool_label: c.node_pool}},
    }]
    for p in cfg.priorities:
        docs.append({"apiVersion": KUEUE_API, "kind": "WorkloadPriorityClass", "metadata": {"name": p.name},
                     "value": p.value, "description": p.description})
    for t in cfg.teams:
        docs.append({
            "apiVersion": KUEUE_API, "kind": "ClusterQueue",
            "metadata": {"name": t.name, "labels": {LABEL: t.name}},
            "spec": {
                "cohortName": COHORT,
                "namespaceSelector": {"matchLabels": {LABEL: t.name}},
                "queueingStrategy": "BestEffortFIFO",
                "preemption": {
                    # take back lent GPUs from teams that are over their guarantee
                    "reclaimWithinCohort": "Any",
                    # inside a team, an urgent job may push out a batch job
                    "withinClusterQueue": "LowerPriority",
                },
                "resourceGroups": [{
                    "coveredResources": ["cpu", "memory", c.gpu_resource],
                    "flavors": [{"name": c.flavor, "resources": [
                        {"name": "cpu", "nominalQuota": CPU_PER_TEAM},
                        {"name": "memory", "nominalQuota": MEM_PER_TEAM},
                        _gpu_quota(cfg, t),
                    ]}],
                }],
            },
        })
    for t in cfg.teams:
        docs.append({"apiVersion": KUEUE_API, "kind": "LocalQueue",
                     "metadata": {"name": t.name, "namespace": t.namespace, "labels": {LABEL: t.name}},
                     "spec": {"clusterQueue": t.name}})
    return docs


def _kai_resources(gpu_quota: int, gpu_limit: int, weight: int) -> dict:
    unlimited = {"quota": -1, "limit": -1, "overQuotaWeight": 1}
    return {"cpu": dict(unlimited), "memory": dict(unlimited),
            "gpu": {"quota": gpu_quota, "limit": gpu_limit, "overQuotaWeight": weight}}


def kai(cfg: Config) -> list[dict]:
    total = cfg.cluster.gpus
    parent = cfg.cluster.name
    docs = [{"apiVersion": KAI_API, "kind": "Queue", "metadata": {"name": parent},
             "spec": {"displayName": f"{parent} cluster", "resources": _kai_resources(total, total, 1)}}]
    for t in cfg.teams:
        limit = t.max_gpus if t.max_gpus is not None else -1
        docs.append({"apiVersion": KAI_API, "kind": "Queue", "metadata": {"name": t.name, "labels": {LABEL: t.name}},
                     "spec": {"displayName": t.description or t.name, "parentQueue": parent,
                              "resources": _kai_resources(t.gpus, limit, t.weight)}})
    return docs


HEADER = "# Generated from tenancy.toml by `python -m tenancy render`. Do not edit by hand."
