"""Synthetic temporal admission checks; these are not model accuracy tests."""
import unittest
from types import SimpleNamespace
from newsverify.news_tracing_runner import _HistoricalCollectorGuard, _SnapshotDocument, run_news_tracing
from newsverify.provenance import MaterialVersion
from newsverify.news_client import TracingClient


class HistoricalCollectorGuardTests(unittest.TestCase):
    def doc(self, date):
        return _SnapshotDocument(MaterialVersion("test", "https://example.org/" + date[:4],
            "SYNTHETIC " + date, "2026-09-14T00:00:00Z", date, date, "Synthetic archive fixture"))

    def test_future_document_never_reaches_research_packet(self):
        old, new = self.doc("2023-01-01T00:00:00Z"), self.doc("2025-01-01T00:00:00Z")
        collector = SimpleNamespace(documents={d.url: d for d in [old, new]}, errors=[],
            fetch=lambda url: new, search=lambda query, limit: [old, new])
        guarded = _HistoricalCollectorGuard(collector, "2024-12-31T23:59:59Z")
        self.assertIsNone(guarded.fetch(new.url))
        self.assertEqual([old], guarded.search("synthetic", 3))
        self.assertEqual([old.url], list(guarded.documents))
        packet = TracingClient(None, guarded)._evidence()
        self.assertEqual([old.content], [row["content"] for row in packet])
        self.assertEqual(1, len(collector.errors))

    def test_ordinary_live_collector_cannot_backdate(self):
        transport = SimpleNamespace(kind="local", model="synthetic", reasoning_effort="low", calls=[])
        report = run_news_tracing({"news": [{"id": "x", "text": "Synthetic", "as_of": "2024-12-31T23:59:59Z"}]},
            transport=transport, collector_factory=lambda **kwargs: SimpleNamespace())
        self.assertEqual(0, report["execution"]["model_call_count"])
        self.assertEqual(1, report["summary"]["failed"])
        self.assertIn("exact cutoff", report["results"][0]["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
