"""Synthetic end-to-end news integration checks; no models or network.

The real synthesis adapter, source boundary, and double-loop orchestration run.
Only the transport and collector are replaced with deterministic fixtures. These
tests establish integration contracts, not model accuracy or genuine news truth.
"""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from newsverify.tunnels import TunnelError


CUTOFF = "2023-12-31T23:59:59Z"
CLAIM = "The synthetic laboratory measured 30 units."
FAILED_CLAIM = "The synthetic second measurement was 90 units."


def material(version, text, available="2023-01-01T00:00:00Z"):
    return {
        "version_id": version, "url": "https://synthetic.invalid/" + version,
        "content": text, "issuer": "Synthetic fixture publisher",
        "retrieved_at": "2026-01-01T00:00:00Z", "published_at": available,
        "available_at": available,
        "availability_basis": "Explicit synthetic historical-version fixture.",
    }


COPY = material("copy", "Copied dispatch: the laboratory measured 30 units. "
                "Reprinted from https://synthetic.invalid/wire")
WIRE = material("wire", "Wire dispatch: the laboratory measured 30 units. "
                "Source laboratory record: https://synthetic.invalid/record")
RECORD = material("record", "Original synthetic laboratory measurement record: 30 units.")
RELEASE = material("release", "Original synthetic laboratory press release: we measured 30 units. "
                   "Raw instrument record: https://synthetic.invalid/raw-log")
RAW_LOG = material("raw-log", "Synthetic raw instrument measurement record: 20 units, not 30 units.")
UNRELATED = material("unrelated", "Original synthetic parks-office record: the new park has 12 trees.",
                     "2020-01-01T00:00:00Z")
UNLINKED_COPY = material("unlinked-copy", "The synthetic laboratory measured 30 units. "
                         "Producing record unavailable at https://synthetic.invalid/missing")
FUTURE = material("future", "FUTURE_VERSION_MUST_NOT_REACH_ANY_MODEL_PACKET", "2025-01-01T00:00:00Z")
UNKNOWN = material("unknown", "UNKNOWN_VERSION_MUST_NOT_REACH_ANY_MODEL_PACKET")
UNKNOWN.update(available_at=None, availability_basis=None)


def basis(source):
    return {"version_id": source["version_id"], "quote": source["content"]}


def source_name(source):
    return urlsplit(source["url"]).path.rsplit("/", 1)[-1]


def retained_basis(context, source):
    """Use only exact evidence actually retained in the incremental packet."""
    if "content" in source:
        return basis(source)
    fragments = context["analyses"][source["version_id"]]["fragments"]
    span = next(item["span"] for item in fragments
                if item["span"]["version_id"] == source["version_id"])
    return {"version_id": span["version_id"], "quote": span["quote"]}


class OfflineCollector:
    """Instantiation is harmless; any fetch/search would violate offline mode."""
    def __init__(self, **kwargs):
        self.documents, self.errors, self.requests = {}, [], []

    def fetch(self, url):
        self.requests.append({"operation": "forbidden_fetch", "url": url})
        raise AssertionError("Offline/historical fixtures must not fetch live URLs")

    def search(self, query, limit=3):
        self.requests.append({"operation": "forbidden_search", "query": query})
        raise AssertionError("Offline/historical fixtures must not search the web")


