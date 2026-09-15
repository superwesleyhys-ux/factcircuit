"""Literal tracing behavior with scripted models and injected source I/O only."""

from copy import deepcopy
import hashlib
import json
import unittest

from newsverify.news_sources import SourceDocument
from newsverify.phrase_sources import LiteralSourceCollector, decode_public_document
from newsverify.phrase_tracing import run_phrase_trace


SEED = "https://news.example.org/report"
RECORD = "https://records.example.org/original"
OTHER = "https://other.example.org/copy"
CAPTURE = "2023-03-01T00:00:00Z"
CUTOFF = "2023-12-31T23:59:59Z"


def document(url=SEED, text="Company reports 100.6 million; unaudited.", *, title="Original report",
             links=(), captured=CAPTURE):
    return SourceDocument(url=url, title=title, content=text, retrieved_at=captured,
                          links=[{"url": target, "text": label} for target, label in links])


def response(*, action="finish", url="", query="", scope="empirical_claim", verdict="unresolved",
             origin_chain=(), citations=(), reason="No independent accounting record.", missing=()):
    return dict(action=action, url=url, query=query, scope=scope, verdict=verdict,
                origin_chain=list(origin_chain), citations=list(citations), reason=reason,
                missing_evidence=list(missing))


def cite(packet, quote, *, url=SEED, occurrence=1, role="context"):
    doc = next(row for row in packet["documents"] if row["url"] == url)
    return dict(source_id=doc["source_id"], quote=quote, occurrence=occurrence, role=role)


class ScriptedTransport:
    kind = "local"
    model = "offline-fixture"
    reasoning_effort = "low"

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.inputs = []

    def generate(self, stage, instructions, packet, schema):
        self.inputs.append(deepcopy(dict(stage=stage, instructions=instructions, packet=packet, schema=schema)))
        call = dict(stage=stage, success=False, usage={"input_tokens": 1, "output_tokens": 1})
        self.calls.append(call)
        if not self.steps:
            raise AssertionError("Unexpected extra model call")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        result = step(packet) if callable(step) else step
        call["success"] = True
        return deepcopy(result)


class FixtureCollector:
    def __init__(self, docs, search_results=None, **limits):
        self.catalog = docs
        self.search_results = search_results or {}
        self.documents, self.requests, self.errors = {}, [], []
        self._attempted, self._searched = set(), set()
        self.limits = limits

    def fetch(self, url):
        if url in self.documents:
            return self.documents[url]
        if url in self._attempted:
            return None
        self._attempted.add(url)
        doc = self.catalog.get(url)
        self.requests.append(dict(operation="fetch", url=url, success=doc is not None))
        if doc is None:
            self.errors.append(dict(operation="fetch", code="not_available", url=url))
        else:
            self.documents[doc.url] = doc
        return doc

    def search(self, query, limit=2):
        self.requests.append(dict(operation="search", query=query, limit=limit))
        self._searched.add(query)
        return [doc for url in self.search_results.get(query, [])[:limit]
                if (doc := self.fetch(url)) is not None]


class CollectorFactory:
    def __init__(self, docs, search_results=None):
        self.docs = {doc.url: doc for doc in docs}
        self.search_results = search_results
        self.instances = []

    def __call__(self, **limits):
        collector = FixtureCollector(self.docs, self.search_results, **limits)
        self.instances.append(collector)
        return collector


