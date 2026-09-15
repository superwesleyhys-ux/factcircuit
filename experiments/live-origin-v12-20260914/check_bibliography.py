"""Synthetic safeguards; optional generic live lookup from supplied source clues.

Default: python check_bibliography.py (no network or model calls).
Live: --live-source sources.json --source-url URL --clues clues.json
      --receipt-dir PRIVATE_DIRECTORY
The clues JSON is an exact CitationClues object; no target IDs are accepted.
"""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit

from bibliographic_locator import (BibliographicLocator, BibliographyError, CitationClues,
                                  LOCATOR_NOTICE, validate_clues)

CUTOFF = "2024-12-31T23:59:59Z"
TEXT = ("March 14, 2021\nThe meteorite chromium study led by Ada Example was published "
        "in Journal of Test Geology on March 14.\nThe study measured meteorite chromium in samples.")
CLUES = CitationClues((TEXT.splitlines()[0], TEXT.splitlines()[1]), "Ada Example",
                     "Journal of Test Geology", "2021-03-14", ("meteorite", "chromium"))


def source(text=TEXT, **kwargs):
    values = dict(url="https://news.example.org/research", title="Research news", content=text,
                  available_at="2023-01-01T00:00:00Z", published_at=None)
    return SimpleNamespace(**(values | kwargs))


def paper(**kwargs):
    title = "Meteorite chromium measurements in dated samples"
    values = dict(url="https://journals.example.org/paper", title=title,
                  content=title + "\nAda Example\nJournal of Test Geology\nDOI: 10.5555/test.paper\n"
                          "March 14, 2021\nAbstract\nMeteorite chromium was measured.\nRESULTS\nMeasurements.",
                  available_at="2023-02-01T00:00:00Z", capture_at="2023-02-01T00:00:00Z",
                  archive_url="https://web.archive.org/web/20230201000000id_/https://journals.example.org/paper")
    return SimpleNamespace(**(values | kwargs))


def record(**kwargs):
    values = {"DOI": "10.5555/test.paper", "title": [paper().title],
              "author": [{"given": "Ada", "family": "Example", "affiliation": [{"name": "Private institution detail"}]}],
              "container-title": ["Journal of Test Geology"], "published": {"date-parts": [[2021, 3, 16]]},
              "created": {"date-time": "2021-03-14T10:30:00Z"},
              "resource": {"primary": {"URL": paper().url}}, "URL": "https://doi.org/10.5555/test.paper"}
    return values | kwargs


def response(items=None, total=None):
    items = [record()] if items is None else items
    return json.dumps({"status": "ok", "message": {"items": items, "total-results": len(items) if total is None else total}}).encode()


class BibliographyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def locator(self, raw=None, **kwargs):
        def request(url):
            self.calls.append(url)
            return response() if raw is None else raw
        return BibliographicLocator(cutoff=CUTOFF, receipt_dir=self.temp.name, request=request, **kwargs)

    def test_source_grounding_precedes_network(self):
        invalid = [replace(CLUES, author="Bea Stranger"), replace(CLUES, journal="Different Journal"),
                   replace(CLUES, keywords=("meteorite", "inventedtopic")), replace(CLUES, publication_date="2021-03-15"),
                   replace(CLUES, quotes=("Invented citation",)), replace(CLUES, quotes=(TEXT.splitlines()[0],)),
                   replace(CLUES, keywords=("chromium", "chromium"))]
        for clues in invalid:
            with self.subTest(clues=clues), self.assertRaises(BibliographyError):
                self.locator().resolve(source(), clues)
        self.assertEqual(self.calls, [])

    def test_metadata_date_requires_citation_day(self):
        clues = replace(CLUES, quotes=(TEXT.splitlines()[1],))
        validate_clues(source(published_at="2021-03-14T12:30:00Z"), clues, CUTOFF)
        validate_clues(source(published_at="2021-03-14"), clues, CUTOFF)
        text = TEXT.replace(" on March 14", "")
        with self.assertRaisesRegex(BibliographyError, "publication_date_not_source_bound"):
            validate_clues(source(text, published_at="2021-03-14"), replace(clues, quotes=(text.splitlines()[1],)), CUTOFF)

    def test_dates_accept_day_month_year_and_separate_exact_anchor(self):
        text = TEXT.replace("March 14, 2021", "14 Mar 2021")
        validate_clues(source(text), replace(CLUES, quotes=tuple(text.splitlines()[:2])), CUTOFF)

    def test_author_journal_date_and_topics_can_have_separate_exact_anchors(self):
        citation = "Ada Example led the study published in Journal of Test Geology on March 14."
        topic = "The new study measured meteorite chromium."
        text = "March 14, 2021\n" + citation + "\n" + topic
        clues = replace(CLUES, quotes=tuple(text.splitlines()))
        self.assertEqual(len(self.locator().resolve(source(text), clues).candidates), 1)
        text = text.replace(" on March 14", "")
        validate_clues(source(text), replace(CLUES, quotes=tuple(text.splitlines())), CUTOFF)

    def test_article_date_alone_cannot_bind_different_citation_day(self):
        text = TEXT.replace("on March 14.", "on March 15.")
        with self.assertRaisesRegex(BibliographyError, "citation_publication_day_not_bound"):
            self.locator().resolve(source(text), replace(CLUES, quotes=tuple(text.splitlines()[:2])))
        self.assertEqual(self.calls, [])

    def test_current_source_rejected_before_lookup(self):
        with self.assertRaisesRegex(BibliographyError, "source_after_cutoff"):
            self.locator().resolve(source(available_at="2026-01-01T00:00:00Z"), CLUES)
        self.assertEqual(self.calls, [])

    def test_exact_month_query_and_identity_only_projection(self):
        result = self.locator().resolve(source(), CLUES)
        params = parse_qs(urlsplit(self.calls[0]).query)
        self.assertEqual(params["filter"], ["from-pub-date:2021-03-01,until-pub-date:2021-03-31,until-created-date:2024-12-31,type:journal-article"])
        self.assertEqual(params["rows"], ["5"])
        self.assertEqual(set(params["select"][0].split(",")), {"DOI", "title", "author", "container-title", "published", "resource", "URL", "created"})
        packet = result.candidates[0].to_packet()
        self.assertEqual(packet["notice"], LOCATOR_NOTICE)
        self.assertNotIn("affiliation", json.dumps(packet))
        self.assertNotIn("created", json.dumps(packet))
        self.assertNotIn("Private institution", json.dumps(packet))
        self.assertNotIn("test.paper", self.calls[0])

    def test_outcome_fields_are_not_admitted_even_if_provider_ignores_select(self):
        for extra in ({"abstract": "Later outcome information"}, {"relation": {}}, {"update-to": []}, {"is-referenced-by-count": 20}):
            with self.subTest(extra=extra), self.assertRaisesRegex(BibliographyError, "unexpected_crossref_fields"):
                self.locator(response([record(**extra)])).resolve(source(), CLUES)

    def test_fuzzy_search_candidates_need_independent_identity_matches(self):
        wrong = [record(**{"container-title": ["Other Journal"]}), record(author=[{"given": "Bea", "family": "Other"}]),
                 record(title=["Unrelated mineral measurement"]), record(published={"date-parts": [[2025, 3, 16]]}),
                 record(created={"date-time": "2026-01-01T00:00:00Z"}),
                 record(resource={"primary": {"URL": "http://127.0.0.1/secret"}}),
                 record(resource={"primary": {"URL": "https://journals.example.org:444/paper"}})]
        for item in wrong:
            with self.subTest(item=item):
                self.assertEqual(self.locator(response([item])).resolve(source(), CLUES).candidates, ())

    def test_one_title_topic_can_locate_paper_when_news_uses_paraphrase(self):
        text = TEXT.replace("meteorite", "cosmic dust")
        clues = replace(CLUES, quotes=tuple(text.splitlines()[:2]), keywords=("chromium", "cosmic dust"))
        locator = self.locator()
        result = locator.resolve(source(text), clues)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, paper().title)
        # Candidate discovery does not relax the full archived topic gate.
        proof = locator.verify(source(text), clues, result.candidates[0], paper())
        self.assertFalse(proof.valid)
        self.assertFalse(proof.checks["source_topics_in_primary"])

    def test_lookup_attempts_and_result_bounds_are_enforced(self):
        locator = self.locator(max_lookups=1)
        locator.resolve(source(), CLUES)
        with self.assertRaisesRegex(BibliographyError, "bibliography_lookup_budget"):
            locator.resolve(source(), CLUES)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaisesRegex(BibliographyError, "invalid_crossref_response"):
            self.locator(response([record()] * 6)).resolve(source(), CLUES)
        result = self.locator(response(total=20)).resolve(source(), CLUES)
        self.assertEqual(result.status, "bounded_candidates")

    def test_failed_transport_consumes_allowance_and_leaves_receipt(self):
        def fail(url):
            raise BibliographyError("crossref_timeout")
        locator = BibliographicLocator(cutoff=CUTOFF, receipt_dir=self.temp.name, max_lookups=1, request=fail)
        with self.assertRaisesRegex(BibliographyError, "crossref_timeout"):
            locator.resolve(source(), CLUES)
        with self.assertRaisesRegex(BibliographyError, "bibliography_lookup_budget"):
            locator.resolve(source(), CLUES)
        receipt = json.loads(next(Path(self.temp.name).glob("*.json")).read_text())
        self.assertEqual(receipt["status"], "failed")

    def test_valid_proof_has_independently_checkable_hashes_and_exact_quotes(self):
        locator = self.locator()
        candidate = locator.resolve(source(), CLUES).candidates[0]
        proof = locator.verify(source(), CLUES, candidate, paper())
        self.assertTrue(proof.valid, proof.checks)
        self.assertEqual(proof.edge_kind, "resolved_citation")
        self.assertTrue(all(q in source().content for q in proof.source_quotes))
        self.assertTrue(all(q in paper().content for q in proof.target_quotes))
        self.assertEqual(set(proof.identity), {"doi", "title", "authors", "journal"})
        json.dumps(asdict(proof))
        self.assertFalse(hasattr(source(), "links"))

    def test_archive_cutoff_redirect_and_body_identity_fail_closed(self):
        locator = self.locator()
        candidate = locator.resolve(source(), CLUES).candidates[0]
        bad = [paper(capture_at="2025-02-01T00:00:00Z"), paper(available_at="2026-02-01T00:00:00Z"),
               paper(archive_url="https://journals.example.org/paper"),
               paper(archive_url="https://web.archive.org/web/20230201000000id_/https://journals.example.org/other"),
               paper(title="Review of meteorite studies"), paper(content=paper().content.replace("10.5555/test.paper", "10.5555/test.other")),
               paper(content=paper().content.replace("RESULTS", "Navigation")),
               paper(url="https://journals.example.org/other")]
        for document in bad:
            with self.subTest(document=document):
                self.assertFalse(locator.verify(source(), CLUES, candidate, document).valid)

    def test_reference_only_doi_and_forged_candidate_do_not_prove_origin(self):
        locator = self.locator()
        candidate = locator.resolve(source(), CLUES).candidates[0]
        self.assertFalse(locator.verify(source(), CLUES, candidate, paper(title="A review", content="References\n" + paper().content)).valid)
        with self.assertRaisesRegex(BibliographyError, "candidate_not_issued"):
            locator.verify(source(), CLUES, replace(candidate, title="Forged"), paper())
        with self.assertRaisesRegex(BibliographyError, "candidate_source_binding_changed"):
            locator.verify(source(TEXT + " changed"), CLUES, candidate, paper())

    def test_doi_prefix_does_not_match_a_different_doi(self):
        locator = self.locator()
        candidate = locator.resolve(source(), CLUES).candidates[0]
        for suffix in (".supplement", "/appendix", "-correction"):
            with self.subTest(suffix=suffix):
                target = paper(content=paper().content.replace(candidate.doi, candidate.doi + suffix))
                self.assertFalse(locator.verify(source(), CLUES, candidate, target).valid)

    @staticmethod
    def formal_fixture(identifier="https://doi.org/10.5555/test.paper", gap="\nOther unrelated page content.\n"):
        block = "Meteorite chromium measurements in dated samples\n[" + identifier + "]\nAda Example\nJournal of Test Geology (2021)"
        notice = "Matters Arising [" + identifier + "] to this article was published on 14 March 2021"
        text = block + gap + notice
        return source(text), replace(CLUES, quotes=(block, notice))

    def test_formal_reference_block_and_separate_notice_join_by_exact_doi(self):
        document, clues = self.formal_fixture()
        binding = validate_clues(document, clues, CUTOFF)
        self.assertEqual(binding["citation_context_joins"][0]["join_basis"], "exact_shared_identifier")
        self.assertEqual(binding["citation_context_joins"][0]["identifiers"], ["doi:10.5555/test.paper"])
        self.assertEqual(len(self.locator().resolve(document, clues).candidates), 1)

    def test_formal_citation_doi_must_equal_candidate_doi_exactly(self):
        document, clues = self.formal_fixture()
        for doi in ("10.5555/other.paper", "10.5555/test.paper.supplement"):
            with self.subTest(doi=doi):
                result = self.locator(response([record(DOI=doi)])).resolve(document, clues)
                self.assertEqual(result.candidates, ())
                self.assertEqual(result.status, "no_match")

    def test_unrelated_background_doi_does_not_constrain_formal_citation(self):
        document, clues = self.formal_fixture()
        background = "Background meteorite chromium data: https://doi.org/10.5555/background.data"
        document = source(document.content + "\n" + background)
        clues = replace(clues, quotes=(*clues.quotes, background))
        binding = validate_clues(document, clues, CUTOFF)
        self.assertEqual(binding["formal_citation_dois"], ["10.5555/test.paper"])
        result = self.locator().resolve(document, clues)
        self.assertEqual(len(result.candidates), 1)

    def test_multiple_joined_formal_citation_dois_are_ambiguous(self):
        document, clues = self.formal_fixture()
        second_block = clues.quotes[0].replace("test.paper", "other.paper")
        second_notice = clues.quotes[1].replace("test.paper", "other.paper")
        changed = source(document.content + "\n" + second_block + "\n" + second_notice)
        with self.assertRaisesRegex(BibliographyError, "ambiguous_formal_citation_identifier"):
            self.locator().resolve(changed, replace(clues, quotes=(*clues.quotes, second_block, second_notice)))
        self.assertEqual(self.calls, [])

    def test_formal_reference_can_join_by_exact_non_doi_article_link(self):
        document, clues = self.formal_fixture("https://journals.example.org/paper")
        binding = validate_clues(document, clues, CUTOFF)
        self.assertEqual(binding["citation_context_joins"][0]["identifiers"], ["https://journals.example.org/paper"])

    def test_adjacent_formal_block_and_dated_notice_need_no_repeated_identifier(self):
        document, clues = self.formal_fixture()
        block = clues.quotes[0].replace("[https://doi.org/10.5555/test.paper]\n", "")
        notice = "This article was published on 14 March 2021."
        document = source(block + "\n" + notice)
        clues = replace(clues, quotes=(block, notice))
        binding = validate_clues(document, clues, CUTOFF)
        self.assertEqual(binding["citation_context_joins"][0]["join_basis"], "adjacent_exact_source_spans")

    def test_separate_formal_block_and_notice_require_same_identifier(self):
        document, clues = self.formal_fixture()
        bad_notice = clues.quotes[1].replace("test.paper", "other.paper")
        for gap in ("\n", "\nUnrelated text.\n"):
            changed = source(clues.quotes[0] + gap + bad_notice)
            with self.subTest(gap=gap), self.assertRaises(BibliographyError):
                self.locator().resolve(changed, replace(clues, quotes=(clues.quotes[0], bad_notice)))
        self.assertEqual(self.calls, [])

    def test_different_article_links_do_not_join_even_when_adjacent(self):
        document, clues = self.formal_fixture("https://journals.example.org/paper")
        notice = clues.quotes[1].replace("/paper", "/other")
        with self.assertRaises(BibliographyError):
            self.locator().resolve(source(clues.quotes[0] + "\n" + notice), replace(clues, quotes=(clues.quotes[0], notice)))
        self.assertEqual(self.calls, [])

    def test_nonadjacent_unlinked_blocks_cannot_mix_bibliographic_fields(self):
        document, clues = self.formal_fixture()
        block = clues.quotes[0].replace("[https://doi.org/10.5555/test.paper]\n", "")
        notice = "This article was published on 14 March 2021."
        with self.assertRaises(BibliographyError):
            self.locator().resolve(source(block + "\nDifferent source section.\n" + notice), replace(clues, quotes=(block, notice)))
        self.assertEqual(self.calls, [])

    def test_formal_publication_date_cannot_borrow_unrelated_date(self):
        document, clues = self.formal_fixture()
        other_date = "March 15, 2021"
        changed = source(document.content + "\n" + other_date)
        with self.assertRaisesRegex(BibliographyError, "formal_citation_publication_date_not_bound"):
            self.locator().resolve(changed, replace(clues, quotes=(*clues.quotes, other_date), publication_date="2021-03-15"))
        self.assertEqual(self.calls, [])

    def test_formal_block_must_contain_topic_and_not_multiple_dois(self):
        document, clues = self.formal_fixture()
        multiple = clues.quotes[0] + "\nhttps://doi.org/10.5555/other.paper"
        no_title = clues.quotes[0].replace("Meteorite chromium measurements in dated samples\n", "")
        for block in (multiple, no_title):
            topic = "Meteorite chromium measurements in dated samples"
            changed = source(block + "\nOther text.\n" + clues.quotes[1] + "\n" + topic)
            with self.subTest(block=block), self.assertRaises(BibliographyError):
                self.locator().resolve(changed, replace(clues, quotes=(block, clues.quotes[1], topic)))
        self.assertEqual(self.calls, [])

    def test_common_homepage_does_not_join_separate_citations(self):
        document, clues = self.formal_fixture("https://journals.example.org/")
        with self.assertRaises(BibliographyError):
            self.locator().resolve(document, clues)
        self.assertEqual(self.calls, [])


def live(args):
    sources = json.loads(args.live_source.read_text())
    if not isinstance(sources, list):
        sources = [sources]
    matches = [s for s in sources if s.get("url") == args.source_url]
    if len(matches) != 1:
        raise BibliographyError("live_source_url_must_match_one_record")
    clues_data = json.loads(args.clues.read_text())
    clues = CitationClues(**clues_data)
    locator = BibliographicLocator(cutoff=args.cutoff, receipt_dir=args.receipt_dir, max_lookups=1)
    result = locator.resolve(SimpleNamespace(**matches[0]), clues)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    if "--live-source" in sys.argv:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--live-source", type=Path, required=True)
        parser.add_argument("--source-url", required=True)
        parser.add_argument("--clues", type=Path, required=True)
        parser.add_argument("--receipt-dir", type=Path, required=True)
        parser.add_argument("--cutoff", default=CUTOFF)
        live(parser.parse_args())
    else:
        unittest.main()
