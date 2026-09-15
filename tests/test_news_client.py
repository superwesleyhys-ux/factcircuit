"""Synthetic news client/core boundary checks; no network or model calls."""
import asyncio
from copy import deepcopy
import json
from threading import Lock
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from newsverify.double_loop import CallBudgetTransport, ModelCallBudgetError
from newsverify.news_client import TracingClient
from newsverify.news_sources import SourceDocument
from newsverify.news_tracing.core import NewsTracingAgent
from newsverify.news_tracing import prompts
from newsverify.news_tracing.schemas import RESEARCH_SCHEMAS, validate_research_output
from newsverify.tunnels import TunnelError


KNOWN = "https://synthetic.invalid/article"
FOREIGN = "https://unfetched.invalid/invented-record"
EMPTY_REPLIES = {
    "deconstruct": {"core_event": "", "date": "unknown",
                    "entities": {"people": [], "organizations": [], "locations": []},
                    "key_claims": [], "causal_hints": []},
    "search_plan": {"queries": []}, "source_trace": {"sources": []},
    "source_verify": {"consistent_facts": [], "disputed_facts": [], "credibility_note": ""},
    "causal_dig": {"causes": []}, "grounding_check": {"checks": []},
    "timeline_build": {"events": []}, "perspective": {"perspectives": []},
    "synthesis": {"key_findings": [], "information_gaps": [], "causal_summary": "", "bias_notes": []},
}


def source_fixture(url=KNOWN):
    return {"url": url, "outlet": "Synthetic", "publish_time": "unknown", "source_type": "record",
            "is_original": True, "facts": [{"claim": "Synthetic claim", "date_mentioned": "unknown"}]}


def cause_fixture(refs=None):
    return {"title": "Cause", "date": "unknown", "summary": "Unverified proposal", "relation": "background",
            "sources": refs or [], "confidence": 0.5, "grounded": True, "is_root": True}


def collector_fixture():
    doc = SourceDocument(KNOWN, "Synthetic article", "A synthetic observation.",
                         "2026-09-08T00:00:00Z")
    return SimpleNamespace(documents={KNOWN: doc}, errors=[], requests=[],
                           search=Mock(return_value=[doc]))


class SyntheticTransport:
    """Deterministic stage replies recorded by the actual budget wrapper."""
    kind = "local"
    model = "synthetic-no-model"
    reasoning_effort = "medium"

    def __init__(self, responses=None, delay=0):
        self.responses = responses or {}
        self.calls = []
        self.schemas = []
        self.delay = delay
        self.active = self.peak_active = 0
        self._lock = Lock()

    def generate(self, stage, instructions, packet, schema):
        with self._lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            result = deepcopy(self.responses.get(stage, EMPTY_REPLIES.get(stage, {})))
            self.schemas.append(deepcopy(schema))
            self.calls.append({"stage": stage, "success": True,
                               "usage": {"input_tokens": 1, "output_tokens": 1}})
            if stage == "direct_response":
                return {"text": "Unverified synthetic research draft."}
            return result
        finally:
            with self._lock:
                self.active -= 1


class NewsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_search_requests_use_only_the_designated_query_or_title(self):
        variants = [
            (prompts.SOURCE_TRACE_PROMPT,
             {"query": "specific reported observation", "angle": "origin",
              "sources": [{"content": "Do not send this prior pool."}]},
             "specific reported observation"),
            (prompts.CAUSAL_DIG_PROMPT,
             {"node_id": "event_000", "title": "specific earlier event",
              "summary": "Do not search the whole summary.", "max_causes": 1},
             "specific earlier event"),
        ]
        for prompt, request, expected in variants:
            with self.subTest(query=expected):
                collector = collector_fixture()
                bounded = CallBudgetTransport(SyntheticTransport(), 1)
                client = TracingClient(bounded, collector)

                await client.query_json(prompt, json.dumps(request), web_search=True)

                collector.search.assert_called_once_with(expected, limit=3)
                self.assertEqual(bounded.model_io[0]["packet"]["fetched_evidence"][0]["url"], KNOWN)

    async def test_arbitrary_prior_pools_and_wrong_stage_fields_are_never_search_queries(self):
        variants = [
            (prompts.SOURCE_TRACE_PROMPT, [{"url": KNOWN, "content": "private prior pool"}]),
            (prompts.SOURCE_TRACE_PROMPT, {"sources": [{"url": KNOWN}], "title": "wrong field"}),
            (prompts.SOURCE_TRACE_PROMPT, {"query": ["wrong query type"]}),
            (prompts.CAUSAL_DIG_PROMPT, {"query": "wrong field", "summary": "prior analysis"}),
            (prompts.CAUSAL_DIG_PROMPT, {"title": {"content": "wrong title type"}}),
            (prompts.SOURCE_VERIFY_PROMPT, {"query": "not a search stage", "title": "not a search stage"}),
        ]
        for prompt, request in variants:
            with self.subTest(request=request):
                collector = collector_fixture()
                client = TracingClient(SyntheticTransport(), collector)
                await client.query_json(prompt, json.dumps(request), web_search=True)
                collector.search.assert_not_called()
        collector = collector_fixture()
        client = TracingClient(SyntheticTransport(), collector)
        await client.query_json(prompts.SOURCE_TRACE_PROMPT, "Malformed prior pool: {", web_search=True)
        collector.search.assert_not_called()

    async def test_exhausted_collection_budget_skips_search_without_losing_the_stage(self):
        for documents, searches in ((1, 3), (8, 0)):
            with self.subTest(documents=documents, searches=searches):
                collector = collector_fixture()
                collector.max_documents = documents
                collector.max_searches = searches
                collector.requests.append({"operation": "fetch", "success": False})
                transport = SyntheticTransport()
                client = TracingClient(transport, collector)

                await client.query_json(prompts.SOURCE_TRACE_PROMPT,
                                        json.dumps({"query": "synthetic query"}), web_search=True)

                collector.search.assert_not_called()
                self.assertEqual(collector.requests[-1],
                                 {"operation": "search_skipped", "reason": "collection_budget"})
                self.assertEqual(len(transport.calls), 1)

    async def test_unknown_source_proposals_are_diagnostic_and_raw_receipts_are_preserved(self):
        refs = [{"url": "https://SYNTHETIC.invalid:443/article#paragraph", "title": "Known"},
                {"url": FOREIGN, "title": "Invented"}]
        variants = [
            (prompts.SOURCE_TRACE_PROMPT, "source_trace",
             {"sources": [source_fixture(ref["url"]) for ref in refs]}),
            (prompts.CAUSAL_DIG_PROMPT, "causal_dig", {"causes": [cause_fixture(refs)]}),
        ]
        for prompt, stage, response in variants:
            with self.subTest(stage=stage):
                bounded = CallBudgetTransport(SyntheticTransport({stage: response}), 1)
                client = TracingClient(bounded, collector_fixture())

                result = await client.query_json(prompt, "{}")

                sources = result["sources"] if stage == "source_trace" else result["causes"][0]["sources"]
                self.assertEqual([source["url"] for source in sources], [KNOWN])
                self.assertEqual(client.errors, [])
                self.assertEqual(len(client.rejected_sources), 1)
                self.assertEqual(client.rejected_sources[0]["type"], "UnfetchedSourceProposal")
                self.assertEqual(client.rejected_sources[0]["count"], 1)
                self.assertTrue(bounded.calls[0]["success"])
                self.assertEqual(bounded.model_io[0]["response"], response)

    async def test_concurrent_research_stages_serialize_and_share_one_call_budget(self):
        transport = SyntheticTransport(delay=0.01)
        bounded = CallBudgetTransport(transport, 3)
        client = TracingClient(bounded, collector_fixture())

        results = await asyncio.gather(*(
            client.query_json(prompts.SOURCE_TRACE_PROMPT, json.dumps({"query": str(index)}))
            for index in range(6)), return_exceptions=True)

        self.assertEqual(sum(isinstance(result, dict) for result in results), 3)
        self.assertEqual(sum(isinstance(result, ModelCallBudgetError) for result in results), 3)
        self.assertEqual(transport.peak_active, 1)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual([call["call_number"] for call in bounded.calls], [1, 2, 3])
        self.assertEqual(len(bounded.model_io), 3)
        self.assertEqual(len(bounded.blocked_calls), 3)

    async def test_core_filters_structured_references_before_report_and_later_prompts(self):
        refs = [{"url": KNOWN, "title": "Known"}, {"url": FOREIGN, "title": "Invented"}]
        responses = {
            "deconstruct": {"core_event": "Synthetic event", "date": "unknown",
                            "entities": {"people": [], "organizations": [], "locations": []},
                            "key_claims": ["A synthetic observation."], "causal_hints": []},
            "search_plan": {"queries": [{"query": "Synthetic event", "angle": "origin"}]},
            "source_trace": {"sources": [{"url": KNOWN, "outlet": "Synthetic", "publish_time": "unknown",
                              "source_type": "record", "is_original": True,
                              "facts": [{"claim": "A synthetic observation.", "date_mentioned": "unknown"}]}]},
            "causal_dig": {"causes": [{"title": "Earlier synthetic event", "date": "unknown",
                            "summary": "Unverified causal proposal.", "relation": "background", "sources": refs,
                            "confidence": 0.5, "grounded": True, "is_root": True}]},
            "source_verify": {
                "consistent_facts": [{"claim": "A synthetic observation.", "source_urls": [KNOWN, FOREIGN]}],
                "disputed_facts": [{"claim": "Synthetic difference.", "source_urls": [FOREIGN], "versions": []}],
                "credibility_note": "Unverified proposals only."},
            "grounding_check": {"checks": [{"node_id": "event_001", "event_title": "Earlier synthetic event", "grounded": True,
                                  "matching_urls": [KNOWN, FOREIGN], "note": "Unverified proposal."}]},
            "timeline_build": {"events": [{"date": "unknown", "title": "Synthetic event",
                                "description": "Unverified event.", "significance": "重要",
                                "sources": refs, "causal_links": [], "source_count": 2}]},
            "synthesis": {"key_findings": [], "information_gaps": [], "causal_summary": "", "bias_notes": []},
        }
        bounded = CallBudgetTransport(SyntheticTransport(responses), 20)
        client = TracingClient(bounded, collector_fixture())
        agent = NewsTracingAgent(client, max_depth=1, max_queries=1, max_causes=1)

        report = await agent.run("Synthetic news input.")

        self.assertEqual([source.url for source in report.sources], [KNOWN])
        self.assertEqual(report.consistent_facts[0].source_urls, [KNOWN])
        self.assertEqual(report.disputed_facts[0].source_urls, [])
        child = report.event.causes[0]
        self.assertEqual(child.sources, [refs[0]])
        self.assertEqual(child.model_grounding_check["matching_urls"], [KNOWN])
        self.assertEqual(report.event_timeline[0].sources, [refs[0]])
        self.assertEqual(report.event_timeline[0].unique_source_url_count, 1)
        self.assertFalse(report.sources[0].is_original)
        self.assertFalse(child.grounded)
        self.assertFalse(report.consistent_facts[0].verified)
        self.assertFalse(report.event_timeline[0].verified)
        self.assertTrue(report.analysis_is_unverified)
        self.assertEqual({e["stage"] for e in report.errors if e["type"] == "InvalidReference"},
                         {"source_verify", "grounding_check", "timeline_build"})
        later = [record for record in bounded.model_io if record["stage"] in {"direct_response", "synthesis"}]
        self.assertEqual(len(later), 2)
        for record in later:
            request = json.loads(record["packet"]["request"])
            self.assertNotIn(FOREIGN, json.dumps(request))
            self.assertNotIn("arbitrary_metadata", request["agreement_proposals"])
        raw = next(record for record in bounded.model_io if record["stage"] == "source_verify")
        self.assertIn(FOREIGN, json.dumps(raw["response"]))

    async def test_all_research_stages_request_native_nested_schemas(self):
        for stage, response in EMPTY_REPLIES.items():
            with self.subTest(stage=stage):
                transport = SyntheticTransport()
                client = TracingClient(transport, collector_fixture())
                result = await client.query_json(getattr(prompts, stage.upper() + "_PROMPT"), "{}")
                self.assertEqual(result, response)
                schema = transport.schemas[0]
                self.assertEqual(schema, RESEARCH_SCHEMAS[stage])
                self.assertFalse(schema["additionalProperties"])
                self.assertNotIn("result_json", schema["properties"])
                validate_research_output(response, schema)
        facts = RESEARCH_SCHEMAS["source_trace"]["properties"]["sources"]["items"]["properties"]["facts"]
        self.assertEqual(facts["type"], "array")
        self.assertEqual(facts["items"]["required"], ["claim", "date_mentioned"])
        self.assertFalse(facts["items"]["additionalProperties"])

    async def test_json_string_missing_closers_cannot_hide_inside_outer_schema(self):
        # The former outer schema constrained only this string, allowing its
        # nested source array/object to end without the final ]} delimiters.
        truncated = json.dumps({"sources": [source_fixture()]})[:-2]
        response = {"result_json": truncated}
        json.loads(json.dumps(response))  # Valid outer JSON is insufficient.
        with self.assertRaises(json.JSONDecodeError):
            json.loads(truncated)
        transport = SyntheticTransport({"source_trace": response})
        bounded = CallBudgetTransport(transport, 3)
        client = TracingClient(bounded, collector_fixture())
        with self.assertRaisesRegex(TunnelError, "required object fields"):
            await client.query_json(prompts.SOURCE_TRACE_PROMPT, "{}")
        self.assertEqual(len(transport.calls), 1)  # No hidden repair/retry.
        self.assertEqual(bounded.model_io[0]["response"], response)
        self.assertEqual(client.rejected_sources, [])
        # A valid legacy string is rejected too, rather than silently falling back.
        transport.responses["source_trace"] = {"result_json": json.dumps({"sources": []})}
        with self.assertRaises(TunnelError):
            await client.query_json(prompts.SOURCE_TRACE_PROMPT, "{}")

    async def test_nested_schema_rejects_types_unknown_fields_and_bounds_without_repair(self):
        bad = []
        for field, value in (("is_original", 1), ("facts", "[]"), ("url", 123)):
            source = source_fixture()
            source[field] = value
            bad.append(("source_trace", {"sources": [source]}, "{}"))
        source = source_fixture()
        source["facts"][0]["verified"] = True
        bad.append(("source_trace", {"sources": [source]}, "{}"))
        bad.extend([
            ("source_trace", {"sources": ["malformed reference"]}, "{}"),
            ("source_trace", {"sources": [source_fixture()] * 6}, "{}"),
            ("source_trace", {"sources": [{**source_fixture(), "outlet": "x" * 501}]}, "{}"),
            ("search_plan", {"queries": [{"angle": "origin", "query": "one"}] * 2}, '{"max_queries":1}'),
            ("causal_dig", {"causes": [cause_fixture(), cause_fixture()]}, '{"max_causes":1}'),
        ])
        for confidence in (True, "0.5", -0.1, 1.1, float("nan"), float("inf"), 10 ** 500):
            bad.append(("causal_dig", {"causes": [{**cause_fixture(), "confidence": confidence}]}, "{}"))
        event = {"date": "unknown", "title": "event", "description": "", "significance": "重要",
                 "sources": [], "causal_links": [], "source_count": 0}
        for field, value in (("source_count", True), ("source_count", -1), ("source_count", 11),
                             ("significance", "invalid"), ("causal_links", [{"target": "e", "relation": "r"}])):
            bad.append(("timeline_build", {"events": [{**event, field: value}]}, "{}"))
        for stage, response, request in bad:
            with self.subTest(stage=stage, response=response):
                transport = SyntheticTransport({stage: response})
                client = TracingClient(transport, collector_fixture())
                with self.assertRaises(TunnelError):
                    await client.query_json(getattr(prompts, stage.upper() + "_PROMPT"), request)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(client.rejected_sources, [])
        self.assertEqual(RESEARCH_SCHEMAS["search_plan"]["properties"]["queries"]["maxItems"], 3)

    async def test_schema_failure_remains_a_failed_phase_and_does_not_establish_trust(self):
        replies = {**EMPTY_REPLIES,
            "deconstruct": {**EMPTY_REPLIES["deconstruct"], "core_event": "Synthetic event",
                            "key_claims": ["Synthetic claim"]},
            "search_plan": {"queries": [{"angle": "origin", "query": "Synthetic claim"}]},
            "source_trace": {"sources": [{**source_fixture(), "verified": True}]}}
        bounded = CallBudgetTransport(SyntheticTransport(replies), 20)
        client = TracingClient(bounded, collector_fixture())
        report = await NewsTracingAgent(client, max_depth=0, max_queries=1).run("Synthetic claim")
        self.assertEqual(report.sources, [])
        self.assertTrue(report.analysis_is_unverified)
        self.assertIn({"stage": "source_trace", "status": "failed"}, report.phase_status)
        self.assertTrue(any(error["stage"] == "source_trace" and error["type"] == "TunnelError"
                            for error in report.errors))
        raw = next(record for record in bounded.model_io if record["stage"] == "source_trace")
        self.assertEqual(raw["response"], replies["source_trace"])
        self.assertEqual(sum(call["stage"] == "source_trace" for call in bounded.calls), 1)

    async def test_unknown_prompt_fails_before_search_or_model_call(self):
        transport, collector = SyntheticTransport(), collector_fixture()
        client = TracingClient(transport, collector)
        with self.assertRaisesRegex(TunnelError, "No structured output contract"):
            await client.query_json("Unregistered prompt", '{"query":"do not search"}', web_search=True)
        self.assertEqual(transport.calls, [])
        collector.search.assert_not_called()

    def test_snapshot_evidence_preserves_all_versions_and_full_text(self):
        collector = collector_fixture()
        docs = [SimpleNamespace(url=KNOWN, title="Version", content=str(index) * 20000,
                                retrieved_at="2023-12-30T00:00:00Z", published_at=None,
                                version_id=f"version-{index}") for index in range(6)]
        docs.append(SimpleNamespace(url=KNOWN, title="Empty version", content="",
                                   retrieved_at="2023-12-30T00:00:00Z", published_at=None,
                                   version_id="empty-version"))
        collector.evidence_documents = docs
        collector.documents = {KNOWN: docs[-1]}
        evidence = TracingClient(SyntheticTransport(), collector)._evidence()
        self.assertEqual([row["version_id"] for row in evidence], [doc.version_id for doc in docs])
        self.assertEqual([len(row["content"]) for row in evidence], [20000] * 6 + [0])
        for doc, row in zip(docs, evidence):
            self.assertEqual(row["content"], doc.content[row["content_start"]:row["content_end"]])
            self.assertEqual(row["context_excerpt"], len(row["content"]) < len(doc.content))
        live = TracingClient(SyntheticTransport(), collector_fixture())._evidence()[0]
        self.assertNotIn("version_id", live)

    def test_evidence_capacity_requires_explicit_larger_opt_in(self):
        collector = collector_fixture()
        late = "late qualification: simulated, not measured."
        doc = SimpleNamespace(url=KNOWN, title="Large", content="x" * 240000 + late,
                              retrieved_at="2023-12-30T00:00:00Z", published_at=None,
                              version_id="large")
        collector.evidence_documents = [doc]
        collector.documents = {KNOWN: doc}
        with self.assertRaisesRegex(TunnelError, "capacity exceeded"):
            TracingClient(SyntheticTransport(), collector)._evidence()
        evidence = TracingClient(SyntheticTransport(), collector, max_evidence_chars=300000)._evidence()
        self.assertTrue(evidence[0]["content"].endswith(late))

    def test_evidence_capacity_rejects_malformed_limits(self):
        for value in (True, False, 0, -1, 1_000_001, "300000", 3.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TracingClient(SyntheticTransport(), collector_fixture(), max_evidence_chars=value)

    def test_short_snapshot_does_not_displace_other_versions_evidence(self):
        collector = collector_fixture()
        collector.evidence_documents = [SimpleNamespace(url=KNOWN, title="Version", content="x" * size,
            retrieved_at="2023-12-30T00:00:00Z", published_at=None, version_id=str(index))
            for index, size in enumerate([100, 20000, 20000, 20000, 20000])]
        evidence = TracingClient(SyntheticTransport(), collector)._evidence()
        self.assertEqual([len(row["content"]) for row in evidence], [100, 20000, 20000, 20000, 20000])

    async def test_late_qualification_and_historical_availability_reach_research(self):
        from newsverify.news_tracing_runner import _SnapshotCollector
        from newsverify.provenance import _time
        qualification = "The stated total includes simulated observations, not measured observations."
        source = {"version_id": "historical-full", "url": KNOWN,
            "content": "A report of measurements.\n" + "Routine background.\n" * 1400 + qualification,
            "retrieved_at": "2026-09-08T00:00:00Z", "published_at": "2020-01-01T00:00:00Z",
            "available_at": "2020-01-02T00:00:00Z", "availability_basis": "Synthetic archived copy captured in 2020.",
            "issuer": "Synthetic record"}
        collector = _SnapshotCollector([source], _time("2023-12-31T23:59:59Z", "cutoff"))
        bounded = CallBudgetTransport(SyntheticTransport(), 1)

        await TracingClient(bounded, collector).query_json(prompts.SYNTHESIS_PROMPT, "{}")

        packet = bounded.model_io[0]["packet"]
        row = packet["fetched_evidence"][0]
        self.assertGreater(source["content"].index(qualification), 16000)
        self.assertTrue(qualification in row["content"], "Research lost the late qualification")
        self.assertEqual(row["content"], source["content"])
        self.assertEqual(row["available_at"], source["available_at"])
        self.assertEqual(row["availability_basis"], source["availability_basis"])
        self.assertEqual(row["content_length"], len(source["content"]))
        self.assertFalse(row["context_excerpt"])
        self.assertIn("text-only", packet["evidence_scope"])

    async def test_over_capacity_evidence_fails_before_inference_instead_of_truncating(self):
        collector = collector_fixture()
        collector.evidence_documents = [SimpleNamespace(url=KNOWN, title="Large record", content="x" * 240001,
            retrieved_at="2023-12-30T00:00:00Z", published_at=None, version_id="large")]
        transport = SyntheticTransport()
        with self.assertRaisesRegex(TunnelError, "evidence capacity"):
            await TracingClient(transport, collector).query_json(prompts.SYNTHESIS_PROMPT, "{}")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
