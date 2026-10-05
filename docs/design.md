# Design

## One agreement, two schedulers

`tenancy.toml` says who gets which GPUs. `python -m tenancy render` turns it into objects for both schedulers, so the agreement is written once and reviewed once:

| tenancy.toml | Kueue | KAI Scheduler |
|---|---|---|
| a team | a namespace + a `ClusterQueue` (namespaceSelector on the team label) + a `LocalQueue` in the namespace | a namespace + a leaf `Queue` under the cluster's parent queue |
| `gpus` (guaranteed) | `nominalQuota` | `quota` |
| `can_borrow` | `borrowingLimit` | `limit` = gpus + can_borrow |
| `lends` | `lendingLimit` | no equivalent: idle quota is always usable by others and reclaimed when needed |
| `weight` | (fair sharing weight, not enabled here) | `overQuotaWeight` |
| `[[priority]]` | `WorkloadPriorityClass` | KAI's built-in PriorityClasses (`train`, `build`, `inference`...) |
| all teams | one cohort (`org`): they lend to and borrow from each other | one parent queue (`lab`) with the whole cluster as its quota |

## What each one is

They work at different layers, and the lab runs them on separate clusters so each can be seen on its own.

| | Kueue | KAI Scheduler |
|---|---|---|
| Role | Admission: decides **when** a whole workload may start (keeps the Job suspended until it fits a quota) | Scheduler: decides **where** each pod runs (replaces kube-scheduler for its pods) |
| Pods placed by | the default kube-scheduler | KAI |
| Unit of decision | the Workload (a Job and all its pods) | the PodGroup (one pod, or a gang) |
| Quota is checked against | numbers in the ClusterQueues | the cluster's real free capacity and the queues |
| Taking GPUs back | preemption: the borrowing Job is suspended and requeued | reclaim: over-quota pods are evicted and rescheduled |
| All-or-nothing | always, per Job | per PodGroup (`kai.scheduler/batch-min-member`, or automatic for PyTorchJob, JobSet...) |
| GPU sharing | no | yes: `gpu-fraction`, several pods on one device |
| Topology | Topology-Aware Scheduling (not used here) | Topology-Aware Scheduling (not used here) |

Kueue can also admit workloads that KAI then places (Kueue for quota and admission, KAI for placement and sharing). That combination is not tested here.

## The lab cluster

kind with one control plane and four workers. [fake-gpu-operator](https://github.com/run-ai/fake-gpu-operator) (Run:ai, open source, maintained: release 0.2.1 in September 2026) runs a device plugin that advertises 8 `nvidia.com/gpu` on each labelled worker: 32 GPUs that the schedulers count and assign, with nothing behind them. It is what KAI Scheduler's own CI uses for its end-to-end tests, and this lab copies that setup (kind v0.32, Kubernetes 1.35, fake-gpu-operator 0.2.x).

What simulated GPUs can and cannot show:

- They can show every scheduling decision: quotas, borrowing, preemption, reclaim, gang scheduling, where a shared pod lands.
- They cannot show anything a GPU does: no CUDA, no memory isolation for shared GPUs (`NonMemoryEnforced` mode), no utilisation-based decisions.

Workloads are Jobs whose pods request GPUs and `sleep`. `python -m tenancy job` writes them, with the right labels for each scheduler.

## Measuring

`python -m tenancy status kueue|kai` gives the per-team view from the schedulers' own objects:

- Kueue: GPUs in use and borrowed from `ClusterQueue.status.flavorsUsage`; admitted, pending and preempted Jobs from the `Workload` conditions and `status.schedulingStats.evictions` (with the cause: `InCohortReclamation`, `InClusterQueue`).
- KAI: running and pending pods per queue, GPUs held (fractions added up), and which pods share a GPU (the `runai-gpu-group` label KAI puts on them).

The scenario scripts assert on that view and save it after each step (`out/*-report.md`, shown in the CI run summary).
