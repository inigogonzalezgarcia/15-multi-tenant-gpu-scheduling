# Decisions

## Design

- **One file, two schedulers.** Teams negotiate GPUs once, in `tenancy.toml`. Rendering both Kueue and KAI objects from it keeps the two in step and makes the agreement reviewable as a diff. `render --check` in CI stops anyone from editing the generated YAML by hand.
- **Separate clusters for Kueue and KAI.** They can be combined (Kueue admits, KAI places), but then a result is hard to attribute. Each scenario script gets its own fresh kind cluster.
- **Assertions on the schedulers' own objects.** `tenancy status` reads ClusterQueue usage, Workload conditions and pods, not my own bookkeeping. If Kueue or KAI disagrees with the agreement, the check fails.
- **Workloads are `sleep` Jobs.** Scheduling only needs resource requests. The busybox image is loaded into kind once, so no node pulls from a registry during a scenario.
- **inference never lends (`lendingLimit: 0`).** Serving capacity that disappears when another team borrows it is not capacity. KAI has no equivalent field: it lends all idle quota and reclaims it when the owner asks. The README table marks that difference instead of hiding it.

## What CI taught me

- **Kueue's usage numbers lag admission.** A Workload can be `Admitted` a moment before `ClusterQueue.status.flavorsUsage` counts it. Checks on GPUs in use poll for up to 60 s instead of reading once.
- **The preemption cause is on a condition, not on the counter.** In Kueue v0.20 `schedulingStats.evictions` counted the preemptions with an empty `underlyingCause`; the cause (`InCohortReclamation`, `InClusterQueue`) was the reason of the `Preempted` condition. Once the workload is admitted again that condition turns `False` and the cause is gone, so the report says `Preempted` from then on.
- **Fractions are strings.** `tenancy status kai` reports GPUs as text so that half GPUs add up exactly (`1/2`). Comparing a tuple of that text with `(16, 3)` failed even though the cluster was right; the checks convert to numbers.
- **GitHub runners have KVM.** The first KubeVirt run passed, but on hardware virtualization. CI now removes `/dev/kvm` before installing KubeVirt and the script fails if KVM is still advertised, so "software emulation" is what is actually tested.

## Lab shortcuts

- Simulated GPUs: fake-gpu-operator's device plugin and status updater, nothing else. `gpu-fraction` runs in `NonMemoryEnforced` mode, so shared pods are not isolated.
- CPU and memory quotas are generous; only GPUs are contended.
- Kueue fair sharing is off: borrowing follows cohort order and priorities, and `weight` is used by KAI only.

## Missing

- Combining both schedulers, and Kueue fair sharing.
- Topology-aware placement (NVLink domains, racks) and multi-node gangs on a real network.
- Quotas per user or project inside a team (KAI hierarchical queues deeper than two levels).
- Showback: GPU-hours per team from the scheduler's history.
