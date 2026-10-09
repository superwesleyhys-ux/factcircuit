"""End-to-end checks of the command-line surface: exit codes, messages, outputs.

Offline commands run against the bundled examples. Model-backed commands are
checked only up to the point where a model transport would be needed.
"""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from newsverify.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def run(argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, prog="factcircuit")
    return code, out.getvalue(), err.getvalue()


class OfflineCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="factcircuit-cli-")
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def report(self, argv, name="out.json"):
        output = self.folder / name
        code, stdout, stderr = run([*argv, "--output", str(output)])
        self.assertEqual(0, code, stderr)
        self.assertEqual(f"Wrote {output}\n", stdout)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_trace_replays_local_snapshot(self):
        report = self.report(["trace", str(EXAMPLES / "local_trace.json")])
        self.assertEqual("local_snapshot_replay", report["execution_mode"])
        self.assertEqual("not_checked", report["fact_status"])

    def test_trace_demo_runs_three_rounds(self):
        report = self.report(["trace-demo"])
        self.assertEqual(3, report["usage"]["rounds"])

    def test_score_and_compare_examples(self):
        scored = self.report(["score", str(EXAMPLES / "evaluation_gold.json"),
                              str(EXAMPLES / "evaluation_predictions.json")], "score.json")
        self.assertEqual("computed", scored["aggregate"]["status"])
        compared = self.report(["compare", str(EXAMPLES / "evaluation_gold.json"),
                                str(EXAMPLES / "comparison_baseline.json"),
                                str(EXAMPLES / "comparison_candidate.json"),
                                "--bootstrap-samples", "20", "--seed", "0"], "compare.json")
        self.assertEqual(20, compared["bootstrap_samples"])
        self.assertEqual("synthetic_no_realworld_claim", compared["conclusion"])

    def test_demo_verify_and_benchmark(self):
        demo = self.report(["demo"], "demo.json")
        verified = self.report(["verify", str(EXAMPLES / "demo.json")], "verify.json")
        self.assertEqual(demo["status"], verified["status"])
        bench = self.report(["benchmark", str(EXAMPLES / "benchmark.json")], "bench.json")
        self.assertTrue(bench["all_policy_expectations_matched"])

    def test_stdout_when_no_output_requested(self):
        code, stdout, _ = run(["trace", str(EXAMPLES / "local_trace.json")])
        self.assertEqual(0, code)
        self.assertEqual("local_snapshot_replay", json.loads(stdout)["execution_mode"])

    def test_output_directory_is_created(self):
        nested = self.folder / "a" / "b" / "trace.json"
        code, _, _ = run(["trace", str(EXAMPLES / "local_trace.json"), "--output", str(nested)])
        self.assertEqual(0, code)
        self.assertTrue(nested.is_file())

    def test_output_may_not_overwrite_input(self):
        source = EXAMPLES / "local_trace.json"
        before = source.read_bytes()
        code, _, stderr = run(["trace", str(source), "--output", str(source)])
        self.assertEqual(2, code)
        self.assertIn("output must differ from input", stderr)
        self.assertEqual(before, source.read_bytes())

    def test_every_subcommand_has_help_text(self):
        with self.assertRaises(SystemExit), redirect_stdout(StringIO()) as out:
            main(["--help"], prog="factcircuit")
        text = out.getvalue()
        for command in ("quickstart", "demo", "trace-demo", "trace", "trace-model", "early-risk",
                        "double-loop", "verify", "benchmark", "score", "compare", "trace-news"):
            self.assertRegex(text, rf"\n\s+{command}\s+\S")


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="factcircuit-cli-")
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def write(self, name, payload):
        path = self.folder / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        return str(path)

    def assert_rejected(self, argv, message):
        code, stdout, stderr = run(argv)
        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn(message, stderr)
        self.assertNotIn("__init__", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_missing_file(self):
        self.assert_rejected(["trace", str(self.folder / "missing.json")], "No such file")

    def test_invalid_json(self):
        self.assert_rejected(["trace", self.write("bad.json", "not json")], "Expecting value")

    def test_target_fields_are_named(self):
        self.assert_rejected(["trace", self.write("t.json", {"target": {}, "rounds": []})],
                             "target is missing required field(s): id, text, as_of")

    def test_unknown_material_field_is_named(self):
        payload = {"target": {"id": "t", "text": "claim", "as_of": "2026-01-01T00:00:00Z"},
                   "rounds": [[{"id": "x"}]]}
        self.assert_rejected(["trace", self.write("m.json", payload)],
                             "rounds[0][0] has unknown field(s): id")

    def test_unknown_config_field_is_named(self):
        payload = {"target": {"id": "t", "text": "claim", "as_of": "2026-01-01T00:00:00Z"},
                   "rounds": [], "config": {"max_rounds": 1, "bogus": True}}
        self.assert_rejected(["trace", self.write("c.json", payload)],
                             "config has unknown field(s): bogus")

    def test_evidence_scope_list_is_accepted_everywhere(self):
        target = {"id": "t", "text": "claim", "as_of": "2026-01-01T00:00:00Z",
                  "evidence_scope": ["v1"]}
        code, stdout, _ = run(["trace", self.write("s.json", {"target": target, "rounds": []})])
        self.assertEqual(0, code)
        self.assertEqual(["v1"], json.loads(stdout)["target"]["evidence_scope"])

    def test_bootstrap_sample_bounds(self):
        self.assert_rejected(["compare", str(EXAMPLES / "evaluation_gold.json"),
                              str(EXAMPLES / "comparison_baseline.json"),
                              str(EXAMPLES / "comparison_candidate.json"),
                              "--bootstrap-samples", "0"], "from 20 to 10000")


class ModelCommandSetupTests(unittest.TestCase):
    """Without a transport the model commands must stop with exit 2 and a clear reason."""

    def setUp(self):
        self.which = patch("newsverify.tunnels.shutil.which", return_value=None).start()
        self.addCleanup(patch.stopall)

    def test_local_commands_report_missing_codex_before_any_work(self):
        for argv in (["trace-model", str(EXAMPLES / "model_trace.json")],
                     ["early-risk", str(EXAMPLES / "early_risk_case.json")],
                     ["double-loop", str(EXAMPLES / "double_loop_case.json")],
                     ["trace-news", str(EXAMPLES / "news_tracing.json")]):
            with self.subTest(command=argv[0]):
                code, stdout, stderr = run(argv)
                self.assertEqual(2, code)
                self.assertEqual("", stdout)
                self.assertIn("requires the Codex CLI", stderr)

    def test_api_commands_require_model_and_key(self):
        with patch.dict("os.environ", {}, clear=True):
            code, _, stderr = run(["double-loop", str(EXAMPLES / "double_loop_case.json"),
                                   "--tunnel", "api"])
            self.assertEqual(2, code)
            self.assertIn("--model or OPENAI_MODEL", stderr)
            code, _, stderr = run(["double-loop", str(EXAMPLES / "double_loop_case.json"),
                                   "--tunnel", "api", "--model", "x"])
            self.assertEqual(2, code)
            self.assertIn("OPENAI_API_KEY", stderr)

    def test_anthropic_tunnel_requires_model_then_key(self):
        with patch.dict("os.environ", {}, clear=True):
            code, _, stderr = run(["trace-model", str(EXAMPLES / "model_trace.json"),
                                   "--tunnel", "anthropic"])
            self.assertEqual(2, code)
            self.assertIn("ANTHROPIC_MODEL", stderr)
            code, _, stderr = run(["trace-model", str(EXAMPLES / "model_trace.json"),
                                   "--tunnel", "anthropic", "--model", "claude-model"])
            self.assertEqual(2, code)
            self.assertIn("ANTHROPIC_API_KEY", stderr)

    def test_double_loop_subcommand_forwards_options(self):
        expected = {"errors": [], "marker": "forwarded"}
        with patch("newsverify.double_loop.run_double_loop_trace", return_value=expected) as runner:
            code, stdout, _ = run(["double-loop", str(EXAMPLES / "double_loop_case.json"),
                                   "--model", "m", "--timeout", "7", "--max-model-calls", "3"])
        self.assertEqual(0, code)
        self.assertEqual(expected, json.loads(stdout))
        kwargs = runner.call_args.kwargs
        self.assertEqual(("local", "m", 7.0, 3),
                         (kwargs["tunnel"], kwargs["model"], kwargs["timeout"], kwargs["max_model_calls"]))
        self.assertEqual("double-loop-example", runner.call_args.args[0]["target"]["id"])

    def test_double_loop_errors_exit_one(self):
        with patch("newsverify.double_loop.run_double_loop_trace",
                   return_value={"errors": [{"stage": "decomposer"}]}):
            code, _, _ = run(["double-loop", str(EXAMPLES / "double_loop_case.json")])
        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
