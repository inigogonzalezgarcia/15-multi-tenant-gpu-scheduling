import tomllib
import unittest
from pathlib import Path

from tenancy import config

ROOT = Path(__file__).parent.parent


def cfg(**changes):
    data = tomllib.loads((ROOT / "tenancy.toml").read_text())
    for team, (key, value) in changes.items():
        t = next(x for x in data["team"] if x["name"] == team)
        if value is None:
            t.pop(key, None)
        else:
            t[key] = value
    return config.parse(data)


class Config(unittest.TestCase):
    def test_repo_config(self):
        c = config.load(ROOT / "tenancy.toml")
        self.assertEqual(c.cluster.gpus, 32)
        self.assertEqual([(t.name, t.gpus, t.max_gpus, t.lends) for t in c.teams],
                         [("training", 16, 24, None), ("research", 8, 16, None), ("inference", 8, 8, 0)])
        self.assertEqual(c.warnings, [])
        self.assertEqual(c.team("research").namespace, "team-research")

    def test_guarantees_cannot_exceed_the_cluster(self):
        with self.assertRaisesRegex(ValueError, "exceed the cluster"):
            cfg(training=("gpus", 20))

    def test_warnings(self):
        self.assertIn("nobody's guarantee", cfg(training=("gpus", 12)).warnings[0])
        self.assertIn("lend at most 8", cfg(training=("can_borrow", 16)).warnings[0])

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "lends 9 GPUs"):
            cfg(inference=("lends", 9))
        with self.assertRaisesRegex(ValueError, "unknown key"):
            cfg(research=("borrow", 4))
        with self.assertRaisesRegex(ValueError, "integer >= 0"):
            cfg(research=("gpus", -1))
        with self.assertRaisesRegex(ValueError, "missing gpus"):
            cfg(research=("gpus", None))
        with self.assertRaisesRegex(ValueError, "unknown team"):
            config.load(ROOT / "tenancy.toml").team("nobody")
        with self.assertRaisesRegex(ValueError, "no \\[\\[team\\]\\]"):
            config.parse({"cluster": {"gpu_nodes": 1, "gpus_per_node": 1}})


if __name__ == "__main__":
    unittest.main()
