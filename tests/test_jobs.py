import unittest
from pathlib import Path

from tenancy import config, jobs

CFG = config.load(Path(__file__).parent.parent / "tenancy.toml")


class Jobs(unittest.TestCase):
    def test_kueue(self):
        j = jobs.job(CFG, "kueue", "research", "r1", pods=2, gpus=4, priority="urgent")
        self.assertEqual(j["metadata"]["namespace"], "team-research")
        self.assertEqual(j["metadata"]["labels"]["kueue.x-k8s.io/queue-name"], "research")
        self.assertEqual(j["metadata"]["labels"]["kueue.x-k8s.io/priority-class"], "urgent")
        pod = j["spec"]["template"]["spec"]
        self.assertNotIn("schedulerName", pod)
        self.assertEqual(pod["containers"][0]["resources"]["limits"]["nvidia.com/gpu"], 4)
        self.assertEqual((j["spec"]["parallelism"], j["spec"]["completions"]), (2, 2))

    def test_kai(self):
        j = jobs.job(CFG, "kai", "training", "g", pods=3, gpus=8, priority="build", min_member=3)
        self.assertEqual(j["metadata"]["annotations"], {"kai.scheduler/batch-min-member": "3"})
        pod = j["spec"]["template"]
        self.assertEqual(pod["metadata"]["labels"]["kai.scheduler/queue"], "training")
        self.assertEqual((pod["spec"]["schedulerName"], pod["spec"]["priorityClassName"]), ("kai-scheduler", "build"))

    def test_fraction(self):
        j = jobs.job(CFG, "kai", "inference", "s", pods=2, gpu_fraction="0.5")
        t = j["spec"]["template"]
        self.assertEqual(t["metadata"]["annotations"], {"gpu-fraction": "0.5"})
        self.assertNotIn("nvidia.com/gpu", t["spec"]["containers"][0]["resources"]["limits"])

    def test_errors(self):
        for kw, msg in [({"scheduler": "kueue", "gpu_fraction": "0.5"}, "KAI feature"),
                        ({"scheduler": "kueue", "min_member": 2}, "for KAI"),
                        ({"scheduler": "kai", "min_member": 5}, "between 1 and"),
                        ({"scheduler": "kai", "gpu_fraction": "1.5"}, "between 0 and 1"),
                        ({"scheduler": "kueue", "priority": "train"}, "unknown Kueue priority"),
                        ({"scheduler": "kai", "priority": "urgent"}, "unknown KAI priority"),
                        ({"scheduler": "slurm"}, "unknown scheduler")]:
            with self.subTest(kw=kw), self.assertRaisesRegex(ValueError, msg):
                args = {"pods": 2, **kw}
                jobs.job(CFG, args.pop("scheduler"), "research", "x", **args)


if __name__ == "__main__":
    unittest.main()
