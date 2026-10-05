import json
import unittest
from pathlib import Path

from tenancy import config, render, yamlout
from tenancy.cli import rendered

ROOT = Path(__file__).parent.parent
CFG = config.load(ROOT / "tenancy.toml")


def by_kind(docs, kind):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


class Kueue(unittest.TestCase):
    def test_cluster_queues(self):
        cqs = by_kind(render.kueue(CFG), "ClusterQueue")
        self.assertEqual(sorted(cqs), ["inference", "research", "training"])
        gpu = lambda name: cqs[name]["spec"]["resourceGroups"][0]["flavors"][0]["resources"][2]
        self.assertEqual(gpu("training"), {"name": "nvidia.com/gpu", "nominalQuota": 16, "borrowingLimit": 8})
        self.assertEqual(gpu("inference"), {"name": "nvidia.com/gpu", "nominalQuota": 8, "borrowingLimit": 0, "lendingLimit": 0})
        spec = cqs["research"]["spec"]
        self.assertEqual(spec["cohortName"], "org")
        self.assertEqual(spec["preemption"], {"reclaimWithinCohort": "Any", "withinClusterQueue": "LowerPriority"})
        self.assertEqual(spec["namespaceSelector"], {"matchLabels": {"tenancy.lab/team": "research"}})

    def test_flavor_queues_priorities(self):
        docs = render.kueue(CFG)
        self.assertEqual(by_kind(docs, "ResourceFlavor")["lab-gpu"]["spec"]["nodeLabels"],
                         {"run.ai/simulated-gpu-node-pool": "default"})
        self.assertEqual({n: d["value"] for n, d in by_kind(docs, "WorkloadPriorityClass").items()},
                         {"batch": 100, "urgent": 1000})
        lq = by_kind(docs, "LocalQueue")["inference"]
        self.assertEqual((lq["metadata"]["namespace"], lq["spec"]["clusterQueue"]), ("team-inference", "inference"))


class Kai(unittest.TestCase):
    def test_queues(self):
        qs = by_kind(render.kai(CFG), "Queue")
        self.assertEqual(qs["lab"]["spec"]["resources"]["gpu"], {"quota": 32, "limit": 32, "overQuotaWeight": 1})
        self.assertEqual(qs["training"]["spec"]["parentQueue"], "lab")
        self.assertEqual(qs["training"]["spec"]["resources"]["gpu"], {"quota": 16, "limit": 24, "overQuotaWeight": 2})
        self.assertEqual(qs["inference"]["spec"]["resources"]["gpu"]["limit"], 8)
        self.assertEqual(qs["research"]["spec"]["resources"]["cpu"], {"quota": -1, "limit": -1, "overQuotaWeight": 1})


class Yaml(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(yamlout.scalar("plain-name"), "plain-name")
        for s in ("10m", "true", "a: b", "", "busybox:1.36", "-1"):
            self.assertEqual(json.loads(yamlout.scalar(s)), s)
        self.assertEqual((yamlout.scalar(16), yamlout.scalar(True), yamlout.scalar(None)), ("16", "true", "null"))

    def test_structure(self):
        doc = {"a": {"b": [1, {"c": "x", "d": []}], "e": {}}, "f": [[1, 2]]}
        self.assertEqual(yamlout.dump(doc), "a:\n  b:\n  - 1\n  - c: x\n    d: []\n  e: {}\nf:\n- - 1\n  - 2\n")

    def test_committed_manifests_are_current(self):
        for name, text in rendered(CFG).items():
            self.assertEqual(text, (ROOT / "manifests" / name).read_text(), f"run: python -m tenancy render ({name})")


if __name__ == "__main__":
    unittest.main()