class PhraseTracingTests(unittest.TestCase):
    def run_case(self, steps, *, docs=None, selectors=None, search_results=None, arm="harness", **extra):
        factory = CollectorFactory(docs or [document()], search_results)
        transport = ScriptedTransport(steps)
        payload = dict(url=SEED, selectors=selectors or ["100.6"], **extra)
        result = run_phrase_trace(payload, arm=arm, transport=transport, collector_factory=factory)
        return result, transport, factory

    def test_each_item_has_independent_calls_full_original_and_no_previous_model_reason(self):
        original = "公司未确认。\nRevenue:  100.6 million; margin:  20 percent.\n  unaudited\r\n"
        first = response(reason="PRIVATE_FIRST_REASON_DO_NOT_PROPAGATE")
        result, transport, factory = self.run_case([first, response()],
                                                  docs=[document(text=original)], selectors=["100.6", "20"])
        self.assertEqual("completed", result["status"])
        self.assertEqual(2, result["model_calls"])
        self.assertEqual(3, len(factory.instances))
        for call in transport.inputs:
            self.assertEqual(original, call["packet"]["documents"][0]["content"])
            self.assertEqual([], call["packet"]["retrieval_history"])
        self.assertNotIn("PRIVATE_FIRST_REASON", json.dumps(transport.inputs[1], ensure_ascii=False))
        self.assertEqual(["100.6", "20"], [row["item"]["text"] for row in result["items"]])

    def test_retrieved_document_is_passed_unaltered_without_model_summary_or_prior_reason(self):
        original = "Record states 100.6\nNOT final;  unit: USD million.\r\n"
        steps = [response(action="open", url=RECORD, reason="DO_NOT_LAUNDER_THIS_SUMMARY"), response()]
        result, transport, _ = self.run_case(steps, docs=[document(links=[(RECORD, "accounting record")]),
                                                         document(RECORD, original)])
        self.assertEqual("completed", result["status"])
        next_input = transport.inputs[1]["packet"]
        self.assertEqual(original, next(row["content"] for row in next_input["documents"] if row["url"] == RECORD))
        self.assertNotIn("DO_NOT_LAUNDER_THIS_SUMMARY", json.dumps(next_input))
        self.assertEqual("open", next_input["retrieval_history"][0]["action"])
        self.assertEqual(1, len(next_input["retrieval_history"][0]["accepted"]))

    def test_search_performs_discovery_and_passes_fetched_source_on_next_call(self):
        query = '"100.6" original accounting records'
        steps = [response(action="search", query=query), response()]
        result, transport, factory = self.run_case(steps, docs=[document(), document(RECORD, "Audited income: 90.2.")],
                                                  search_results={query: [RECORD]})
        self.assertEqual("completed", result["status"])
        self.assertEqual({SEED, RECORD}, {row["url"] for row in transport.inputs[1]["packet"]["documents"]})
        self.assertEqual([dict(operation="search", query=query, limit=2)],
                         [row for row in factory.instances[1].requests if row["operation"] == "search"])

    def test_cutoff_rejects_future_content_and_title_before_subsequent_model_call(self):
        future = document(RECORD, "FUTURE_BODY_FABRICATION", title="FUTURE_TITLE_RESULT",
                          captured="2025-01-01T00:00:00Z")
        steps = [response(action="search", query="original accounting record"), response()]
        result, transport, _ = self.run_case(steps, docs=[document(), future],
                                             search_results={"original accounting record": [RECORD]}, as_of=CUTOFF)
        self.assertEqual("completed", result["status"])
        serialized = json.dumps(transport.inputs)
        self.assertNotIn("FUTURE_BODY", serialized)
        self.assertNotIn("FUTURE_TITLE", serialized)
        self.assertEqual(["after_cutoff"], result["items"][0]["actions"][0]["errors"])
        self.assertEqual([SEED], [row["url"] for row in transport.inputs[1]["packet"]["documents"]])

    def test_future_seed_never_reaches_any_model_call(self):
        factory = CollectorFactory([document(captured="2025-01-01T00:00:00Z")])
        transport = ScriptedTransport([])
        with self.assertRaisesRegex(ValueError, "after_cutoff"):
            run_phrase_trace(dict(url=SEED, selectors=["100.6"], as_of=CUTOFF),
                             transport=transport, collector_factory=factory)
        self.assertEqual([], transport.inputs)

    def test_malformed_source_timestamp_is_rejected_without_echoing_metadata_to_model(self):
        invalid = document(RECORD, "Rejected body", captured="FUTURE_SECRET_PAYLOAD")
        steps = [response(action="search", query="records"), response()]
        result, transport, _ = self.run_case(steps, docs=[document(), invalid],
                                             search_results={"records": [RECORD]}, as_of=CUTOFF)
        self.assertEqual("completed", result["status"])
        self.assertNotIn("FUTURE_SECRET_PAYLOAD", json.dumps(transport.inputs))
        self.assertEqual([SEED], [row["url"] for row in transport.inputs[1]["packet"]["documents"]])
        self.assertTrue(result["items"][0]["actions"][0]["errors"])

    def test_citation_uses_requested_exact_occurrence_with_program_owned_offsets(self):
        content = "100.6 was preliminary; 100.6 was repeated, still unaudited."
        final = lambda packet: response(scope="source_statement", verdict="supported",
                                         citations=[cite(packet, "100.6", occurrence=2, role="source_statement")])
        result, _, _ = self.run_case([final], docs=[document(text=content)],
                                     selectors=[{"start": 0, "end": 5}])
        item = result["items"][0]
        self.assertEqual("completed", item["status"])
        citation, = item["citations"]
        self.assertEqual(content.index("100.6", 5), citation["start"])
        self.assertEqual("100.6", content[citation["start"]:citation["end"]])
        self.assertEqual("exact_substrings_verified", item["citation_integrity"])

    def test_forged_quotes_and_occurrences_are_failed_visible_items(self):
        variants = [("100,6", 1), ("100.6", 0), ("100.6", 2), ("100.6", True)]
        for quote, occurrence in variants:
            with self.subTest(quote=quote, occurrence=occurrence):
                result, transport, _ = self.run_case([
                    lambda packet: response(scope="source_statement", verdict="supported",
                                            citations=[cite(packet, quote, occurrence=occurrence)])])
                self.assertEqual("partial", result["status"])
                self.assertEqual("failed", result["items"][0]["status"])
                self.assertEqual([], result["items"][0]["citations"])
                self.assertEqual(1, len(transport.inputs))

    def test_unread_citation_source_is_not_evidence(self):
        final = response(verdict="supported", scope="source_statement", citations=[
            dict(source_id="invented", quote="100.6", occurrence=1, role="source_statement")])
        result, _, _ = self.run_case([final])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["citation_source_not_read"], result["items"][0]["errors"])

    def test_open_requires_observed_link_and_does_not_fetch_invented_url(self):
        result, _, factory = self.run_case([response(action="open", url=RECORD)],
                                           docs=[document(), document(RECORD)])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["open_requires_observed_link"], result["items"][0]["errors"])
        self.assertEqual([], factory.instances[1].requests)

    def test_origin_chain_requires_read_documents_and_actual_hyperlink_edges(self):
        result, _, _ = self.run_case([response(origin_chain=[SEED, RECORD])],
                                     docs=[document(links=[(RECORD, "linked but unread")]), document(RECORD)])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["unread_origin"], result["items"][0]["errors"])
        steps = [response(action="search", query="find source"), response(origin_chain=[SEED, RECORD])]
        result, _, _ = self.run_case(steps, docs=[document(), document(RECORD)],
                                     search_results={"find source": [RECORD]})
        self.assertEqual("partial", result["status"])
        self.assertEqual(["unobserved_origin_edge"], result["items"][0]["errors"])

    def test_valid_origin_chain_remains_candidate_chain_not_truth_certificate(self):
        steps = [response(action="open", url=RECORD), response(origin_chain=[SEED, RECORD])]
        result, _, _ = self.run_case(steps, docs=[document(links=[(RECORD, "record")]), document(RECORD)])
        item = result["items"][0]
        self.assertEqual("candidate_chain_only", item["origin_status"])
        self.assertEqual("unresolved", item["judgment"]["verdict"])

    def test_origin_redirect_accepts_observed_fetched_alias_or_final_url_only(self):
        old_url = "https://records.example.org/old-release"
        final_url = "https://records.example.org/new-release/"
        unread_alias = "https://records.example.org/other-old-release"
        seed_body = ('<p>Company reports 100.6.</p><a href="' + old_url + '">original</a>'
                     '<a href="' + unread_alias + '">unread alternative</a>').encode()
        def fetch(url):
            if url == SEED:
                return SEED, {"content-type": "text/html"}, seed_body
            if url == old_url:
                return final_url, {"content-type": "text/plain"}, b"Original 100.6, unaudited."
            raise AssertionError("No request authorized for this alias")
        for chain_end, expected in ((old_url, "completed"), (final_url, "completed"), (unread_alias, "partial")):
            with self.subTest(chain_end=chain_end):
                transport = ScriptedTransport([response(action="open", url=old_url),
                                                response(origin_chain=[SEED, chain_end])])
                result = run_phrase_trace(dict(url=SEED, selectors=["100.6"]), transport=transport,
                                          collector_factory=lambda **limits: LiteralSourceCollector(fetch=fetch, **limits))
                self.assertEqual(expected, result["status"])
                row = next(doc for doc in result["items"][0]["documents"] if doc["url"] == final_url)
                self.assertEqual([old_url], row["requested_urls"])
                if expected == "completed":
                    self.assertEqual([SEED, chain_end], result["items"][0]["judgment"]["origin_chain"])
                else:
                    self.assertIn("unread_origin", result["items"][0]["errors"])

    def test_self_report_cannot_be_promoted_to_supported_empirical_claim(self):
        final = lambda packet: response(verdict="supported", citations=[
            cite(packet, "100.6", role="independent_record")])
        result, _, _ = self.run_case([final])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["self_report_is_not_independent_authentication"], result["items"][0]["errors"])

    def test_identical_copy_on_other_domain_is_not_independent_authentication(self):
        content = "Company reports 100.6 million; unaudited."
        final = lambda packet: response(verdict="supported", citations=[
            cite(packet, "100.6", url=OTHER, role="independent_record")])
        steps = [response(action="open", url=OTHER), final]
        result, _, _ = self.run_case(steps, docs=[document(text=content, links=[(OTHER, "copy")]),
                                                 document(OTHER, content, title="Different publisher, same text")])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["self_report_is_not_independent_authentication"], result["items"][0]["errors"])

    def test_model_failure_has_no_retry_and_other_items_are_still_retained(self):
        result, transport, _ = self.run_case([RuntimeError("model unavailable"), response()],
                                             docs=[document(text="100.6 million; 20 percent.")],
                                             selectors=["100.6", "20"])
        self.assertEqual("partial", result["status"])
        self.assertEqual(2, result["model_calls"])
        self.assertEqual(2, len(transport.inputs))
        self.assertEqual(["failed", "completed"], [row["status"] for row in result["items"]])
        self.assertEqual("100.6", result["items"][0]["item"]["text"])
        self.assertEqual(["model unavailable"], result["items"][0]["errors"])

    def test_evidence_occurrence_budget_failure_is_retained_instead_of_aborting_run(self):
        repeated = document(RECORD, "100.6 " * 101)
        steps = [response(action="open", url=RECORD)]
        result, transport, _ = self.run_case(steps, docs=[document(links=[(RECORD, "record")]), repeated])
        self.assertEqual("partial", result["status"])
        self.assertEqual("failed", result["items"][0]["status"])
        self.assertIn("100 item limit", " ".join(result["items"][0]["errors"]))
        self.assertEqual(1, len(transport.inputs))

    def test_direct_and_harness_receive_same_raw_evidence_tools_and_budgets(self):
        direct, direct_model, direct_factory = self.run_case([response()], arm="direct", as_of=CUTOFF)
        harness, harness_model, harness_factory = self.run_case([response()], arm="harness", as_of=CUTOFF)
        self.assertEqual(direct_model.inputs[0]["packet"], harness_model.inputs[0]["packet"])
        self.assertEqual(direct_model.inputs[0]["schema"], harness_model.inputs[0]["schema"])
        self.assertEqual(direct_factory.instances[1].limits, harness_factory.instances[1].limits)
        self.assertNotEqual(direct["policy_sha256"], harness["policy_sha256"])
        self.assertEqual(direct["input_source"], harness["input_source"])

    def test_payload_rejects_summaries_or_gold_labels_as_source_substitutes(self):
        factory = CollectorFactory([document()])
        transport = ScriptedTransport([])
        for field in ("summary", "gold_label", "research_advice", "content"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "no summaries or labels"):
                run_phrase_trace(dict(url=SEED, selectors=["100.6"], **{field: "SUPPORTED"}),
                                 transport=transport, collector_factory=factory)
        self.assertEqual([], transport.inputs)
        self.assertEqual([], factory.instances)

    def test_retrieval_in_last_slot_is_failure_not_success_or_implicit_retry(self):
        result, transport, _ = self.run_case([response(action="search", query="records")], limits={"max_calls": 1})
        self.assertEqual("partial", result["status"])
        self.assertEqual(["budget_exhausted_without_final"], result["items"][0]["errors"])
        self.assertEqual(1, len(transport.inputs))


