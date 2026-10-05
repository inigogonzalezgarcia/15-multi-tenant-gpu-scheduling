"""tenancy.toml: the GPU agreement between teams, validated before anything is rendered."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$")


@dataclass(frozen=True)
class Team:
    name: str
    gpus: int
    can_borrow: int | None = None  # None: no cap beyond what the cohort has free
    lends: int | None = None       # None: lends everything it is not using
    weight: int = 1
    description: str = ""

    @property
    def namespace(self) -> str:
        return f"team-{self.name}"

    @property
    def max_gpus(self) -> int | None:
        return None if self.can_borrow is None else self.gpus + self.can_borrow


@dataclass(frozen=True)
class Priority:
    name: str
    value: int
    description: str = ""


@dataclass(frozen=True)
class Cluster:
    name: str
    gpu_nodes: int
    gpus_per_node: int
    gpu_resource: str = "nvidia.com/gpu"
    node_pool_label: str = "run.ai/simulated-gpu-node-pool"
    node_pool: str = "default"
    flavor: str = "lab-gpu"

    @property
    def gpus(self) -> int:
        return self.gpu_nodes * self.gpus_per_node


@dataclass(frozen=True)
class Config:
    cluster: Cluster
    teams: list[Team]
    priorities: list[Priority] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def team(self, name: str) -> Team:
        for t in self.teams:
            if t.name == name:
                return t
        raise ValueError(f"unknown team {name!r} (teams: {', '.join(t.name for t in self.teams)})")


ALLOWED = {
    "cluster": {"name", "gpu_nodes", "gpus_per_node", "gpu_resource", "node_pool_label", "node_pool", "flavor"},
    "team": {"name", "gpus", "can_borrow", "lends", "weight", "description"},
    "priority": {"name", "value", "description"},
}


def _check_keys(section: str, d: dict) -> None:
    unknown = set(d) - ALLOWED[section]
    if unknown:
        raise ValueError(f"[{section}]: unknown key(s) {', '.join(sorted(unknown))}")


def _int(section: str, d: dict, key: str, minimum: int = 0, required: bool = True) -> int | None:
    if key not in d:
        if required:
            raise ValueError(f"[{section}] {d.get('name', '')}: missing {key}")
        return None
    v = d[key]
    if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
        raise ValueError(f"[{section}] {d.get('name', '')}: {key} must be an integer >= {minimum}, got {v!r}")
    return v


def parse(data: dict) -> Config:
    if set(data) - {"cluster", "team", "priority"}:
        raise ValueError(f"unknown section(s): {', '.join(sorted(set(data) - {'cluster', 'team', 'priority'}))}")
    c = data.get("cluster") or {}
    _check_keys("cluster", c)
    cluster = Cluster(
        name=str(c.get("name", "lab")), gpu_nodes=_int("cluster", c, "gpu_nodes", 1),
        gpus_per_node=_int("cluster", c, "gpus_per_node", 1),
        **{k: str(c[k]) for k in ("gpu_resource", "node_pool_label", "node_pool", "flavor") if k in c})

    teams, seen = [], set()
    for t in data.get("team", []):
        _check_keys("team", t)
        name = str(t.get("name", ""))
        if not NAME.match(name) or name in seen:
            raise ValueError(f"[team]: invalid or duplicate name {name!r}")
        seen.add(name)
        teams.append(Team(name, _int("team", t, "gpus"), _int("team", t, "can_borrow", required=False),
                          _int("team", t, "lends", required=False), _int("team", t, "weight", 1, required=False) or 1,
                          str(t.get("description", ""))))
    if not teams:
        raise ValueError("no [[team]] defined")

    prios = []
    for p in data.get("priority", []):
        _check_keys("priority", p)
        if not NAME.match(str(p.get("name", ""))):
            raise ValueError(f"[priority]: invalid name {p.get('name')!r}")
        prios.append(Priority(p["name"], _int("priority", p, "value"), str(p.get("description", ""))))

    warnings = []
    guaranteed = sum(t.gpus for t in teams)
    if guaranteed > cluster.gpus:
        raise ValueError(f"guaranteed GPUs ({guaranteed}) exceed the cluster ({cluster.gpus}): "
                         "a guarantee that cannot be met is not a guarantee")
    if guaranteed < cluster.gpus:
        warnings.append(f"{cluster.gpus - guaranteed} GPU(s) are nobody's guarantee; only borrowing can use them")
    for t in teams:
        if t.lends is not None and t.lends > t.gpus:
            raise ValueError(f"team {t.name}: lends {t.lends} GPUs but is only guaranteed {t.gpus}")
        lendable = sum(o.gpus if o.lends is None else o.lends for o in teams if o is not t)
        if t.can_borrow and t.can_borrow > lendable:
            warnings.append(f"team {t.name}: can_borrow {t.can_borrow} but the others lend at most {lendable}")
    return Config(cluster, teams, prios, warnings)


def load(path: str | Path) -> Config:
    try:
        data = tomllib.loads(Path(path).read_text())
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: {e}") from None
    return parse(data)
