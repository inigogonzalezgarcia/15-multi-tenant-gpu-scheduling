# Multi-tenant GPU Scheduling

Three teams share 32 GPUs. One file says who is guaranteed what, who may borrow and who never lends. The same agreement is enforced twice, by **Kueue** (quotas and admission) and by NVIDIA's **KAI Scheduler** (GPU-aware scheduling: reclaim, priorities, gang scheduling, GPU sharing), on kind with simulated GPUs, and every push checks that both behave as agreed.

**tenancy.toml → `python -m tenancy render` → Kueue ClusterQueues / KAI Queues → scenarios on a kind cluster with 32 simulated GPUs → per-team report**

![ci](https://github.com/inigogonzalezgarcia/15-multi-tenant-gpu-scheduling/actions/workflows/ci.yml/badge.svg)

> A learning-in-public lab about sharing GPU clusters between teams. I don't have production GPU fleet experience; this project is how I am learning the problem. Kubernetes, Kueue and KAI Scheduler are real (upstream releases on kind). The GPUs are not: Run:ai's [fake-gpu-operator](https://github.com/run-ai/fake-gpu-operator) advertises 8 `nvidia.com/gpu` per node with nothing behind them, which is enough to see every scheduling decision and nothing a GPU does.

Seventh in a series: [09 – node remediation](https://github.com/inigogonzalezgarcia/09-gpu-node-remediation), [10 – fleet observability](https://github.com/inigogonzalezgarcia/10-gpu-fleet-observability), [11 – fleet lifecycle](https://github.com/inigogonzalezgarcia/11-gpu-fleet-lifecycle), [12 – goodput and MTBI](https://github.com/inigogonzalezgarcia/12-gpu-goodput-mtbi), [13 – cluster acceptance](https://github.com/inigogonzalezgarcia/13-cluster-acceptance-burnin), [14 – Slurm GPU operations](https://github.com/inigogonzalezgarcia/14-slurm-gpu-ops) (the same questions on Slurm: queues, requeue, accounting).

## The agreement

[tenancy.toml](tenancy.toml), on a cluster of 4 nodes x 8 GPUs:

| Team | Guaranteed | May borrow | Lends its idle GPUs | Weight |
|---|---|---|---|---|
| training | 16 | 8 | yes | 2 |
| research | 8 | 8 | yes | 1 |
| inference | 8 | 0 | **no** | 1 |

`python -m tenancy render` writes [manifests/](manifests): namespaces, Kueue `ClusterQueue`s in one cohort with `nominalQuota` / `borrowingLimit` / `lendingLimit`, and KAI `Queue`s under one parent with `quota` / `limit` / `overQuotaWeight`. CI fails if the committed manifests drift from the file. [docs/design.md](docs/design.md) maps each field to each scheduler and explains how the two differ.

## What it shows

| | Kueue ([scripts/kueue.sh](scripts/kueue.sh)) | KAI Scheduler ([scripts/kai.sh](scripts/kai.sh)) |
|---|---|---|
| Borrowing idle GPUs | research runs 16 GPUs (8 borrowed); the 5th job waits at its limit | research runs 16 GPUs (8 over quota); the 5th pod waits at its limit |
| A team that never lends | inference's 8 GPUs are never lent; it starts without preempting anyone | — (KAI always lends idle quota, then reclaims it) |
| Getting guaranteed GPUs back | training submits 16 GPUs: 2 research jobs preempted (`InCohortReclamation`), research back to 8 | training submits 16 GPUs: research's over-quota pods evicted, research back to 8 |
| Priority inside a team | an `urgent` training job preempts a `batch` one of the same team (`InClusterQueue`); other teams untouched | a `build` (non-preemptible) job preempts a `train` pod of the same queue |
| All or nothing | a 20-GPU job research can never get stays pending, 0 of its 5 pods created | gang of 3 x 8 GPUs with 16 free: 0 of 3 start; the same job without a gang starts 2 of 3; with 24 free the gang starts at once |
| GPU sharing | — | two pods with `gpu-fraction: 0.5` placed on the same GPU: 1 GPU used |

## CI run

Every push runs unit tests, then three jobs on kind in parallel: Kueue (22 checks, about 2.5 minutes), KAI Scheduler (19 checks, about 4 minutes) and KubeVirt (5 checks, about 4 minutes). Per-team view after each step, from run 11 (the run summary shows the same tables):

Kueue, step 3, training takes its guarantee back:

```
team       guaranteed  in use borrowed  admitted / pending / preempted
inference           8       8        0  2 / 0 / -
research            8       8        0  2 / 3 / InCohortReclamation=2
training           16      16        0  4 / 0 / -
```

KAI Scheduler, step 5, gang scheduling:

```
queue      GPUs running pods running pods pending  jobs
inference             8            2            0  serving 2/2
research              8            1            0  blocker 1/1
training             16            2            4  gang 0/3, loose 2/3
```

then, once 24 GPUs are free: `training 24 3 0 gang 3/3`.

## KubeVirt with software emulation

An extra: [scripts/kubevirt.sh](scripts/kubevirt.sh) installs KubeVirt v1.9 with `useEmulation` on a separate kind cluster, after CI removes `/dev/kvm` so the VM really runs under QEMU TCG (the script fails if KVM is still there). A CirrOS VM in the research namespace reaches its login prompt in about a minute (61 s in run 11), and its launcher pod counts against the team's `ResourceQuota`: a second VM is refused with `exceeded quota` while the first keeps running. VMs and GPU jobs can be governed by the same per-team quota; no GPU passthrough is involved.

## Run it

Docker, kind, kubectl and Helm. Python 3.11+ (standard library only).

```bash
python -m tenancy check                       # what each team gets
python -m tenancy render                      # manifests/ from tenancy.toml
bash scripts/up.sh                            # kind, 4 workers x 8 simulated GPUs
bash scripts/kueue.sh                         # or: bash scripts/kai.sh (on a fresh cluster)
python -m tenancy status kueue                # per-team view at any moment
python -m tenancy job kai research sweep --pods 3 --gpus 4 | kubectl apply -f -
bash scripts/kubevirt.sh                      # separate cluster, no GPUs needed
kind delete clusters lab vms
python -m unittest -v
```

Versions are pinned in [cluster/versions.env](cluster/versions.env): kind v0.32.0, Kubernetes v1.35.0, fake-gpu-operator 0.2.1, Kueue v0.20.0, KAI Scheduler v0.18.2, KubeVirt v1.9.0.

## Documentation

- [docs/design.md](docs/design.md): one agreement, two schedulers; what Kueue and KAI each are; the lab cluster; how the report is built
- [docs/decisions.md](docs/decisions.md): design decisions, what CI taught me, what is missing

## Not tested here

- Real GPUs: no CUDA, no memory isolation for shared GPUs, no utilisation-based decisions.
- Kueue and KAI together (Kueue for admission, KAI for placement), fair sharing, Topology-Aware Scheduling, `PyTorchJob` / `JobSet` workloads.
- Several nodes per gang in a real network topology; multi-cluster (MultiKueue).
- KubeVirt with GPUs (passthrough or vGPU).

## Customisation and contact

Want to talk about GPU scheduling, multi-tenant clusters or a lab like this for your team? Get in touch:

- Email: [inigogonzalezgarcia@yahoo.es](mailto:inigogonzalezgarcia@yahoo.es)
- LinkedIn: [linkedin.com/in/igonzalez93](https://www.linkedin.com/in/igonzalez93)

## License

MIT