class LiteralPublicSourceTests(unittest.TestCase):
    def test_parser_preserves_spacing_newlines_chinese_entities_and_empty_table_cells(self):
        body = ("<html><head><title>测试 &amp; Report</title><script>HEAD_SECRET</script></head>"
                "<body><p>公司  尚未确认\n收入&nbsp;100.6 &amp; 20。</p>"
                "<table><tr><th>Year</th><th>USD millions</th><th></th></tr>"
                "<tr><td>2021</td><td> 100.6 </td><td></td></tr></table>"
                "<p>cafe\u0301 🧪 <a href='/record'>原始  记录</a></p><script>BODY_SECRET</script></body></html>").encode("utf-8")
        doc = decode_public_document(SEED, {"Content-Type": "text/html; charset=utf-8"}, body, retrieved_at=CAPTURE)
        self.assertEqual("测试 & Report", doc.title)
        self.assertIn("公司  尚未确认\n收入\u00a0100.6 & 20。", doc.content)
        self.assertIn("\tYear\tUSD millions\t\n", doc.content)
        self.assertIn("\t2021\t 100.6 \t\n", doc.content)
        self.assertIn("cafe\u0301 🧪", doc.content)
        self.assertNotIn("SECRET", doc.content)
        self.assertEqual([{"url": "https://news.example.org/record", "text": "原始  记录"}], doc.links)
        self.assertEqual(hashlib.sha256(body).hexdigest(), doc.raw_body_sha256)

    def test_plain_text_retains_all_whitespace_and_does_not_decode_html_entities(self):
        original = "  公司\t 100.6\r\n &amp;  \n"
        doc = decode_public_document(SEED, {"content-type": "text/plain; charset=utf-8"},
                                     original.encode(), retrieved_at=CAPTURE)
        self.assertEqual(original, doc.content)
        self.assertEqual(CAPTURE, doc.available_at)

    def test_access_restriction_and_http_200_challenge_bytes_are_excluded(self):
        variants = [
            (b'<html><head><title>Just a moment...</title></head><body>CHALLENGE_SECRET</body></html>', "access_challenge"),
            (b'<html><body>Subscribe to read MEMBER_SECRET</body></html>', "access_restricted_or_challenge"),
            (b'<script type="application/ld+json">{"isAccessibleForFree":false}</script><p>TEASER_SECRET</p>', "access_restricted"),
        ]
        for body, code in variants:
            with self.subTest(code=code):
                def fetch(url):
                    return url, {"content-type": "text/html"}, body
                collector = LiteralSourceCollector(fetch=fetch)
                self.assertIsNone(collector.fetch(RECORD))
                self.assertEqual({}, collector.documents)
                self.assertEqual(code, collector.errors[0]["code"])
                self.assertFalse(collector.requests[0]["success"])
                self.assertNotIn("SECRET", json.dumps([collector.requests, collector.errors]))
                # The failed request remains attempted; no automatic retry.
                self.assertIsNone(collector.fetch(RECORD))
                self.assertEqual(1, len(collector.requests))

    def test_paywall_teaser_never_reaches_next_model_packet(self):
        seed_body = b'<p>Company reports 100.6; <a href="https://records.example.org/original">records</a>.</p>'
        bodies = {SEED: seed_body, RECORD: b'<p>Subscribe to read SECRET_TEASER_LAUNDERING</p>'}
        collectors = []
        def factory(**config):
            collector = LiteralSourceCollector(fetch=lambda url: (url, {"content-type": "text/html"}, bodies[url]), **config)
            collectors.append(collector)
            return collector
        transport = ScriptedTransport([response(action="open", url=RECORD), response()])
        result = run_phrase_trace(dict(url=SEED, selectors=["100.6"]), transport=transport, collector_factory=factory)
        self.assertEqual("completed", result["status"])
        self.assertEqual([SEED], [row["url"] for row in transport.inputs[1]["packet"]["documents"]])
        self.assertNotIn("SECRET_TEASER", json.dumps(transport.inputs))
        self.assertIn("access_restricted_or_challenge", result["items"][0]["actions"][0]["errors"])
        self.assertFalse(collectors[1].requests[0]["success"])

    def test_real_collector_search_uses_urls_and_fetched_text_not_candidate_snippets(self):
        fetched = []
        def fetch(url):
            fetched.append(url)
            return url, {"content-type": "text/plain"}, b"Actual raw record 100.6; unaudited."
        collector = LiteralSourceCollector(fetch=fetch, search=lambda query, limit: [
            {"url": RECORD, "title": "FABRICATED_SEARCH_TITLE", "snippet": "FABRICATED_SEARCH_SNIPPET"}])
        docs = collector.search("100.6 accounting", limit=2)
        self.assertEqual([RECORD], fetched)
        self.assertEqual("Actual raw record 100.6; unaudited.", docs[0].content)
        self.assertNotIn("FABRICATED_SEARCH", json.dumps([doc.__dict__ for doc in docs]))
        self.assertEqual(1, len(collector.documents))


if __name__ == "__main__":
    unittest.main()
