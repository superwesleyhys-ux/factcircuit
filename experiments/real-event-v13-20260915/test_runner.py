"""Offline safeguards and real production orchestration; no model or network I/O."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_test as runner
import retrieval
from archive_collector import ArchivedDocument
from newsverify.double_loop import CallBudgetTransport
from newsverify.tunnels import TunnelError


def document(url, text, links=()):
    return ArchivedDocument(url=url, title="Synthetic event record", content=text,
        retrieved_at="2026-01-01T00:00:00Z", capture_at="2024-01-01T00:00:00+00:00",
        links=[{"url": u, "text": "Source"} for u in links])


SEED = document("https://example.invalid/news", "The city reported opening the bridge.",
                ("https://example.invalid/record",))
PRIMARY = document("https://example.invalid/record", "Original city record: the bridge opened.")
CASE = {"id": "fixture", "url": SEED.url, "claim": "The city reported opening the bridge."}


class Collector:
    historical_cutoff = runner.CUTOFF
    max_documents = 3
    max_searches = 0

    def __init__(self, documents=(SEED, PRIMARY)):
        self.catalog = {d.url: deepcopy(d) for d in documents}
        self.documents, self.requests, self.errors, self.archive_records = {}, [], [], {}

    def fetch(self, url):
        if url in self.documents:
            return self.documents[url]
        if sum(r["operation"] == "fetch" for r in self.requests) >= self.max_documents:
            raise AssertionError("Document cap exceeded")
        d = self.catalog.get(url)
        self.requests.append({"operation": "fetch", "url": url, "success": d is not None,
                              "final_url": d.url if d else None})
        if d:
            self.documents[d.url] = d
        return d

    def search(self, *args, **kwargs):
        raise AssertionError("No historical search service")


class Transport:
    kind, model, reasoning_effort = "local", "gpt-6-astra", "low"

    def __init__(self, responses):
        self.responses, self.calls, self.inputs = list(responses), [], []

    def generate(self, stage, instructions, packet, schema):
        self.inputs.append({"stage": stage, "packet": deepcopy(packet), "instructions": instructions,
                            "schema": deepcopy(schema)})
        row = {"stage": stage, "model": self.model, "reasoning_effort": self.reasoning_effort,
               "tunnel": self.kind, "status": "completed", "success": True,
               "usage": {"input_tokens": 10, "output_tokens": 2}}
        self.calls.append(row)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            row.update(status="failed", success=False, usage=None)
            raise response
        return response


def prediction():
    return dict(fact_status="supported", origin_url=PRIMARY.url, origin_chain=[SEED.url, PRIMARY.url],
                risk="insufficient_evidence", fabrication_established=False,
                citations=[{"url": PRIMARY.url, "quote": PRIMARY.content}], rationale="Synthetic exact record.")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for target in ("socket.create_connection", "subprocess.run", "subprocess.Popen", "newsverify.tunnels.LocalTunnel.generate"):
            blocker = patch(target, side_effect=AssertionError("Network/model execution forbidden in offline tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def collector(self):
        c = Collector()
        c.fetch(SEED.url)
        c.fetch(PRIMARY.url)
        return c

    def test_exact_final_chain_and_redirect_alias_are_valid(self):
        c = self.collector()
        value = prediction()
        runner.validate_prediction(value, c, SEED.url)
        alias = "https://example.invalid/old-record"
        c.requests.append({"operation": "fetch", "url": alias, "success": True, "final_url": PRIMARY.url})
        value["origin_chain"] = [SEED.url, alias, PRIMARY.url]
        runner.validate_prediction(value, c, SEED.url)

    def test_final_rejects_unfetched_unlinked_future_and_inexact_evidence(self):
        for failure in ("unfetched", "unlinked", "future", "quote", "schema", "cycle"):
            with self.subTest(failure=failure):
                c, value = self.collector(), prediction()
                if failure == "unfetched":
                    value["origin_url"] = "https://example.invalid/missing"
                elif failure == "unlinked":
                    c.documents[SEED.url] = replace(c.documents[SEED.url], links=[])
                elif failure == "future":
                    c.documents[PRIMARY.url] = replace(c.documents[PRIMARY.url], capture_at="2025-01-01T00:00:00+00:00")
                elif failure == "quote":
                    value["citations"][0]["quote"] = "Invented claim."
                elif failure == "schema":
                    value["fabrication_established"] = "false"
                else:
                    value["origin_chain"] += [SEED.url, PRIMARY.url]
                with self.assertRaises(ValueError):
                    runner.validate_prediction(value, c, SEED.url)

    def test_unknown_fetch_is_feedback_and_never_reaches_collector(self):
        c = Collector()
        c.fetch(SEED.url)
        transport = Transport([{"action": "fetch", "urls": ["https://example.invalid/unknown"], "rationale": "Try."},
                               {"action": "stop", "urls": [], "rationale": "Unavailable."}])
        result = retrieval.retrieve_sources(CASE, c, transport, arm="direct", directory=self.directory, cutoff=runner.CUTOFF)
        self.assertEqual([SEED.url], [r["url"] for r in c.requests])
        self.assertEqual(2, len(transport.calls))
        self.assertIn("tool_error", result["decisions"][0])
        self.assertEqual(result["decisions"][:1], transport.inputs[1]["packet"]["prior_decisions"])

    def test_retrieval_is_bounded_and_shared_raw_input_is_identical(self):
        first_packets = []
        for arm in ("direct", "harness"):
            c = Collector()
            c.fetch(SEED.url)
            transport = Transport([{"action": "fetch", "urls": [PRIMARY.url], "rationale": "Read original."},
                                   {"action": "stop", "urls": [], "rationale": "Done."}])
            result = retrieval.retrieve_sources(CASE, c, transport, arm=arm, directory=self.directory, cutoff=runner.CUTOFF)
            first_packets.append(transport.inputs[0]["packet"])
            self.assertEqual(2, len(transport.calls))
            self.assertEqual(2, len(c.requests))
            self.assertEqual([], transport.inputs[0]["packet"]["prior_decisions"])
            self.assertEqual(SEED.content, first_packets[-1]["fetched_evidence"][0]["content"])
        self.assertEqual(first_packets[0], first_packets[1])

    def test_retrieval_does_not_mutate_returned_model_response(self):
        response = {"action": "fetch", "urls": [PRIMARY.url], "rationale": "Read."}
        c = Collector()
        c.fetch(SEED.url)
        transport = Transport([response, {"action": "stop", "urls": [], "rationale": "Done."}])
        retrieval.retrieve_sources(CASE, c, transport, arm="harness", directory=self.directory, cutoff=runner.CUTOFF)
        self.assertNotIn("fetch_results", response)

    def test_journal_snapshots_and_sixteen_call_cap(self):
        base = Transport([{"value": []} for _ in range(16)])
        journal = runner.JournalTransport(self.directory, base=base)
        packet, schema = {"items": []}, {"type": "object"}
        with redirect_stdout(io.StringIO()):
            response = journal.generate("fixture", "instruction", packet, schema)
            packet["items"].append("later")
            schema["later"] = True
            response["value"].append("later")
            for _ in range(15):
                journal.generate("fixture", "instruction", {}, {})
            with self.assertRaises(TunnelError):
                journal.generate("forbidden", "instruction", {}, {})
        self.assertEqual({"items": []}, journal.records[0]["packet"])
        self.assertEqual({"type": "object"}, journal.records[0]["schema"])
        self.assertEqual({"value": []}, journal.records[0]["response"])
        self.assertEqual(16, len(base.calls))
        self.assertEqual(16, len(journal.records))
        self.assertEqual(1, len(journal.blocked_dispatches))

    def test_failed_model_dispatch_blocks_all_later_calls_without_unknown_usage_as_zero(self):
        base = Transport([TunnelError("Synthetic timeout")])
        journal = runner.JournalTransport(self.directory, base=base)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(TunnelError):
                journal.generate("failed", "", {}, {})
            with self.assertRaises(TunnelError):
                journal.generate("blocked", "", {}, {})
        self.assertEqual(1, len(base.calls))
        self.assertIsNone(journal.calls[0]["usage"])
        self.assertEqual("failed", journal.records[0]["status"])
        self.assertEqual(1, len(journal.blocked_dispatches))

    def test_full_evidence_limit_rejects_instead_of_truncating(self):
        c = self.collector()
        with patch.object(retrieval, "MAX_EVIDENCE_CHARS", len(SEED.content)):
            with self.assertRaisesRegex(ValueError, "no text truncated"):
                retrieval.full_evidence(c)
        with patch.object(runner, "MAX_EVIDENCE_CHARS", len(SEED.content)):
            with self.assertRaisesRegex(ValueError, "no text truncated"):
                runner.evidence(c)

    def test_changed_seed_fails_before_dispatch(self):
        c, transport = Collector(), Transport([])
        result = runner.run_case(CASE, "direct", self.directory / "arm", seed_hash="0" * 64,
                                 collector=c, journal=transport)
        self.assertEqual("failed", result["status"])
        self.assertEqual([], transport.calls)

    def test_direct_stub_preserves_final_assessment_and_stays_in_budget(self):
        transport = Transport([{"action": "fetch", "urls": [PRIMARY.url], "rationale": "Read."},
                               {"action": "stop", "urls": [], "rationale": "Done."}, prediction()])
        result = runner.run_case(CASE, "direct", self.directory / "arm",
            seed_hash=hashlib.sha256(SEED.content.encode()).hexdigest(), collector=Collector(), journal=transport)
        self.assertEqual("completed", result["status"], result["errors"])
        self.assertEqual(prediction(), result["prediction"])
        self.assertEqual(3, len(transport.calls))
        self.assertEqual("shared_final_forecast", transport.calls[-1]["stage"])
        self.assertTrue(result["final_valid"])
        self.assertEqual(3, result["attempted_calls"])
        self.assertTrue(result["call_cap_respected"])

    def test_valid_final_is_separate_from_native_error_and_unresolved_verdict(self):
        for failed in (False, True):
            with self.subTest(native_failed=failed):
                transport = Transport([{"action": "fetch", "urls": [PRIMARY.url], "rationale": "Read."},
                                       {"action": "stop", "urls": [], "rationale": "Done."}, prediction()])
                errors = [{"stage": "verifier", "message": "Invalid quoted evidence."}] if failed else []
                native = {"status": "failed" if failed else "completed", "errors": errors,
                          "claims": [{"text": CASE["claim"], "fact_status": "unresolved",
                                      "provenance_status": "unresolved", "errors": errors}], "research_advice": {}}
                with patch.object(runner, "run_news_tracing", return_value={"results": [native]}):
                    result = runner.run_case(CASE, "harness", self.directory / str(failed),
                        seed_hash=hashlib.sha256(SEED.content.encode()).hexdigest(),
                        collector=Collector(), journal=transport)
                self.assertTrue(result["final_valid"])
                self.assertEqual("completed_with_workflow_errors" if failed else "completed", result["status"])
                self.assertEqual(native["status"], result["workflow_native_status"])
                self.assertEqual(errors, result["native_errors"])
                self.assertEqual("shared_final_forecast", transport.calls[-1]["stage"])

    def test_run_stops_before_final_after_transport_failure(self):
        directory = self.directory / "arm"
        base = Transport([TunnelError("Synthetic failure")])
        journal = runner.JournalTransport(directory, base=base)
        with redirect_stdout(io.StringIO()):
            result = runner.run_case(CASE, "direct", directory,
                seed_hash=hashlib.sha256(SEED.content.encode()).hexdigest(), collector=Collector(), journal=journal)
        self.assertEqual("failed", result["status"])
        self.assertFalse(result["final_valid"])
        self.assertEqual(1, result["attempted_calls"])
        self.assertEqual(["direct_retrieval_decision"], [c["stage"] for c in base.calls])

    def test_main_records_initial_cache_bytes_without_loading_them_as_documents(self):
        cache = self.directory / "private-cache"
        cache.mkdir()
        body = b"offline frozen body"
        (cache / "seed.body").write_bytes(body)
        registration = {"seed_text_sha256": {CASE["id"]: hashlib.sha256(SEED.content.encode()).hexdigest()}}

        def completed(case, arm, directory, *, seed_hash):
            return {"case_id": case["id"], "arm": arm, "status": "completed", "calls": [], "sources": []}

        output = self.directory / "run"
        with patch.object(runner, "HERE", self.directory), \
                patch.object(runner, "load_setup", return_value=([CASE], registration)), \
                patch.object(runner, "run_case", side_effect=completed), \
                patch.object(runner.subprocess, "check_output", return_value="frozen-commit\n"), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(0, runner.main(["--output", str(output)]))
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual([{"path": "private-cache/seed.body", "bytes": len(body),
                           "sha256": hashlib.sha256(body).hexdigest()}], manifest["initial_cache_manifest"])
        self.assertFalse(manifest["gold_loaded"])
        self.assertFalse(manifest["models_local"])
        marker = json.loads((output / "COMPLETED.json").read_text())
        self.assertEqual(runner.sha(output / "predictions.json"), marker["results_sha256"])

    def test_harness_calls_real_production_claim_mode_and_reserves_final_call(self):
        from tests.test_news_tracing_integration import NewsFixtureTransport, COPY, WIRE, RECORD
        # The production fixture derives new edges from retained exact spans.
        docs = [document(m["url"], m["content"], [u["url"] for u in [WIRE, RECORD]
                if u["url"] in m["content"]]) for m in (COPY, WIRE, RECORD)]
        case = {"id": "event", "url": COPY["url"], "claim": "The synthetic laboratory measured 30 units."}
        collector = Collector(docs)

        class ProductionFixture(NewsFixtureTransport):
            def synthesis(self, stage):
                value = super().synthesis(stage)
                if stage == "source_trace":
                    value["sources"][0]["url"] = RECORD["url"]
                return value

            def generate(self, stage, instructions, packet, schema):
                if stage.endswith("_retrieval_decision") or stage == "shared_final_forecast":
                    self.inputs.append({"stage": stage, "packet": deepcopy(packet)})
                    self.calls.append({"stage": stage, "success": True, "status": "completed",
                                       "usage": {"input_tokens": 10, "output_tokens": 2}})
                    if stage.endswith("_retrieval_decision"):
                        return {"action": "fetch", "urls": [WIRE["url"] if len(collector.documents) == 1 else RECORD["url"]],
                                "rationale": "Follow the documented source."}
                    return {"fact_status": "supported", "origin_url": RECORD["url"],
                            "origin_chain": [COPY["url"], WIRE["url"], RECORD["url"]],
                            "risk": "insufficient_evidence", "fabrication_established": False,
                            "citations": [], "rationale": "Offline fixture."}
                return super().generate(stage, instructions, packet, schema)

        transport = ProductionFixture()
        result = runner.run_case(case, "harness", self.directory / "arm",
            seed_hash=hashlib.sha256(COPY["content"].encode()).hexdigest(), collector=collector, journal=transport)
        self.assertEqual("completed", result["status"], result["errors"])
        claim = result["workflow"]["results"][0]["claims"][0]
        self.assertEqual("original_material_located", claim["provenance_status"])
        self.assertEqual("supported", claim["fact_status"])
        self.assertEqual(16, len(transport.calls))
        self.assertEqual(3, sum(c["stage"] == "decompose" for c in transport.calls))
        self.assertEqual(3, sum(c["stage"] == "verify" for c in transport.calls))
        self.assertEqual(2, sum(c["stage"] == "select" for c in transport.calls))
        self.assertEqual({"deconstruct", "search_plan", "source_trace", "source_verify", "synthesis"},
                         {c["stage"] for c in transport.calls} -
                         {"harness_retrieval_decision", "decompose", "verify", "select", "shared_final_forecast"})
        self.assertFalse(claim["trace"]["config"]["reanalyze_existing_versions"])


if __name__ == "__main__":
    unittest.main()
