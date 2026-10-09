"""Preregistered head-to-head runner: sealing, arm execution and scoring."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import head_to_head  # noqa: E402
from test_double_loop import D1, D2, ScriptedTransport, script  # noqa: E402

USAGE = {"input_tokens": 100, "output_tokens": 20}


def case(identifier):
    return {"id": identifier,
            "target": {"id": "target", "text": "The measured result was 30 units.",
                       "as_of": "2026-01-03T00:00:00Z", "source_version_id": "notice"},
            "materials": deepcopy([D1, D2]), "initial_version_ids": ["notice"],
            "config": {"max_rounds": 4, "max_documents": 4, "max_decomposition_calls": 8,
                       "reanalyze_existing_versions": True}}  # the shared script is the legacy sequence


CASES = {"benchmark_id": "fixture-h2h", "cases": [case("c1"), case("c2")]}
GOLD = {"benchmark_id": "fixture-h2h",
        "labels": {"c1": {"truth": "supported", "original_version_ids": ["record"]},
                   "c2": {"truth": "supported", "original_version_ids": ["record"]}}}


class DirectTransport:
    kind, model, reasoning_effort = "local", "fixture-model", "medium"

    def __init__(self, value):
        self.value, self.calls, self.packets = value, [], []

    def generate(self, stage, instructions, packet, schema):
        self.packets.append(packet)
        self.calls.append({"stage": stage, "success": True, "status": "completed", "usage": USAGE})
        return self.value


def run(argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = head_to_head.main(argv)
    return code, out.getvalue(), err.getvalue()


class HeadToHeadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="h2h-")
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.cases = self.folder / "cases.json"
        self.gold = self.folder / "sealed" / "gold.json"
        self.gold.parent.mkdir()
        self.cases.write_text(json.dumps(CASES), encoding="utf-8")
        self.gold.write_text(json.dumps(GOLD), encoding="utf-8")
        self.run_dir = self.folder / "run-001"

    def register(self):
        code, stdout, stderr = run(["register", str(self.cases), "--gold", str(self.gold),
                                    "--output", str(self.run_dir)])
        self.assertEqual(0, code, stderr)
        return json.loads((self.run_dir / "REGISTRATION.json").read_text())

    def test_register_seals_gold_by_hash_without_copying_it(self):
        registration = self.register()
        self.assertEqual(["c1", "c2"], registration["case_order"])
        self.assertEqual([["direct", "harness"], ["harness", "direct"]], registration["arm_order"])
        self.assertEqual(head_to_head.sha256_file(self.gold), registration["gold_sha256"])
        self.assertEqual(2.0, registration["max_token_ratio"])
        self.assertNotIn("labels", (self.run_dir / "REGISTRATION.json").read_text())
        self.assertEqual(["REGISTRATION.json"], [p.name for p in self.run_dir.iterdir()])

    def test_register_refuses_gold_inside_repository(self):
        inside = head_to_head.ROOT / "examples" / "head_to_head_gold.example.json"
        code, _, stderr = run(["register", str(self.cases), "--gold", str(inside),
                               "--output", str(self.run_dir)])
        self.assertEqual(2, code)
        self.assertIn("outside the repository", stderr)

    def transports(self):
        def factory(tunnel, model, effort, timeout):
            self.settings.append((tunnel, model, effort, timeout))
            # registered order: case 1 direct, harness; case 2 harness, direct
            if len(self.settings) in (1, 4):
                return DirectTransport({"verdict": "unresolved", "basis": [],
                                        "original_version_ids": [], "rationale": "cannot tell"})
            return ScriptedTransport(script())
        self.settings = []
        return factory

    def run_both(self):
        with patch.object(head_to_head, "make_transport", side_effect=self.transports()):
            code, _, stderr = run(["run", str(self.run_dir), "--cases", str(self.cases),
                                   "--tunnel", "local", "--model", "fixture-model"])
        return code, stderr

    def test_run_executes_both_arms_in_registered_order_and_scores_a_registered_win(self):
        self.register()
        code, stderr = self.run_both()
        self.assertEqual(0, code, stderr)
        self.assertEqual([("local", "fixture-model", None, 180.0)] * 4, self.settings)
        direct = json.loads((self.run_dir / "predictions-direct.json").read_text())
        harness = json.loads((self.run_dir / "predictions-harness.json").read_text())
        self.assertEqual(["unresolved", "unresolved"], [r["verdict"] for r in direct["results"]])
        self.assertEqual(["supported", "supported"], [r["verdict"] for r in harness["results"]])
        self.assertEqual([["record"], ["record"]], [r["original_version_ids"] for r in harness["results"]])
        self.assertTrue(all(r["valid"] for r in harness["results"]))
        self.assertEqual(120, direct["results"][0]["tokens"]["total_tokens"])
        self.assertEqual(6, harness["results"][0]["tokens"]["calls"])
        self.assertNotIn("labels", (self.run_dir / "predictions-harness.json").read_text())

        code, stdout, stderr = run(["score", str(self.run_dir), "--cases", str(self.cases), "--gold", str(self.gold)])
        self.assertEqual(0, code, stderr)
        summary = json.loads((self.run_dir / "SUMMARY.json").read_text())
        self.assertEqual(0.0, summary["direct"]["accuracy"])
        self.assertEqual(1.0, summary["harness"]["accuracy"])
        self.assertEqual(2, summary["harness"]["origin_correct"])
        self.assertEqual(2, summary["direct"]["abstained"])
        self.assertTrue(summary["verdict"]["accuracy_strictly_higher"])
        self.assertTrue(summary["verdict"]["all_harness_outputs_valid"])
        self.assertAlmostEqual(summary["harness"]["total_tokens"] / summary["direct"]["total_tokens"],
                               summary["verdict"]["token_ratio"])
        self.assertLess(summary["verdict"]["token_ratio"], 2.0)
        self.assertTrue(summary["verdict"]["within_token_ratio"])
        self.assertTrue(summary["verdict"]["harness_wins_by_registered_rule"])
        self.assertIn("Harness wins by the registered rule: True", stdout)
        self.assertEqual([True, True], [row["harness_correct"] for row in summary["paired_cases"]])
        self.assertTrue((self.run_dir / "SUMMARY.md").is_file())

    def test_direct_arm_only_sees_cutoff_eligible_materials_and_validates_quotes(self):
        later = dict(D2, version_id="later", available_at="2026-02-01T00:00:00Z",
                     published_at="2026-02-01T00:00:00Z", retrieved_at="2026-02-02T00:00:00Z")
        item = case("c1"); item["materials"].append(later)
        transport = DirectTransport({"verdict": "supported", "basis": [{"version_id": "later", "quote": "30 units"}],
                                     "original_version_ids": [], "rationale": "x"})
        result = head_to_head.run_direct(item, transport)
        self.assertEqual(["notice", "record"], [m["version_id"] for m in transport.packets[0]["materials"]])
        self.assertEqual(["later"], transport.packets[0]["excluded_version_ids"])
        self.assertFalse(result["valid"])
        self.assertIn("unavailable material version", result["error"])
        transport = DirectTransport({"verdict": "supported", "basis": [], "original_version_ids": [],
                                     "rationale": "x"})
        self.assertIn("requires at least one quoted basis", head_to_head.run_direct(item, transport)["error"])

    def test_score_refuses_tampered_gold_or_changed_cases(self):
        self.register()
        self.run_both()
        self.gold.write_text(json.dumps({**GOLD, "labels": {**GOLD["labels"], "c1": {
            "truth": "unresolved", "original_version_ids": None}}}), encoding="utf-8")
        code, _, stderr = run(["score", str(self.run_dir), "--cases", str(self.cases), "--gold", str(self.gold)])
        self.assertEqual(2, code)
        self.assertIn("does not match the registration", stderr)
        self.cases.write_text(json.dumps({**CASES, "benchmark_id": "edited"}), encoding="utf-8")
        with patch.object(head_to_head, "make_transport", side_effect=self.transports()):
            code, _, stderr = run(["run", str(self.run_dir), "--cases", str(self.cases)])
        self.assertEqual(2, code)
        self.assertIn("changed since registration", stderr)

    def test_invalid_outputs_count_as_wrong_and_never_retry(self):
        self.register()
        calls = []

        def factory(tunnel, model, effort, timeout):
            calls.append(1)
            if len(calls) in (1, 4):
                return DirectTransport({"verdict": "supported", "basis": [], "original_version_ids": [],
                                        "rationale": "no quotes"})
            return ScriptedTransport(script()[:1])  # harness runs out of scripted answers
        with patch.object(head_to_head, "make_transport", side_effect=factory):
            code, _, _ = run(["run", str(self.run_dir), "--cases", str(self.cases)])
        self.assertEqual(1, code)
        self.assertEqual(4, len(calls))
        code, _, stderr = run(["score", str(self.run_dir), "--cases", str(self.cases), "--gold", str(self.gold)])
        self.assertEqual(0, code, stderr)
        summary = json.loads((self.run_dir / "SUMMARY.json").read_text())
        self.assertEqual(2, summary["direct"]["invalid_outputs"])
        self.assertEqual(0, summary["direct"]["correct"])
        self.assertFalse(summary["verdict"]["harness_wins_by_registered_rule"])


if __name__ == "__main__":
    unittest.main()
