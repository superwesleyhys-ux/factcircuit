"""Keyword-tracing CLI dispatch and report safety without model or network I/O."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factcircuit.cli import main


class KeywordCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "input.json"
        self.output = self.directory / "nested" / "output.json"
        self.payload = {"url": "https://example.invalid/news"}
        self.source.write_text(json.dumps(self.payload), encoding="utf-8")
        self.arguments = ["trace-keywords", str(self.source), "--output", str(self.output)]

    def test_defaults_and_complete_output_are_forwarded_without_summary(self):
        expected = {"status": "completed", "marker": "直接原文", "model_calls": 2}
        with patch("newsverify.keyword_tracing.run_keyword_trace", return_value=expected) as trace, \
                redirect_stdout(StringIO()) as stdout:
            status = main(self.arguments)
        self.assertEqual(0, status)
        trace.assert_called_once_with(self.payload, model=None, reasoning_effort="low", timeout=180)
        self.assertEqual(expected, json.loads(self.output.read_text(encoding="utf-8")))
        self.assertIn("直接原文", self.output.read_text(encoding="utf-8"))
        self.assertIn(str(self.output), stdout.getvalue())
        self.assertEqual(self.payload, json.loads(self.source.read_text(encoding="utf-8")))

    def test_optional_input_fields_and_explicit_model_configuration(self):
        payload = dict(self.payload, selectors=["拟收购", {"start": 8, "end": 10}],
                       as_of="2026-09-16T00:00:00Z",
                       limits={"max_keywords": 24, "max_associations": 12, "max_searches": 2})
        self.source.write_text(json.dumps(payload), encoding="utf-8")
        with patch("newsverify.keyword_tracing.run_keyword_trace", return_value={"status": "completed"}) as trace, \
                redirect_stdout(StringIO()):
            status = main(self.arguments + ["--model", "test-local-model", "--reasoning-effort", "medium",
                                           "--timeout", "12.5"])
        self.assertEqual(0, status)
        trace.assert_called_once_with(payload, model="test-local-model", reasoning_effort="medium", timeout=12.5)

    def test_incomplete_execution_is_saved_and_returns_one(self):
        for state in ("failed", "partial"):
            with self.subTest(state=state):
                destination = self.directory / (state + ".json")
                expected = {"status": state, "error": "retained failure receipt"}
                with patch("newsverify.keyword_tracing.run_keyword_trace", return_value=expected), \
                        redirect_stdout(StringIO()):
                    status = main(["trace-keywords", str(self.source), "--output", str(destination)])
                self.assertEqual(1, status)
                self.assertEqual(expected, json.loads(destination.read_text(encoding="utf-8")))

    def test_existing_report_and_input_are_never_overwritten(self):
        destination = self.directory / "previous.json"
        destination.write_text("previous attempt", encoding="utf-8")
        for path in (destination, self.source):
            with self.subTest(path=path):
                previous = path.read_bytes()
                with patch("newsverify.keyword_tracing.run_keyword_trace") as trace, \
                        redirect_stderr(StringIO()) as stderr:
                    status = main(["trace-keywords", str(self.source), "--output", str(path)])
                self.assertEqual(2, status)
                self.assertIn("must be a new file", stderr.getvalue())
                trace.assert_not_called()
                self.assertEqual(previous, path.read_bytes())

    def test_report_created_during_trace_is_preserved(self):
        def trace(_payload, **_settings):
            self.output.parent.mkdir(parents=True)
            self.output.write_text("concurrent report", encoding="utf-8")
            return {"status": "completed"}

        with patch("newsverify.keyword_tracing.run_keyword_trace", side_effect=trace), \
                redirect_stderr(StringIO()):
            status = main(self.arguments)
        self.assertEqual(2, status)
        self.assertEqual("concurrent report", self.output.read_text(encoding="utf-8"))

    def test_bad_json_does_not_start_trace(self):
        self.source.write_text("{not JSON}", encoding="utf-8")
        with patch("newsverify.keyword_tracing.run_keyword_trace") as trace, \
                redirect_stderr(StringIO()):
            status = main(self.arguments)
        self.assertEqual(2, status)
        trace.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_runner_validation_error_returns_two_without_report(self):
        with patch("newsverify.keyword_tracing.run_keyword_trace", side_effect=ValueError("invalid keyword limits")), \
                redirect_stderr(StringIO()) as stderr:
            status = main(self.arguments)
        self.assertEqual(2, status)
        self.assertIn("invalid keyword limits", stderr.getvalue())
        self.assertFalse(self.output.exists())

    def test_output_is_required_and_comparison_arm_is_not_available(self):
        for arguments in (["trace-keywords", str(self.source)], self.arguments + ["--arm", "direct"]):
            with self.subTest(arguments=arguments), redirect_stderr(StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                main(arguments)
            self.assertEqual(2, raised.exception.code)

    def test_existing_phrase_command_keeps_its_comparison_arm(self):
        with patch("newsverify.phrase_tracing.run_phrase_trace", return_value={"status": "completed"}) as trace, \
                redirect_stdout(StringIO()):
            status = main(["trace-phrases", str(self.source), "--output", str(self.output), "--arm", "direct"])
        self.assertEqual(0, status)
        trace.assert_called_once_with(self.payload, arm="direct", model=None, reasoning_effort="low", timeout=180)


if __name__ == "__main__":
    unittest.main()