class NewsFixtureTransport:
    kind = "local"
    model = "synthetic-no-model"
    reasoning_effort = "medium"

    def __init__(self, mode="chain"):
        self.mode, self.calls, self.inputs = mode, [], []

    def generate(self, stage, instructions, packet, schema):
        self.inputs.append({"stage": stage, "instructions": instructions,
                            "packet": deepcopy(packet), "schema": deepcopy(schema)})
        call = {"stage": stage, "model": self.model, "tunnel": self.kind,
                "reasoning_effort": self.reasoning_effort, "success": True,
                "status": "completed", "wall_seconds": 0.001,
                "usage": {"input_tokens": 10, "output_tokens": 2}}
        self.calls.append(call)
        if stage == "verify" and packet["target"]["text"] == FAILED_CLAIM:
            call.update(success=False, status="failed", usage=None)
            raise TunnelError("Synthetic verification transport failure.")
        if stage == "decompose":
            return self.decompose(packet)
        if stage == "verify":
            return self.verify(packet)
        if stage == "select":
            ids = {source_name(x): x["version_id"] for x in packet["catalog"]}
            chosen = next((ids[x] for x in ("wire", "record", "raw-log", "unrelated") if x in ids), next(iter(ids.values())))
            return {"version_id": chosen, "rationale": "Inspect this registered synthetic candidate."}
        if stage == "origin_links":
            return {"urls": [], "rationale": "No additional live retrieval in this offline fixture."}
        if "result_json" in schema.get("properties", {}):
            return {"result_json": json.dumps(self.synthesis(stage), ensure_ascii=False)}
        if "text" in schema.get("properties", {}):
            return {"text": "Synthetic unverified explanatory draft."}
        return self.synthesis(stage)

    def synthesis(self, stage):
        """The old model-only flags deliberately suggest more than they prove."""
        objects = {
            "deconstruct": {"core_event": CLAIM, "date": "2023-01-01",
                            "entities": {"people": [], "organizations": [], "locations": []},
                            "key_claims": [CLAIM], "causal_hints": []},
            "search_plan": {"queries": [{"angle": "origin", "query": "synthetic source record"}]},
            "source_trace": {"sources": [{"outlet": "Synthetic parks office",
                "url": UNRELATED["url"], "publish_time": "2020-01-01",
                "source_type": "original_record", "is_original": True,
                "facts": [{"claim": CLAIM, "date_mentioned": "2023-01-01"}]}]},
            "source_verify": {"consistent_facts": [], "disputed_facts": [], "credibility_note": "Synthetic source comparison."},
            "causal_dig": {"causes": []}, "grounding_check": {"checks": []},
            "timeline_build": {"events": []}, "perspective": {"perspectives": []},
            "synthesis": {"key_findings": [], "information_gaps": ["Synthetic evidence only."],
                          "causal_summary": "", "bias_notes": []},
        }
        name = stage.removesuffix("_prompt")
        if name not in objects:
            raise AssertionError("Unexpected synthesis phase: " + stage)
        return deepcopy(objects[name])

    def decompose(self, packet):
        source, target = packet["material"], packet["target"]
        context = packet["context"]
        available = {x["version_id"]: x for x in
                     context["materials"] + context.get("prior_materials", [])}
        available[source["version_id"]] = source
        version = source_name(source)
        versions = {source_name(item): key for key, item in available.items()}
        result = {"fragments": [{"id": "claim", "text": source["content"],
                    "quote": source["content"], "qualifiers": []}],
                  "relations": [], "gaps": [], "resolutions": [], "origins": [],
                  "revisit_versions": [], "notes": "Synthetic evidence annotation."}
        upstream = {"copy": "wire", "wire": "record", "release": "raw-log"}.get(version)
        if upstream:
            direct = upstream in versions
            result["relations"].append({"id": "upstream", "from_version": source["version_id"],
                "to_version": versions[upstream] if direct else None, "kind": "cites",
                "status": "direct" if direct else "declared", "basis": [basis(source)],
                "rationale": "The quoted source explicitly identifies this upstream URL.",
                "upstream_locator": "https://synthetic.invalid/" + upstream})
        # The new source binds an earlier declared citation using its retained
        # exact evidence. No earlier analysis is repeated or promoted in place.
        for previous in context["analyses"].values():
            for relation in previous["relations"]:
                if relation["status"] == "declared" and relation["upstream_locator"] == source["url"]:
                    result["relations"].append({
                        "id": "obtained-" + relation["from_version"],
                        "from_version": relation["from_version"], "to_version": source["version_id"],
                        "kind": "cites", "status": "direct",
                        "basis": [{"version_id": span["version_id"], "quote": span["quote"]}
                                  for span in relation["basis"]],
                        "rationale": "The earlier exact citation identifies this newly obtained source.",
                        "upstream_locator": source["url"],
                    })
        if version in {"record", "release", "unrelated"}:
            result["origins"] = [{"version_id": source["version_id"], "basis": [basis(source)],
                "material_kind": "original_record", "rationale": "Synthetic producing-record annotation."}]
            result["resolutions"] = [{"gap_id": "origin:" + target["id"],
                "basis": [basis(source)], "rationale": "Synthetic root finding; a source path is still required."}]
        return result

    def verify(self, packet):
        context, target = packet["context"], packet["target"]
        available = {source_name(x): x for x in
                     context["materials"] + context.get("prior_materials", [])}
        settled = "raw-log" if self.mode == "false_primary" else "record"
        gap_id = "verification:" + target["id"] + ":measurement"
        result = {"verdict": "unresolved", "basis": [],
                  "rationale": "The source assertions alone do not establish this synthetic measurement.",
                  "gaps": [{"id": gap_id, "question": "Obtain the synthetic measurement record.",
                            "stage": "verification"}], "resolutions": []}
        if settled in available:
            evidence = retained_basis(context, available[settled])
            result.update(verdict="contradicted" if self.mode == "false_primary" else "supported",
                          basis=[evidence], gaps=[],
                          rationale="The cited synthetic measurement record settles the stated value.")
            if any(g["id"] == gap_id for g in context["gaps"]):
                result["resolutions"] = [{"gap_id": gap_id, "basis": [evidence],
                                          "rationale": "The quoted record answers the measurement question."}]
        return result


