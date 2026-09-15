"""Synthetic bibliography provenance boundaries; no network or model calls."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from newsverify.news_client import url_key
from newsverify.news_tracing_runner import _located_origins, _observed_links, _resolved_citations, _ResearchAdviceTransport
from newsverify.provenance import MaterialVersion


CUTOFF = "2024-12-31T23:59:59Z"
AUTHOR = "Ada Example"
JOURNAL = "Journal of Test Geology"
TITLE = "Meteorite chromium measurements in dated samples"
DOI = "10.5555/test.paper"
SOURCE_TEXT = f"Ada Example led the meteorite chromium study published in {JOURNAL} on March 14, 2021."
TARGET_TEXT = f"{TITLE}\n{AUTHOR}\n{JOURNAL}\nDOI: {DOI}\nAbstract\nMeteorite chromium measurements.\nRESULTS\nRecorded measurements."
CHECKS = {key: True for key in ("source_citation_context_bound", "source_version_unchanged",
    "archive_cutoff_and_identity", "selected_publisher_url", "page_title_matches",
    "identity_in_front_matter", "source_topics_in_primary", "primary_body_present")}


def material(version_id, content):
    return MaterialVersion(version_id=version_id, url="https://synthetic.invalid/" + version_id,
        content=content, retrieved_at="2026-01-01T00:00:00Z", published_at="2021-03-14T00:00:00Z",
        available_at="2023-01-01T00:00:00Z", availability_basis="Explicit synthetic archive fixture.")


def hash_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def span(version, text, quote):
    start = text.index(quote)
    return {"version_id": version, "start": start, "end": start + len(quote), "quote": quote}


class BibliographicLineageTests(unittest.TestCase):
    def setUp(self):
        self.source, self.target = material("source", SOURCE_TEXT), material("target", TARGET_TEXT)
        self.materials = [self.source, self.target]
        self.receipt = {"source_url": self.source.url, "target_url": self.target.url,
            "source_text_sha256": hash_text(SOURCE_TEXT), "target_text_sha256": hash_text(TARGET_TEXT),
            "source_quotes": [SOURCE_TEXT], "target_quotes": [TITLE, DOI, AUTHOR, JOURNAL],
            "identity": {"doi": DOI, "title": TITLE, "authors": [AUTHOR], "journal": JOURNAL},
            "cutoff": CUTOFF, "valid": True, "checks": deepcopy(CHECKS), "receipt_id": "synthetic-proof",
            "edge_kind": "resolved_citation"}
        self.collector = SimpleNamespace(bibliographic_bindings=[self.receipt], requests=[],
            documents={self.source.url: SimpleNamespace(url=self.source.url, title="Research news", content=SOURCE_TEXT, links=[]),
                       self.target.url: SimpleNamespace(url=self.target.url, title=TITLE, content=TARGET_TEXT, links=[])})

    def resolutions(self):
        return _resolved_citations(self.materials, self.collector, CUTOFF)

    def claim(self):
        return {"id": "claim", "trace": {"provenance_status": "original_material_located", "errors": [],
            "target": {"source_version_id": self.source.version_id}, "materials": [asdict(m) for m in self.materials],
            "relations": [{"id": "citation", "from_version": "source", "to_version": "target", "kind": "cites",
                "status": "direct", "basis": [span("source", SOURCE_TEXT, SOURCE_TEXT), span("target", TARGET_TEXT, TITLE)],
                "rationale": "Native synthetic finding from both actual source versions."}],
            "origins": [{"version_id": "target", "material_kind": "original_record",
                "basis": [span("target", TARGET_TEXT, "Recorded measurements.")]}]}}

    def located(self, claim=None, resolutions=None):
        observed = _observed_links(self.materials, self.collector)
        return _located_origins(self.claim() if claim is None else claim, observed,
            self.resolutions() if resolutions is None else resolutions)

    def test_incremental_prior_endpoint_keeps_validated_bibliography_available(self):
        base = SimpleNamespace(kind="local", model="fixture", reasoning_effort="low", generate=Mock(return_value={}))
        wrapper = _ResearchAdviceTransport(base, {}, self.resolutions())
        packet = {"material": asdict(self.target), "context": {"materials": [],
                  "prior_materials": [{"version_id": "source", "content_sha256": hash_text(SOURCE_TEXT)}],
                  "analyses": {"source": {"fragments": []}}}}
        wrapper.generate("decompose", "instruction", packet, {})
        sent = base.generate.call_args.args[2]
        self.assertEqual(self.resolutions(), sent["citation_resolutions"])
        self.assertNotIn("content", sent["context"]["prior_materials"][0])

    def test_prior_metadata_without_accepted_analysis_does_not_admit_endpoint(self):
        base = SimpleNamespace(kind="local", model="fixture", reasoning_effort="low", generate=Mock(return_value={}))
        wrapper = _ResearchAdviceTransport(base, {}, self.resolutions())
        packet = {"material": asdict(self.target), "context": {"materials": [],
                  "prior_materials": [{"version_id": "source", "content_sha256": hash_text(SOURCE_TEXT)}],
                  "analyses": {}}}
        wrapper.generate("decompose", "instruction", packet, {})
        self.assertNotIn("citation_resolutions", base.generate.call_args.args[2])

    def test_valid_binding_admits_typed_edge_without_fabricating_hyperlinks(self):
        before_materials = [asdict(m) for m in self.materials]
        before_docs = deepcopy(self.collector.documents)
        observed_before = _observed_links(self.materials, self.collector)
        result = self.resolutions()
        self.assertEqual(result[0]["edge_kind"], "resolved_citation")
        self.assertEqual((result[0]["source_version_id"], result[0]["target_version_id"]), ("source", "target"))
        self.assertNotIn(url_key(self.target.url), observed_before["source"])
        self.assertEqual(_observed_links(self.materials, self.collector), observed_before)
        self.assertEqual([asdict(m) for m in self.materials], before_materials)
        self.assertEqual(self.collector.documents, before_docs)

    def test_stale_hashes_cutoff_and_unverified_receipts_rejected(self):
        cases = [{"source_text_sha256": "bad"}, {"target_text_sha256": "bad"},
                 {"cutoff": "2025-12-31T23:59:59Z"}, {"valid": False}, {"valid": "true"},
                 {"edge_kind": "observed_hyperlink"}, {"source_url": "https://synthetic.invalid/missing"}]
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.collector.bibliographic_bindings = [deepcopy(self.receipt) | mutation]
                with self.assertRaises(ValueError):
                    self.resolutions()

    def test_false_or_missing_identity_check_rejected(self):
        for key in CHECKS:
            for value in (False, None, "true"):
                with self.subTest(key=key, value=value):
                    receipt = deepcopy(self.receipt)
                    receipt["checks"][key] = value
                    self.collector.bibliographic_bindings = [receipt]
                    with self.assertRaises(ValueError):
                        self.resolutions()

    def test_post_cutoff_endpoint_cannot_be_admitted_by_pre_cutoff_receipt(self):
        self.materials[1] = replace(self.target, available_at="2025-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "endpoint not admitted"):
            self.resolutions()

    def test_inexact_or_empty_source_and_target_quotes_rejected(self):
        for side in ("source", "target"):
            for quotes in ([], ["invented quotation"], [""], [" "], None):
                with self.subTest(side=side, quotes=quotes):
                    receipt = deepcopy(self.receipt)
                    receipt[side + "_quotes"] = quotes
                    self.collector.bibliographic_bindings = [receipt]
                    with self.assertRaises(ValueError):
                        self.resolutions()

    def test_stale_article_title_and_unbound_author_or_journal_rejected(self):
        for key, value in (("title", "Another article"), ("doi", "10.5555/other"),
                           ("authors", ["Bea Stranger"]), ("journal", "Unrelated Journal")):
            with self.subTest(key=key):
                receipt = deepcopy(self.receipt)
                receipt["identity"][key] = value
                self.collector.bibliographic_bindings = [receipt]
                with self.assertRaises(ValueError):
                    self.resolutions()
        self.collector.bibliographic_bindings = [self.receipt]
        self.collector.documents[self.target.url].title = "A page merely referencing the paper"
        with self.assertRaises(ValueError):
            self.resolutions()

    def test_incidental_exact_target_quote_does_not_replace_identity_anchors(self):
        self.receipt["target_quotes"] = ["RESULTS"]
        with self.assertRaises(ValueError):
            self.resolutions()

    def test_doi_prefix_is_not_an_exact_identity_match(self):
        self.receipt["identity"]["doi"] = "10.5555/test"
        with self.assertRaises(ValueError):
            self.resolutions()

    def test_source_quote_must_include_citation_context(self):
        self.receipt["source_quotes"] = [AUTHOR, JOURNAL]
        with self.assertRaisesRegex(ValueError, "citation context"):
            self.resolutions()

    def test_binding_alone_cannot_produce_native_original_finding(self):
        for mutation in ({"provenance_status": "unresolved"}, {"origins": []},
                         {"errors": [{"type": "SyntheticFailure"}]}, {"relations": []}):
            with self.subTest(mutation=mutation):
                claim = self.claim()
                claim["trace"].update(mutation)
                self.assertEqual(self.located(claim), [])

    def test_direct_cites_with_both_exact_endpoint_spans_and_binding_accepted(self):
        result = self.located()
        self.assertEqual([r["version_id"] for r in result], ["target"])
        self.assertEqual(self.located(resolutions=[]), [])

    def test_supports_declared_and_other_lineage_kinds_cannot_use_bibliography_edge(self):
        for mutation in ({"kind": "supports"}, {"kind": "quotes"}, {"kind": "derives"},
                         {"kind": "reprints"}, {"status": "declared"}):
            with self.subTest(mutation=mutation):
                claim = self.claim()
                claim["trace"]["relations"][0].update(mutation)
                self.assertEqual(self.located(claim), [])

    def test_missing_either_endpoint_quote_cannot_use_bibliography_edge(self):
        for keep in ([], [0], [1]):
            claim = self.claim()
            basis = claim["trace"]["relations"][0]["basis"]
            claim["trace"]["relations"][0]["basis"] = [basis[i] for i in keep]
            with self.subTest(keep=keep):
                self.assertEqual(self.located(claim), [])

    def test_version_ids_without_actual_exact_spans_do_not_establish_citation(self):
        variants = [[{"version_id": "source"}, {"version_id": "target"}],
                    [{"version_id": "source", "quote": "invented"}, {"version_id": "target", "quote": "invented"}],
                    [span("source", SOURCE_TEXT, SOURCE_TEXT), span("target", TARGET_TEXT, TITLE) | {"start": 1}]]
        for basis in variants:
            claim = self.claim()
            claim["trace"]["relations"][0]["basis"] = basis
            with self.subTest(basis=basis):
                self.assertEqual(self.located(claim), [])


if __name__ == "__main__":
    unittest.main()
