"""tenancy: one GPU agreement between teams, enforced by Kueue or by KAI Scheduler.

    tenancy check                      validate tenancy.toml and print what each team gets
    tenancy render [--out manifests]   write namespaces.yaml, kueue.yaml, kai.yaml
    tenancy render --check             fail if the committed manifests are out of date
    tenancy job kueue|kai TEAM NAME    print a lab Job (pipe it to kubectl apply -f -)
    tenancy status kueue|kai           per-team GPUs, admitted / pending / preempted work
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config, jobs, render, status, yamlout

FILES = {"namespaces.yaml": render.namespaces, "kueue.yaml": render.kueue, "kai.yaml": render.kai}


def rendered(cfg: config.Config) -> dict[str, str]:
    return {name: yamlout.dump_all(fn(cfg), render.HEADER) for name, fn in FILES.items()}


def summary(cfg: config.Config) -> str:
    c = cfg.cluster
    rows = [f"cluster {c.name}: {c.gpu_nodes} nodes x {c.gpus_per_node} GPUs = {c.gpus} GPUs",
            f"{'team':<10} {'guaranteed':>10} {'max':>5} {'lends':>6} {'weight':>6}"]
    for t in cfg.teams:
        mx = "-" if t.max_gpus is None else str(t.max_gpus)
        lends = "all" if t.lends is None else str(t.lends)
        rows.append(f"{t.name:<10} {t.gpus:>10} {mx:>5} {lends:>6} {t.weight:>6}")
    rows += [f"warning: {w}" for w in cfg.warnings]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tenancy", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", default="tenancy.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate tenancy.toml")
    r = sub.add_parser("render", help="write the Kubernetes objects")
    r.add_argument("--out", default="manifests")
    r.add_argument("--check", action="store_true", help="only compare with the files in --out")
    j = sub.add_parser("job", help="print a lab Job")
    j.add_argument("scheduler", choices=("kueue", "kai"))
    j.add_argument("team")
    j.add_argument("name")
    j.add_argument("--pods", type=int, default=1)
    j.add_argument("--gpus", type=int, default=1, help="GPUs per pod")
    j.add_argument("--priority")
    j.add_argument("--min-member", type=int, help="KAI: gang size (pods that must start together)")
    j.add_argument("--gpu-fraction", help="KAI: share of one GPU per pod, e.g. 0.5")
    j.add_argument("--seconds", type=int, default=3600)
    s = sub.add_parser("status", help="per-team view of the cluster")
    s.add_argument("scheduler", choices=("kueue", "kai"))
    s.add_argument("--from", dest="saved", help="directory with saved kubectl JSON instead of a live cluster")
    s.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    try:
        cfg = config.load(a.config)
        if a.cmd == "check":
            print(summary(cfg))
            return 0
        if a.cmd == "render":
            out = Path(a.out)
            files = rendered(cfg)
            if a.check:
                stale = [n for n, text in files.items() if not (out / n).exists() or (out / n).read_text() != text]
                if stale:
                    print(f"out of date: {', '.join(stale)} (run: python -m tenancy render)", file=sys.stderr)
                    return 1
                print(f"{out}/: up to date")
                return 0
            out.mkdir(parents=True, exist_ok=True)
            for n, text in files.items():
                (out / n).write_text(text)
            print(f"wrote {', '.join(str(out / n) for n in files)}")
            return 0
        if a.cmd == "job":
            sys.stdout.write(yamlout.dump(jobs.job(cfg, a.scheduler, a.team, a.name, a.pods, a.gpus, a.priority,
                                                   a.min_member, a.gpu_fraction, a.seconds)))
            return 0
        if a.cmd == "status":
            gpu = cfg.cluster.gpu_resource
            if a.scheduler == "kueue":
                cqs = status.load_saved(a.saved, "clusterqueues") if a.saved else status.kubectl_json("clusterqueues")
                wls = status.load_saved(a.saved, "workloads") if a.saved else status.kubectl_json("workloads", "-A")
                teams = status.kueue(cqs, wls, gpu)
                print(status.to_json(teams) if a.json else status.table_kueue(teams))
            else:
                pods = status.load_saved(a.saved, "pods") if a.saved else status.kubectl_json("pods", "-A")
                teams = status.kai(pods, gpu)
                print(status.to_json(teams) if a.json else status.table_kai(teams))
            return 0
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"tenancy: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # kubectl failures
        print(f"tenancy: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
