"""The standalone entry point defaults to the local JSON harness, never API."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ENTRY = Path(__file__).resolve().parents[1] / "integrations/news-tracing-master/main.py"
spec = spec_from_file_location("standalone_news_entry_fixture", ENTRY)
entry = module_from_spec(spec)
spec.loader.exec_module(entry)


class StandaloneNewsCliTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_local_without_optional_api_dependencies_preserves_json(self):
        expected = {"summary": {"failed": 0, "partial": 0}, "marker": "local-json"}
        with patch.dict(sys.modules, {"openai": None, "rich": None, "dotenv": None}), \
                patch("newsverify.news_tracing_runner.run_news_tracing", return_value=expected) as trace, \
                patch.object(entry, "_run_api") as api, redirect_stdout(StringIO()) as stdout:
            status = entry.main(["https://example.invalid/news"])
        self.assertEqual(0, status)
        self.assertEqual(expected, json.loads(stdout.getvalue()))
        self.assertEqual("local", trace.call_args.kwargs["tunnel"])
        self.assertIsNone(trace.call_args.kwargs["model"])
        self.assertEqual("https://example.invalid/news", trace.call_args.args[0]["news"][0]["url"])
        api.assert_not_called()

    def test_explicit_other_local_model_and_input_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "out.json"
            payload = {"news": [{"id": "fixture", "text": "Source text."}]}
            source.write_text(json.dumps(payload))
            expected = {"summary": {"failed": 0, "partial": 0}}
            with patch("newsverify.news_tracing_runner.run_news_tracing", return_value=expected) as trace:
                status = entry.main(["--input", str(source), "--output", str(output),
                                     "--model", "gpt-5.6-luna", "--reasoning-effort", "low"])
            self.assertEqual(0, status)
            self.assertEqual(payload, trace.call_args.args[0])
            self.assertEqual("gpt-5.6-luna", trace.call_args.kwargs["model"])
            self.assertEqual(expected, json.loads(output.read_text()))
            self.assertEqual(payload, json.loads(source.read_text()))

    def test_local_failure_does_not_fall_back_to_api(self):
        with patch("newsverify.cli.main", return_value=1), patch.object(entry, "_run_api") as api:
            self.assertEqual(1, entry.main(["Source text."]))
        api.assert_not_called()

    def test_api_workflow_requires_explicit_tunnel(self):
        with patch.object(entry, "_run_api", return_value=0) as api, \
                patch.object(entry, "_run_local") as local:
            self.assertEqual(0, entry.main(["News", "--tunnel", "api", "--model", "provider-model"]))
        api.assert_called_once()
        self.assertEqual("provider-model", api.call_args.args[0].model)
        local.assert_not_called()

    def test_json_input_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text('{"news": []}')
            with redirect_stderr(StringIO()), patch("newsverify.news_tracing_runner.run_news_tracing") as trace:
                status = entry.main(["--input", str(source), "--output", str(source)])
            self.assertEqual(2, status)
            trace.assert_not_called()
            self.assertEqual('{"news": []}', source.read_text())


if __name__ == "__main__":
    unittest.main()
