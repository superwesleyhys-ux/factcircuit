"""Offline real-runner checks: immutable logs and stop after transport failure."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import run_test as run


class FakeTunnel:
    def __init__(self, **kwargs):
        self.calls, self.returned, self.arguments = [], [], []
        self.fail = False

    def generate(self, stage, instructions, packet, schema):
        self.arguments.append((packet, schema))
        self.calls.append({"stage": stage, "success": not self.fail, "usage": None})
        if self.fail:
            raise run.TunnelError("synthetic transport failure")
        answer = {"items": [{"value": "original"}]}
        self.returned.append(answer)
        return answer


class RuntimeChecks(unittest.TestCase):
    def test_later_nested_changes_do_not_rewrite_original_request_or_response(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(run, "LocalTunnel", FakeTunnel):
            journal = run.JournalTransport(Path(folder))
            packet = {"prior_decisions": []}
            schema = {"properties": {"value": {"type": "string"}}}
            answer = journal.generate("first", "instruction", packet, schema)
            self.assertIs(answer, journal.base.returned[0])
            self.assertIs(packet, journal.base.arguments[0][0])
            self.assertIs(schema, journal.base.arguments[0][1])
            packet["prior_decisions"].append({"action": "later"})
            schema["properties"]["value"]["type"] = "changed"
            answer["items"][0]["value"] = "normalized by host"
            journal.generate("second", "instruction", {}, {})
            records = json.loads((Path(folder) / "model-io.json").read_text())
            self.assertEqual([], records[0]["packet"]["prior_decisions"])
            self.assertEqual("string", records[0]["schema"]["properties"]["value"]["type"])
            self.assertEqual("original", records[0]["response"]["items"][0]["value"])
            self.assertEqual(2, len(json.loads((Path(folder) / "calls.json").read_text())))

    def test_failure_blocks_later_dispatches_and_preserves_actual_attempt(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(run, "LocalTunnel", FakeTunnel):
            journal = run.JournalTransport(Path(folder))
            journal.base.fail = True
            with self.assertRaisesRegex(run.TunnelError, "synthetic"):
                journal.generate("search_plan", "instruction", {}, {})
            journal.base.fail = False
            for stage in ("direct_response", "synthesis", "decompose", "shared_final_forecast"):
                with self.assertRaisesRegex(run.TunnelError, "Arm stopped"):
                    journal.generate(stage, "instruction", {}, {})
            self.assertEqual(1, len(journal.base.arguments))
            self.assertEqual(1, len(journal.calls))
            records = json.loads((Path(folder) / "model-io.json").read_text())
            self.assertEqual(1, len(records))
            self.assertEqual("failed", records[0]["status"])
            self.assertIn("finished_at", records[0])
            blocked = json.loads((Path(folder) / "blocked-dispatches.json").read_text())
            self.assertEqual(4, len(blocked))
            self.assertTrue(all(item["blocked_by"] == "search_plan" for item in blocked))


if __name__ == "__main__":
    unittest.main()