class NewsTracingIntegrationTests(unittest.TestCase):
    def run_fixture(self, materials, mode="chain", claims=None, extra_news=(), transport=None,
                    research_mode=None, news_text=None):
        from newsverify.news_tracing_runner import run_news_tracing
        news = {"id": "n1", "text": news_text if news_text is not None else CLAIM, "url": materials[0]["url"],
                "claims": claims or [CLAIM], "materials": deepcopy(materials),
                "source_version_id": materials[0]["version_id"], "as_of": CUTOFF}
        payload = {"news": [news, *deepcopy(extra_news)], "config": {
            "max_claims": 3, "depth": 0, "max_queries": 1, "max_documents": 8,
            "max_searches": 1, "max_origin_depth": 3, "max_origin_calls": 10}}
        if research_mode is not None:
            payload["config"]["research_mode"] = research_mode
        transport = transport or NewsFixtureTransport(mode)
        collectors = []

        def factory(**kwargs):
            value = OfflineCollector(**kwargs)
            collectors.append(value)
            return value

        report = run_news_tracing(payload, transport=transport, collector_factory=factory,
                                  max_model_calls=40)
        self.assertFalse(any(c.requests for c in collectors), "Offline input triggered live source retrieval")
        self.assertLessEqual(len(transport.calls), 40)
        return report, transport

    def test_copy_chain_requires_incremental_exact_edges_to_the_actual_terminal_origin(self):
        report, transport = self.run_fixture([COPY, WIRE, RECORD])
        result = report["results"][0]
        self.assertEqual("completed", result["status"])
        self.assertEqual([], result["errors"])
        claim = result["claims"][0]
        self.assertEqual(("supported", "original_material_located"),
                         (claim["fact_status"], claim["provenance_status"]))
        trace = claim["trace"]
        direct_edges = {(e["from_version"], e["to_version"]) for e in trace["relations"]
                        if e["status"] == "direct" and e["kind"] == "cites"}
        self.assertEqual({("copy", "wire"), ("wire", "record")}, direct_edges)
        history = trace["analysis_history"]
        self.assertEqual(["copy", "wire", "record"], [h["version_id"] for h in history])
        self.assertTrue(all(h["accepted"] and not h["revisit"] and not h["duplicate"] for h in history))
        decompositions = [i["packet"] for i in transport.inputs if i["stage"] == "decompose"]
        self.assertEqual(["copy", "wire", "record"], [p["material"]["version_id"] for p in decompositions])
        self.assertEqual(3, trace["usage"]["decomposition_calls"])
        self.assertFalse(trace["config"]["reanalyze_existing_versions"])
        for index, packet in enumerate(decompositions):
            previous = {h["version_id"]: h["analysis"] for h in history[:index]}
            self.assertEqual(previous, packet["context"]["analyses"])
            self.assertEqual([], packet["context"]["materials"])
            self.assertEqual(set(previous), {m["version_id"] for m in packet["context"]["prior_materials"]})
            self.assertTrue(all("content" not in m for m in packet["context"]["prior_materials"]))
        self.assertEqual({h["version_id"]: h["analysis"] for h in history}, trace["analyses"])
        self.assertEqual("declared", trace["analyses"]["copy"]["relations"][0]["status"])
        self.assertEqual("declared", trace["analyses"]["wire"]["relations"][0]["status"])
        contents = {m["version_id"]: m["content"] for m in trace["materials"]}
        for relation in trace["relations"]:
            if relation["status"] == "direct":
                self.assertIn(relation["from_version"], {s["version_id"] for s in relation["basis"]})
                for span in relation["basis"]:
                    self.assertEqual(span["quote"], contents[span["version_id"]][span["start"]:span["end"]])
        origins = result["origin_summary"]
        self.assertEqual(("located", 1, 1), (origins["status"], origins["claim_count"], origins["located_claims"]))
        self.assertEqual([RECORD["url"]], [s["url"] for s in origins["sources"]])
        self.assertTrue(origins["sources"][0]["claim_ids"])
        self.assertIsInstance(result["analysis"], dict)
        self.assertTrue(any(i["stage"] == "source_trace" for i in transport.inputs))

    def test_locating_an_original_does_not_make_its_false_measurement_true(self):
        report, _ = self.run_fixture([RELEASE, RAW_LOG], mode="false_primary")
        result = report["results"][0]
        claim = result["claims"][0]
        self.assertEqual("original_material_located", claim["provenance_status"])
        self.assertEqual("contradicted", claim["fact_status"])
        self.assertEqual("located", result["origin_summary"]["status"])
        self.assertEqual([RELEASE["url"]], [s["url"] for s in result["origin_summary"]["sources"]])
        self.assertEqual("raw-log", claim["trace"]["verification_history"][-1]["basis"][0]["version_id"])

    def test_old_unrelated_original_suggestion_cannot_replace_source_lineage(self):
        report, _ = self.run_fixture([UNLINKED_COPY, UNRELATED], mode="unrelated")
        result = report["results"][0]
        claim = result["claims"][0]
        self.assertNotEqual("original_material_located", claim["provenance_status"])
        self.assertEqual("unresolved", claim["fact_status"])
        self.assertEqual([], result["origin_summary"]["sources"])
        self.assertEqual(0, result["origin_summary"]["located_claims"])
        self.assertTrue(any(g["id"].startswith("lineage:") for g in claim["trace"]["gaps"]))
        proposal = result["analysis"]["report"]["sources"][0]
        self.assertTrue(proposal["model_proposed_original"])
        self.assertFalse(proposal["is_original"])
        self.assertFalse(proposal["fetched_and_verified"])
        self.assertFalse(result["analysis"]["establishes_truth_or_origin"])

    def test_historical_unavailable_and_future_versions_never_reach_synthesis_or_trace(self):
        unavailable = {"id": "missing-history", "text": CLAIM, "url": "https://synthetic.invalid/missing",
                       "claims": [CLAIM], "as_of": CUTOFF}
        report, transport = self.run_fixture([UNLINKED_COPY, FUTURE, UNKNOWN],
                                             mode="unavailable", extra_news=[unavailable])
        self.assertEqual(2, len(report["results"]))
        first, missing = report["results"]
        self.assertEqual([], first["origin_summary"]["sources"])
        self.assertEqual("unresolved", first["claims"][0]["fact_status"])
        self.assertEqual("failed", missing["status"])
        self.assertTrue(missing["errors"])
        sent = json.dumps(transport.inputs, ensure_ascii=False)
        self.assertNotIn(FUTURE["content"], sent)
        self.assertNotIn(UNKNOWN["content"], sent)

    def test_a_later_claim_failure_preserves_successful_sibling_and_failed_receipt(self):
        report, transport = self.run_fixture([RECORD], claims=[CLAIM, FAILED_CLAIM])
        self.assertEqual(1, len(report["results"]))
        result = report["results"][0]
        self.assertEqual("partial", result["status"])
        self.assertEqual(2, len(result["claims"]))
        good, failed = result["claims"]
        self.assertEqual("supported", good["fact_status"])
        self.assertEqual([], good["errors"])
        self.assertEqual("unresolved", failed["fact_status"])
        self.assertTrue(failed["errors"])
        self.assertTrue(result["errors"])
        self.assertEqual((2, 1), (result["origin_summary"]["claim_count"],
                                  result["origin_summary"]["located_claims"]))
        actual_failures = [c for c in transport.calls if not c["success"]]
        self.assertEqual(1, len(actual_failures))
        receipts = result["execution"]["model_calls"]
        self.assertEqual(len(transport.calls), len(receipts))
        self.assertEqual(1, sum(not c["success"] for c in receipts))
        self.assertTrue(failed["trace"]["analysis_history"])

    def run_html_chain(self, max_documents=4, redirects=False):
        from newsverify.news_sources import NewsSourceCollector
        from newsverify.news_tracing_runner import run_news_tracing

        class LinkTransport(NewsFixtureTransport):
            def generate(self, stage, instructions, packet, schema):
                result = super().generate(stage, instructions, packet, schema)
                if stage == "origin_links":
                    return {"urls": [packet["links"][0]["url"]],
                            "rationale": "Follow the exact source hyperlink in this synthetic page."}
                return result

        input_url = "https://synthetic.invalid/old-dispatch" if redirects else COPY["url"]
        wire_url = "https://synthetic.invalid/archived-wire" if redirects else WIRE["url"]
        pages = {
            COPY["url"]: '<article>Copied dispatch: laboratory measured 30 units. '
                         f'<a href="{wire_url}">Reprinted from wire</a></article>',
            WIRE["url"]: '<article>Wire dispatch: laboratory measured 30 units. '
                         '<a href="/record">Source laboratory record</a></article>',
            RECORD["url"]: '<article>Original synthetic laboratory measurement record: 30 units.</article>',
        }
        fetches, searches = [], []

        def fetch(url):
            fetches.append(url)
            final_url = {input_url: COPY["url"], wire_url: WIRE["url"]}.get(url, url)
            return final_url, {"Content-Type": "text/html"}, pages[final_url].encode()

        def search(query, limit):
            searches.append(query)
            return []

        def factory(**kwargs):
            return NewsSourceCollector(**kwargs, fetch=fetch, search=search)

        transport = LinkTransport()
        report = run_news_tracing({"news": [{"id": "links", "text": CLAIM,
            "url": input_url, "claims": [CLAIM]}], "config": {
            "depth": 0, "max_queries": 1, "max_searches": 1, "max_documents": max_documents,
            "max_origin_depth": 2, "max_origin_calls": 10}},
            transport=transport, collector_factory=factory)
        return report, transport, fetches, searches

    def test_actual_html_links_reach_upstream_records_through_injected_collection(self):
        report, transport, fetches, searches = self.run_html_chain(max_documents=4)
        result = report["results"][0]
        self.assertEqual("completed", result["status"], result["errors"])
        self.assertEqual([COPY["url"], WIRE["url"], RECORD["url"]], fetches)
        self.assertEqual(1, len(searches))
        self.assertEqual("live_collection", result["mode"])
        self.assertEqual("supported", result["claims"][0]["fact_status"])
        self.assertEqual([RECORD["url"]], [s["url"] for s in result["origin_summary"]["sources"]])
        links = result["execution"]["followed_origin_links"]
        self.assertEqual([(1, WIRE["url"], True), (2, RECORD["url"], True)],
                         [(link["depth"], link["url"], link["fetched"]) for link in links])
        self.assertEqual(2, sum(call["stage"] == "origin_links" for call in transport.calls))
        materials = result["sources"]
        self.assertTrue(all(m["available_at"] == m["retrieved_at"] for m in materials))
        self.assertTrue(any(WIRE["url"] in m["content"] for m in materials))

    def test_upstream_chain_uses_tight_document_budget_before_background_search(self):
        report, transport, fetches, searches = self.run_html_chain(max_documents=3)
        result = report["results"][0]
        self.assertEqual("completed", result["status"], result["errors"])
        self.assertEqual([COPY["url"], WIRE["url"], RECORD["url"]], fetches)
        self.assertEqual([], searches)
        self.assertEqual([], result["execution"]["source_errors"])
        self.assertEqual(3, len(result["sources"]))
        self.assertEqual([RECORD["url"]], [s["url"] for s in result["origin_summary"]["sources"]])
        self.assertEqual(["origin_links", "origin_links"], [call["stage"] for call in transport.calls[:2]])
        self.assertTrue(any(request.get("operation") == "search_skipped"
                            for request in result["execution"]["source_requests"]))

    def test_redirect_aliases_preserve_input_anchor_and_observed_upstream_edge(self):
        report, _, fetches, searches = self.run_html_chain(max_documents=3, redirects=True)
        result = report["results"][0]
        self.assertEqual("completed", result["status"], result["errors"])
        self.assertEqual(["https://synthetic.invalid/old-dispatch",
                          "https://synthetic.invalid/archived-wire", RECORD["url"]], fetches)
        self.assertEqual([], searches)
        claim = result["claims"][0]
        trace = claim["trace"]
        materials = {m["version_id"]: m for m in trace["materials"]}
        self.assertEqual(COPY["url"], materials[trace["target"]["source_version_id"]]["url"])
        edges = {(materials[r["from_version"]]["url"], materials[r["to_version"]]["url"])
                 for r in trace["relations"] if r["status"] == "direct"}
        self.assertEqual({(COPY["url"], WIRE["url"]), (WIRE["url"], RECORD["url"])}, edges)
        self.assertEqual("original_material_located", claim["provenance_status"])
        self.assertEqual([RECORD["url"]], [s["url"] for s in result["origin_summary"]["sources"]])
        alias_receipts = [r for r in result["execution"]["source_requests"]
                          if r.get("final_url") and r.get("url") != r["final_url"]]
        self.assertEqual(2, len(alias_receipts))

    def test_origin_catalog_keeps_article_source_after_large_navigation_menu(self):
        from newsverify.news_sources import NewsSourceCollector
        from newsverify.news_tracing_runner import _follow_origin_links

        navigation = "".join(f'<a href="/research/menu{i}">Research navigation {i}</a>'
                             for i in range(82))
        pages = {COPY["url"]: '<nav>' + navigation + '</nav><article>Measurement 30 units. '
                 f'<a href="{RECORD["url"]}">Original measurements</a></article>',
                 RECORD["url"]: '<article>Original measurement record: 30 units.</article>'}
        fetched, packets = [], []

        def fetch(url):
            fetched.append(url)
            return url, {"Content-Type": "text/html"}, pages[url].encode()

        class SelectRecord:
            def generate(self, stage, instructions, packet, schema):
                packets.append(deepcopy(packet))
                return {"urls": [RECORD["url"]], "rationale": "Follow the observed original measurement record."}

        collector = NewsSourceCollector(max_documents=2, fetch=fetch, search=lambda query, limit: [])
        collector.fetch(COPY["url"])
        errors = []
        followed = _follow_origin_links(collector, SelectRecord(), [CLAIM], COPY["url"], 1, errors)
        self.assertEqual(83, len(packets[0]["links"]))
        self.assertIn(RECORD["url"], {link["url"] for link in packets[0]["links"]})
        self.assertEqual(1, packets[0]["max_urls"], "Only one document slot remains")
        self.assertEqual([COPY["url"], RECORD["url"]], fetched)
        self.assertTrue(followed[0]["fetched"])
        self.assertEqual([], errors)
        self.assertEqual([], collector.errors)

    def test_origin_discovery_skips_full_document_budget_without_model_or_fetch_error(self):
        from newsverify.news_sources import NewsSourceCollector
        from newsverify.news_tracing_runner import _follow_origin_links

        body = ('<article>Measurement 30 units. '
                f'<a href="{RECORD["url"]}">Original source</a></article>').encode()
        fetched, generated = [], []

        def fetch(url):
            fetched.append(url)
            return url, {"Content-Type": "text/html"}, body

        class UnnecessaryCall:
            def generate(self, *args):
                generated.append(args)
                raise AssertionError("No upstream document can be fetched within this budget")

        collector = NewsSourceCollector(max_documents=1, fetch=fetch, search=lambda query, limit: [])
        collector.fetch(COPY["url"])
        errors = []
        followed = _follow_origin_links(collector, UnnecessaryCall(), [CLAIM], COPY["url"], 3, errors)
        self.assertEqual([], generated)
        self.assertEqual([COPY["url"]], fetched)
        self.assertEqual([], followed)
        self.assertEqual([], errors)
        self.assertEqual([], collector.errors)
        self.assertTrue(any(request.get("operation") == "origin_discovery_skipped"
                            and request.get("reason") == "document_limit" for request in collector.requests))

    def test_model_direct_label_with_nonlink_quote_cannot_establish_origin(self):
        class FalseEdgeTransport(NewsFixtureTransport):
            def decompose(self, packet):
                result = super().decompose(packet)
                source = packet["material"]
                context = packet["context"]
                available = {source_name(s): s for s in context.get("prior_materials", [])}
                if source_name(source) == "unrelated" and "unlinked-copy" in available:
                    previous = available["unlinked-copy"]
                    result["relations"] = [{"id": "invented-use", "from_version": previous["version_id"],
                        "to_version": source["version_id"], "kind": "cites", "status": "direct",
                        "basis": [retained_basis(context, previous)], "upstream_locator": source["url"],
                        "rationale": "Synthetic false assertion of a link absent from the actual source."}]
                return result

        report, transport = self.run_fixture([UNLINKED_COPY, UNRELATED], transport=FalseEdgeTransport())
        result = report["results"][0]
        claim = result["claims"][0]
        self.assertTrue(claim["trace"]["origins"], "The fixture must offer a model-labelled origin")
        self.assertTrue(any(r["status"] == "direct" for r in claim["trace"]["relations"]),
                        "The fixture must exercise a purported direct edge with an exact non-link quote")
        self.assertEqual(["unlinked-copy", "unrelated"], [entry["packet"]["material"]["version_id"]
                         for entry in transport.inputs if entry["stage"] == "decompose"])
        self.assertNotEqual("original_material_located", claim["provenance_status"])
        self.assertEqual("unresolved", result["origin_summary"]["status"])
        self.assertEqual([], result["origin_summary"]["sources"])

    def test_batch_call_cap_retains_unassessed_items_without_resetting_budget(self):
        from newsverify.news_tracing_runner import run_news_tracing
        transport = NewsFixtureTransport()
        news = [{"id": identifier, "text": CLAIM, "claims": [CLAIM],
                 "materials": [deepcopy(RECORD)], "url": RECORD["url"],
                 "source_version_id": "record", "as_of": CUTOFF} for identifier in ("first", "second")]
        report = run_news_tracing({"news": news, "config": {"depth": 0, "max_queries": 1}},
                                  transport=transport, max_model_calls=1)
        self.assertEqual(1, len(transport.calls))
        self.assertEqual(2, report["summary"]["news_items"])
        self.assertEqual(2, report["summary"]["failed"])
        self.assertEqual(["first", "second"], [r["id"] for r in report["results"]])
        self.assertEqual([1, 0], [len(r["execution"]["model_calls"]) for r in report["results"]])
        self.assertEqual([1, 0], [len(r["execution"]["model_io"]) for r in report["results"]])
        self.assertTrue(all(r["claims"][0]["errors"] for r in report["results"]))
        self.assertTrue(all(r["claims"][0]["fact_status"] == "unresolved" for r in report["results"]))

    def test_cli_defaults_to_local_and_writes_real_offline_report(self):
        from newsverify.cli import main
        from newsverify.news_tracing_runner import run_news_tracing
        transport, options = NewsFixtureTransport(), []

        def run(payload, **kwargs):
            options.append(deepcopy(kwargs))
            return run_news_tracing(payload, **kwargs, transport=transport,
                                    collector_factory=OfflineCollector)

        payload = {"news": [{"id": "cli", "text": CLAIM, "claims": [CLAIM],
            "materials": [deepcopy(RECORD)], "url": RECORD["url"],
            "source_version_id": "record", "as_of": CUTOFF}], "config": {"depth": 0, "max_queries": 1}}
        with tempfile.TemporaryDirectory(prefix="news-trace-cli-test-") as folder:
            input_path, output_path = Path(folder) / "input.json", Path(folder) / "output.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            original = input_path.read_bytes()
            with patch("newsverify.news_tracing_runner.run_news_tracing", side_effect=run):
                with redirect_stdout(io.StringIO()):
                    code = main(["trace-news", str(input_path), "--output", str(output_path)])
                self.assertEqual(0, code)
                self.assertEqual("local", options[0]["tunnel"])
                result = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual("supported", result["results"][0]["claims"][0]["fact_status"])
                self.assertEqual("located", result["results"][0]["origin_summary"]["status"])
                self.assertEqual(original, input_path.read_bytes())
                count = len(transport.calls)
                with redirect_stderr(io.StringIO()):
                    code = main(["trace-news", str(input_path), "--output", str(input_path)])
                self.assertEqual(2, code)
                self.assertEqual(count, len(transport.calls))
                self.assertEqual(original, input_path.read_bytes())

    def test_cli_help_exposes_both_tunnels_and_batch_budget(self):
        from newsverify.cli import main
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["trace-news", "--help"])
        self.assertEqual(0, caught.exception.code)
        self.assertIn("{local,api}", output.getvalue())
        self.assertIn("--max-model-calls", output.getvalue())

    def test_research_advice_guides_selection_without_changing_explicit_claim_or_sources(self):
        from newsverify.news_tracing_runner import RESEARCH_ADVICE_LIMIT

        class AdvisedTransport(NewsFixtureTransport):
            def synthesis(self, stage):
                result = super().synthesis(stage)
                if stage == "deconstruct":
                    result["key_claims"] = [FAILED_CLAIM]
                if stage == "source_trace":
                    result["sources"][0]["url"] = RECORD["url"]
                if stage == "synthesis":
                    result["key_findings"] = ["Inspect the original measurement record."] + ["finding " + "x" * 900] * 4
                    result["information_gaps"] = [f"Unverified question {i}: " + "y" * 900 for i in range(6)]
                return result

            def generate(self, stage, instructions, packet, schema):
                result = super().generate(stage, instructions, packet, schema)
                if stage == "select" and "record" in {m["version_id"] for m in packet["catalog"]}:
                    if "record" in packet["research_advice"]["candidate_version_ids"]:
                        return {"version_id": "record", "rationale": "Inspect the eligible candidate suggested by untrusted research."}
                return result

        report, transport = self.run_fixture([UNLINKED_COPY, UNRELATED, RECORD], transport=AdvisedTransport())
        item = report["results"][0]
        advice = item["research_advice"]
        self.assertTrue(advice["truncated"])
        self.assertLessEqual(len(json.dumps(advice, ensure_ascii=False)), RESEARCH_ADVICE_LIMIT)
        self.assertEqual(["record"], advice["candidate_version_ids"])
        self.assertIn("Inspect the original measurement record.", advice["findings_to_check"])
        formal = [entry for entry in transport.inputs if entry["stage"] in {"decompose", "select", "verify"}]
        self.assertEqual({"decompose", "select", "verify"}, {entry["stage"] for entry in formal})
        for entry in formal:
            self.assertEqual(advice, entry["packet"]["research_advice"])
            self.assertEqual(CLAIM, entry["packet"]["target"]["text"])
            self.assertIn("UNTRUSTED", entry["instructions"])
            self.assertIn("never by research_advice", entry["instructions"])
        decomposed = [entry["packet"]["material"]["version_id"] for entry in formal if entry["stage"] == "decompose"]
        self.assertEqual(["unlinked-copy", "record"], decomposed[:2])
        self.assertEqual([UNLINKED_COPY, UNRELATED, RECORD], item["sources"])
        self.assertEqual("supported", item["claims"][0]["fact_status"])
        self.assertNotEqual("original_material_located", item["claims"][0]["provenance_status"])
        latest_check = [entry["packet"]["context"] for entry in formal if entry["stage"] == "verify"][-1]
        self.assertEqual(["unrelated"], [m["version_id"] for m in latest_check["materials"]])
        prior_record = next(m for m in latest_check["prior_materials"] if m["version_id"] == "record")
        self.assertNotIn("content", prior_record)
        self.assertIn("record", latest_check["verified_version_ids"])
        self.assertEqual(basis(RECORD), retained_basis(latest_check, prior_record))
        final_evidence = item["claims"][0]["trace"]["verification_history"][-1]["basis"]
        self.assertEqual(["record"], [span["version_id"] for span in final_evidence])
        self.assertEqual(RECORD["content"], final_evidence[0]["quote"])
        actual = [entry for entry in item["execution"]["model_io"] if entry["stage"] in {"decompose", "select", "verify"}]
        self.assertEqual([entry["packet"] for entry in formal], [entry["packet"] for entry in actual])
        self.assertEqual(len(transport.calls), len(item["execution"]["model_calls"]))

    def test_research_only_sentence_cannot_be_quoted_as_canonical_evidence(self):
        invented = "ADVICE_ONLY fabricated measurement claim must never become source evidence."

        class AdviceQuoteTransport(NewsFixtureTransport):
            def synthesis(self, stage):
                result = super().synthesis(stage)
                if stage == "synthesis":
                    result["key_findings"] = [invented]
                return result

            def verify(self, packet):
                self.asserted_advice = packet["research_advice"]
                return {"verdict": "supported", "basis": [{"version_id": "record", "quote": invented}],
                        "rationale": "Attempt to treat generated research as evidence.", "gaps": [], "resolutions": []}

        report, transport = self.run_fixture([RECORD], transport=AdviceQuoteTransport())
        item, claim = report["results"][0], report["results"][0]["claims"][0]
        self.assertIn(invented, transport.asserted_advice["findings_to_check"])
        self.assertNotIn(invented, json.dumps(item["sources"]))
        self.assertTrue(claim["errors"])
        self.assertNotEqual("completed", item["status"])
        self.assertEqual("unresolved", claim["fact_status"])
        self.assertTrue(all(call["success"] for call in item["execution"]["model_calls"]),
                        "Output validation failure must remain distinct from transport success")

    def test_failed_research_stays_partial_even_when_formal_evidence_succeeds(self):
        class FailedResearchTransport(NewsFixtureTransport):
            def generate(self, stage, instructions, packet, schema):
                result = super().generate(stage, instructions, packet, schema)
                if stage == "source_verify":
                    self.calls[-1].update(success=False, status="failed", usage=None)
                    raise TunnelError("Synthetic research comparison failure.")
                return result

        for mode in ("full", "claim"):
            with self.subTest(mode=mode):
                report, transport = self.run_fixture([UNRELATED, RECORD], transport=FailedResearchTransport(), research_mode=mode)
                item = report["results"][0]
                self.assertEqual("supported", item["claims"][0]["fact_status"])
                self.assertEqual([], item["claims"][0]["errors"])
                self.assertEqual("partial", item["status"])
                self.assertTrue(item["errors"])
                self.assertTrue(item["research_advice"]["research_had_errors"])
                self.assertEqual((0, 1, 0), (report["summary"]["completed"], report["summary"]["partial"], report["summary"]["failed"]))
                self.assertEqual(1, sum(not call["success"] for call in item["execution"]["model_calls"]))
                self.assertEqual(len(transport.calls), len(item["execution"]["model_calls"]))

    def test_claim_mode_research_uses_exact_explicit_target_and_keeps_formal_checks(self):
        exact = "  " + CLAIM + "  "
        report, transport = self.run_fixture([RECORD], claims=[exact], research_mode="claim",
                                             news_text="UNRELATED_BACKGROUND_SHOULD_NOT_REPLACE_EXPLICIT_CLAIM")
        item, claim = report["results"][0], report["results"][0]["claims"][0]
        self.assertEqual("completed", item["status"], item["errors"])
        self.assertEqual("supported", claim["fact_status"])
        self.assertEqual("original_material_located", claim["provenance_status"])
        self.assertEqual(exact, claim["text"])
        self.assertEqual(exact, claim["trace"]["target"]["text"])
        self.assertEqual(CUTOFF, claim["trace"]["target"]["as_of"])
        self.assertEqual("record", claim["trace"]["target"]["source_version_id"])
        self.assertEqual([RECORD], item["sources"])
        omitted = {"causal_dig", "grounding_check", "timeline_build", "perspective", "direct_response"}
        self.assertFalse(omitted & {entry["stage"] for entry in transport.inputs})
        deconstruct = next(entry for entry in transport.inputs if entry["stage"] == "deconstruct")
        self.assertEqual(exact, deconstruct["packet"]["request"])
        synthesis = next(entry for entry in transport.inputs if entry["stage"] == "synthesis")
        self.assertEqual(exact, json.loads(synthesis["packet"]["request"])["research_scope"]["fixed_claim"])
        for entry in transport.inputs:
            if entry["stage"] in {"decompose", "select", "verify"}:
                self.assertEqual(exact, entry["packet"]["target"]["text"])
                self.assertEqual(item["research_advice"], entry["packet"]["research_advice"])
        self.assertEqual(len(transport.calls), len(item["execution"]["model_calls"]))

    def test_claim_mode_validates_one_explicit_claim_per_item_before_any_call(self):
        from newsverify.news_tracing_runner import run_news_tracing
        base = {"id": "one", "text": CLAIM, "claims": [CLAIM], "materials": [deepcopy(RECORD)], "as_of": CUTOFF}
        for claims in (None, [], [CLAIM, FAILED_CLAIM]):
            second = {**deepcopy(base), "id": "two"}
            if claims is None:
                second.pop("claims")
            else:
                second["claims"] = claims
            transport = NewsFixtureTransport()
            with self.subTest(claims=claims), self.assertRaises(ValueError):
                run_news_tracing({"news": [base, second], "config": {"research_mode": "claim"}}, transport=transport)
            self.assertEqual([], transport.calls)
        second = {**deepcopy(base), "id": "two"}
        transport = NewsFixtureTransport()
        result = run_news_tracing({"news": [base, second], "config": {"research_mode": "claim", "max_queries": 1}},
                                  transport=transport)
        self.assertEqual(2, result["summary"]["completed"])
        self.assertEqual(2, result["summary"]["claims"])
        self.assertEqual(["one:c1", "two:c1"], [item["claims"][0]["id"] for item in result["results"]])

    def test_same_url_versions_reach_research_separately_after_cutoff_filter(self):
        from newsverify.news_tracing_runner import _SnapshotCollector
        from newsverify.provenance import _time
        excerpt, complete, future, unknown = map(deepcopy, (UNLINKED_COPY, RECORD, FUTURE, UNKNOWN))
        shared_url = "https://synthetic.invalid/same-paper"
        for source in (excerpt, complete, future, unknown):
            source["url"] = shared_url
        snapshot = _SnapshotCollector([excerpt, complete, future, unknown], _time(CUTOFF, "cutoff"))
        self.assertEqual(1, len(snapshot.documents))
        self.assertEqual(["unlinked-copy", "record"], [doc.version_id for doc in snapshot.evidence_documents])
        self.assertEqual("record", snapshot.fetch(shared_url).version_id)
        self.assertEqual(1, len(snapshot.search("same paper")))
        report, transport = self.run_fixture([excerpt, complete, future, unknown])
        research = [entry for entry in transport.inputs if "fetched_evidence" in entry["packet"]]
        self.assertTrue(research)
        for entry in research:
            versions = entry["packet"]["fetched_evidence"]
            self.assertEqual(["unlinked-copy", "record"], [source["version_id"] for source in versions])
            self.assertEqual([excerpt["content"], complete["content"]], [source["content"] for source in versions])
            self.assertEqual([shared_url, shared_url], [source["url"] for source in versions])
            self.assertEqual([0, 0], [source["content_start"] for source in versions])
            self.assertEqual([len(excerpt["content"]), len(complete["content"])], [source["content_end"] for source in versions])
        self.assertNotIn(FUTURE["content"], json.dumps(transport.inputs))
        self.assertNotIn(UNKNOWN["content"], json.dumps(transport.inputs))
        self.assertEqual(["unlinked-copy", "record"], [source["version_id"] for source in report["results"][0]["sources"]])


if __name__ == "__main__":
    unittest.main()
