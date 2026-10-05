"""Lab workloads: a Kubernetes Job that holds GPUs and sleeps, addressed to Kueue or to KAI.

    kueue  the Job carries the LocalQueue label; Kueue keeps it suspended until the team's quota
           admits all of its pods at once, then the default scheduler places them
    kai    the pods carry the KAI queue label and schedulerName: kai-scheduler; KAI places them
           (all together when --min-member is given: gang scheduling)
"""

from __future__ import annotations

from .config import Config

IMAGE = "busybox:1.36"
KAI_PRIORITIES = {"train", "build-preemptible", "build", "inference"}  # installed with KAI


def job(cfg: Config, scheduler: str, team: str, name: str, pods: int = 1, gpus: int = 1,
        priority: str | None = None, min_member: int | None = None, gpu_fraction: str | None = None,
        seconds: int = 3600) -> dict:
    t = cfg.team(team)
    if scheduler not in ("kueue", "kai"):
        raise ValueError(f"unknown scheduler {scheduler!r}")
    if pods < 1 or gpus < 0:
        raise ValueError("pods must be >= 1 and gpus >= 0")
    if gpu_fraction is not None and scheduler != "kai":
        raise ValueError("--gpu-fraction is a KAI feature")
    if min_member is not None and scheduler != "kai":
        raise ValueError("--min-member is for KAI; Kueue always admits a Job's pods together")
    if min_member is not None and not 1 <= min_member <= pods:
        raise ValueError("--min-member must be between 1 and --pods")

    resources = {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "16Mi"}}
    pod_annotations, pod_labels = {}, {"app": name}
    if gpu_fraction is not None:
        frac = float(gpu_fraction)
        if not 0 < frac < 1:
            raise ValueError("--gpu-fraction must be between 0 and 1")
        pod_annotations["gpu-fraction"] = gpu_fraction
    elif gpus:
        resources["limits"][cfg.cluster.gpu_resource] = gpus

    pod_spec = {
        "restartPolicy": "Never",
        "terminationGracePeriodSeconds": 0,
        "nodeSelector": {cfg.cluster.node_pool_label: cfg.cluster.node_pool},
        "containers": [{"name": "work", "image": IMAGE, "command": ["sleep", str(seconds)],
                        "resources": resources}],
    }
    labels, annotations = {"tenancy.lab/team": t.name}, {}
    if scheduler == "kueue":
        labels["kueue.x-k8s.io/queue-name"] = t.name
        if priority:
            known = {p.name for p in cfg.priorities}
            if priority not in known:
                raise ValueError(f"unknown Kueue priority {priority!r} (known: {', '.join(sorted(known))})")
            labels["kueue.x-k8s.io/priority-class"] = priority
    else:
        pod_labels["kai.scheduler/queue"] = t.name
        pod_spec["schedulerName"] = "kai-scheduler"
        if priority:
            if priority not in KAI_PRIORITIES:
                raise ValueError(f"unknown KAI priority {priority!r} (known: {', '.join(sorted(KAI_PRIORITIES))})")
            pod_spec["priorityClassName"] = priority
        if min_member is not None:
            annotations["kai.scheduler/batch-min-member"] = str(min_member)

    template_meta = {"labels": pod_labels}
    if pod_annotations:
        template_meta["annotations"] = pod_annotations
    meta = {"name": name, "namespace": t.namespace, "labels": labels}
    if annotations:
        meta["annotations"] = annotations
    return {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": meta,
        "spec": {
            "parallelism": pods, "completions": pods,
            # preempted or reclaimed pods are recreated; they must not use up the Job's retries
            "backoffLimit": 20,
            "podFailurePolicy": {"rules": [{"action": "Ignore", "onPodConditions": [{"type": "DisruptionTarget"}]}]},
            "template": {"metadata": template_meta, "spec": pod_spec},
        },
    }
