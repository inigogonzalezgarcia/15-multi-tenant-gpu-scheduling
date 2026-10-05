import contextlib
import io
import json
import unittest
from pathlib import Path

from tenancy import status
from tenancy.cli import main

FIX = Path(__file__).parent / "fixtures" / "synthetic"


class Kueue(unittest.TestCase):
    def test_teams(self):
        t = status.kueue(status.load_saved(FIX, "clusterqueues"), status.load_saved(FIX, "workloads"))
        self.assertEqual((t["training"].guaranteed, t["training"].borrow_limit, t["training"].gpus_in_use), (16, 8, 16))
        self.assertEqual((t["training"].admitted, t["training"].pending), (["training-1"], ["training-2"]))
        self.assertEqual(t["training"].preemptions, {"InClusterQueue": 1})
        self.assertIn("insufficient unused quota", t["training"].pending_reason["training-2"])
        self.assertEqual(t["research"].preemptions, {"InCohortReclamation": 2})
        self.assertEqual(t["inference"].admitted, ["inference-1"])
        self.assertIn("InCohortReclamation=2", status.table_kueue(t))

    def test_cause_gone_after_readmission(self):
        # admitted again: Kueue sets Preempted=False (reason QuotaReserved); the count stays, the cause does not
        wl = {"metadata": {"name": "w", "ownerReferences": [{"name": "research-1"}]}, "spec": {"queueName": "research"},
              "status": {"conditions": [{"type": "Admitted", "status": "True"},
                                        {"type": "Preempted", "status": "False", "reason": "QuotaReserved"}],
                         "schedulingStats": {"evictions": [{"reason": "Preempted", "underlyingCause": "", "count": 1}]}}}
        t = status.kueue({"items": []}, {"items": [wl]})
        self.assertEqual(t["research"].preemptions, {"Preempted": 1})


class Kai(unittest.TestCase):
    def test_teams(self):
        t = status.kai(status.load_saved(FIX, "pods"))
        self.assertEqual(sorted(t), ["inference", "training"])  # finished and non-KAI pods ignored
        inf = t["inference"]
        self.assertEqual((inf.running_pods, inf.gpus_running), (2, "1"))
        self.assertEqual(inf.shared_gpus, {"lab-worker/g1": ["shared-a", "shared-b"]})
        tr = t["training"]
        self.assertEqual((tr.gpus_running, tr.jobs_running, tr.jobs_pending), ("16", {"loose": 2}, {"gang": 2, "loose": 1}))
        self.assertIn("gang 0/2", status.table_kai(t))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["--config", str(FIX.parent.parent.parent / "tenancy.toml"), *args])
        return rc, out.getvalue()

    def test_status_from_files(self):
        rc, out = self.run_cli("status", "kueue", "--from", str(FIX), "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["research"]["preemptions"], {"InCohortReclamation": 2})
        rc, out = self.run_cli("status", "kai", "--from", str(FIX))
        self.assertIn("inference", out)

    def test_check_and_job(self):
        rc, out = self.run_cli("check")
        self.assertIn("32 GPUs", out)
        rc, out = self.run_cli("job", "kueue", "research", "r1", "--gpus", "4")
        self.assertIn("kueue.x-k8s.io/queue-name: research", out)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli("job", "kueue", "nobody", "x")[0], 2)


if __name__ == "__main__":
    unittest.main()
