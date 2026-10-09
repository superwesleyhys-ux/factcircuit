"""Synthetic contract and failure regressions, not measurements of news accuracy."""

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from newsverify.provenance import (
    Analysis, Fragment, Gap, MaterialVersion, OriginFinding,
    Relation, ReplayTraceProvider, Resolution, Span, Target, TraceConfig,
    VerificationResult, run_provenance,
)
from newsverify.trace_demo import build_demo, run_demo


TARGET = Target("claim", "The company already cut 30% of staff.", "2026-09-04T20:00:00Z")


def material(identifier="a", **changes):
    value = MaterialVersion(identifier, "https://example.org/" + identifier,
        "Discussing next year's budget with a ceiling of 30%.", "2026-09-05T01:00:00Z",
        "2026-08-01T12:00:00Z", "2026-08-01T12:00:00Z", "Synthetic exact-version archive capture.")
    return replace(value, **changes)


def full(value):
    return Span(value.version_id, 0, len(value.content), value.content)


class FunctionDecomposer:
    def __init__(self, function):
        self.function = function

    def decompose(self, target, value, context):
        return self.function(target, value, context)


class FunctionVerifier:
    def __init__(self, function):
        self.function = function

    def verify(self, target, context):
        return self.function(target, context)


class ProvenanceContractTests(unittest.TestCase):
    def test_every_return_is_saved_then_decomposed_before_graph_and_verifier(self):
        report = run_demo()
        operations = report["operations"]
        for observation in report["observations"]:
            identifier = observation["version_id"]
            relevant = [(i, x["action"]) for i, x in enumerate(operations) if x.get("version_id") == identifier]
            actions = [x[1] for x in relevant]
            self.assertLess(actions.index("snapshot_saved"), actions.index("decompose_started"))
            self.assertLess(actions.index("decompose_completed"), actions.index("alignment_checked"))
            self.assertLess(actions.index("alignment_checked"), actions.index("graph_updated"))
        self.assertEqual(4, report["usage"]["documents"])
        self.assertGreaterEqual(report["usage"]["decomposition_calls"], 4)

    def test_original_target_and_qualifiers_survive_revisions(self):
        args = build_demo()
        text = args["target"].text
        report = run_provenance(**args)
        self.assertEqual(text, report["target"]["text"])
        original = next(x for x in report["materials"] if x["version_id"] == "headline:v1")
        self.assertIn("already cut", original["content"])
        fragment = next(x for x in report["fragments"] if x["span"]["version_id"] == "minutes:v1")
        self.assertEqual(("discussing", "next year", "ceiling", "external-services budget"), fragment["qualifiers"])
        for item in report["fragments"]:
            self.assertEqual(text, report["target"]["text"])
            self.assertEqual(args["target"].id, item["parent_id"])

    def test_same_url_has_distinct_immutable_versions(self):
        report = run_demo()
        headlines = [x for x in report["materials"] if x["url"] == "https://news.example/headline"]
        self.assertEqual(2, len(headlines))
        self.assertNotEqual(headlines[0]["content"], headlines[1]["content"])
        with self.assertRaises(FrozenInstanceError):
            material().content = "replacement"

    def test_same_id_different_content_fails_closed(self):
        first = material()
        report = run_provenance(TARGET, ReplayTraceProvider(((first,), (replace(first, content="Changed text."),))))
        self.assertEqual("integrity_error", report["stop_reason"])
        self.assertEqual("unresolved", report["provenance_status"])
        self.assertEqual(first.content, report["materials"][0]["content"])
        self.assertIn("version_id_collision", report["observations"][-1]["reasons"])

    def test_repeat_fetch_time_is_observation_not_new_version(self):
        first = material()
        second = replace(first, retrieved_at="2026-09-06T01:00:00Z")
        report = run_provenance(TARGET, ReplayTraceProvider(((first,), (second,))))
        self.assertEqual(1, len(report["materials"]))
        self.assertEqual(2, len(report["analysis_history"]))
        self.assertTrue(report["observations"][1]["duplicate"])
        self.assertEqual(second.retrieved_at, report["observations"][1]["retrieved_at"])

    def test_missing_original_and_earliest_article_are_not_roots(self):
        value = material(content="According to an anonymous source, a reduction is planned.")
        def decompose(target, value, context):
            return Analysis(relations=(Relation("anonymous", value.version_id, None, "quotes", "declared",
                (full(value),), "Publisher claims an anonymous interview.", "anonymous interview"),))
        report = run_provenance(TARGET, ReplayTraceProvider(((value,),)), FunctionDecomposer(decompose))
        self.assertEqual("partial", report["provenance_status"])
        self.assertEqual([], report["origins"])
        self.assertTrue(report["gaps"])
        self.assertEqual("declared", report["relations"][0]["status"])

    def test_semantic_support_does_not_establish_citation_or_origin(self):
        a, b = material("a"), material("b")
        def decompose(target, value, context):
            if value.version_id == "b":
                return Analysis(relations=(Relation("supports", "b", "a", "supports", "inferred",
                    (full(value),), "Fixture semantic similarity only."),))
            return Analysis()
        report = run_provenance(TARGET, ReplayTraceProvider(((a, b),)), FunctionDecomposer(decompose))
        self.assertEqual("partial", report["provenance_status"])
        self.assertEqual("supports", report["relations"][0]["kind"])
        self.assertEqual([], report["origins"])

    def test_unavailable_upstream_cannot_be_labeled_direct(self):
        def decompose(target, value, context):
            return Analysis(relations=(Relation("bad", value.version_id, None, "cites", "direct",
                (full(value),), "Unseen record.", "missing record"),))
        report = run_provenance(TARGET, ReplayTraceProvider(((material(),),)), FunctionDecomposer(decompose))
        self.assertEqual("decomposer_error", report["stop_reason"])

    def test_old_original_accepted_with_late_retrieval(self):
        value = material(published_at="2010-01-01T00:00:00Z", available_at="2010-01-01T00:00:00Z")
        report = run_provenance(TARGET, ReplayTraceProvider(((value,),)))
        self.assertEqual([value.version_id], report["eligible_version_ids"])
        self.assertFalse(report["observations"][0]["reasons"])

    def test_future_version_decomposed_but_cannot_enter_graph_or_verifier(self):
        future = material("future", available_at="2026-09-05T00:00:00Z")
        old = material("old")
        seen = []
        def verify(target, context):
            seen.append({x["version_id"] for x in context["materials"]})
            return VerificationResult()
        report = run_provenance(TARGET, ReplayTraceProvider(((future, old),)), verifier=FunctionVerifier(verify))
        self.assertEqual(["old"], report["eligible_version_ids"])
        self.assertTrue(all("future" not in values for values in seen))
        self.assertEqual(2, report["usage"]["decomposition_calls"])
        self.assertFalse(report["analysis_history"][0]["accepted"])

    def test_missing_availability_proof_excluded_even_with_early_publication(self):
        for overrides, reason in (({"available_at": None}, "version_availability_unknown"),
                                  ({"availability_basis": None}, "version_availability_unsubstantiated")):
            with self.subTest(reason=reason):
                report = run_provenance(TARGET, ReplayTraceProvider(((material(**overrides),),)))
                self.assertEqual([], report["eligible_version_ids"])
                self.assertIn(reason, report["observations"][0]["reasons"])

    def test_timezone_required_and_offsets_compared_as_instants(self):
        with self.assertRaises(ValueError):
            run_provenance(replace(TARGET, as_of="2026-09-04T20:00:00"), ReplayTraceProvider(()))
        good = material(available_at="2026-09-04T13:00:00-07:00")
        report = run_provenance(TARGET, ReplayTraceProvider(((good,),)))
        self.assertEqual(["a"], report["eligible_version_ids"])
        bad = material(retrieved_at="2026-09-05T01:00:00")
        report = run_provenance(TARGET, ReplayTraceProvider(((bad,),)))
        self.assertEqual("material_error", report["stop_reason"])

    def test_duplicate_cycle_stops_after_redecomposition(self):
        value = material()
        report = run_provenance(TARGET, ReplayTraceProvider(((value,), (value,), (value,))),
                                config=TraceConfig(max_rounds=10))
        self.assertEqual(2, report["usage"]["rounds"])
        self.assertEqual(2, report["usage"]["decomposition_calls"])
        self.assertEqual("no_new_eligible_materials", report["stop_reason"])

    def test_duplicate_redecomposition_with_new_gap_can_retrieve_upstream(self):
        a, b = material("a"), material("b")
        seen = []
        class Provider:
            def search(self, target, tasks, round_number, limit):
                if round_number < 3:
                    yield a
                elif round_number == 3 and any(item.id == "new-upstream" for item in tasks):
                    yield b
        def decompose(target, value, context):
            seen.append(value.version_id)
            if value.version_id == "a" and seen.count("a") == 2:
                return Analysis(gaps=(Gap("new-upstream", "Retrieve the newly recognized report"),))
            return Analysis()
        report = run_provenance(TARGET, Provider(), FunctionDecomposer(decompose))
        self.assertIn("b", report["eligible_version_ids"])
        self.assertEqual(["a", "a", "b"], seen)
        progress = [x for x in report["operations"] if x["action"] == "progress_checked" and x["round"] == 2]
        self.assertTrue(progress[0]["new_structure"])

    def test_cosmetic_notes_cannot_keep_duplicate_loop_running(self):
        counter = []
        def decompose(target, value, context):
            counter.append(1)
            return Analysis(notes="Different notes " + str(len(counter)))
        report = run_provenance(TARGET, ReplayTraceProvider(((material(),),) * 5), FunctionDecomposer(decompose))
        self.assertEqual(2, report["usage"]["rounds"])
        self.assertEqual("no_new_eligible_materials", report["stop_reason"])

    def test_provider_failure_preserves_audit_but_clears_success_status(self):
        class BrokenProvider:
            def search(self, target, tasks, round_number, limit):
                yield material()
                raise RuntimeError("provider lost connection")
        report = run_provenance(TARGET, BrokenProvider())
        self.assertEqual("provider_error", report["stop_reason"])
        self.assertEqual("unresolved", report["provenance_status"])
        self.assertEqual(1, len(report["analysis_history"]))

    def test_decomposer_failure_preserves_raw_material(self):
        def broken(*args):
            raise RuntimeError("parse failed")
        report = run_provenance(TARGET, ReplayTraceProvider(((material(),),)), FunctionDecomposer(broken))
        self.assertEqual("decomposer_error", report["stop_reason"])
        self.assertEqual(1, len(report["materials"]))
        self.assertEqual([], report["eligible_version_ids"])

    def test_verifier_failure_does_not_leave_old_supported_verdict(self):
        calls = []
        def verify(target, context):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("verification failed")
            return VerificationResult("supported", (full(material("a")),), "Fixture annotation.")
        report = run_provenance(TARGET, ReplayTraceProvider(((material("a"),), (material("b"),))),
                                verifier=FunctionVerifier(verify))
        self.assertEqual("verifier_error", report["stop_reason"])
        self.assertEqual("unresolved", report["fact_status"])
        self.assertEqual("supported", report["verification_history"][0]["verdict"])

    def test_verification_followup_returns_through_psi(self):
        report = run_demo()
        operations = report["operations"]
        search = next(x for x in operations if x["action"] == "search" and x["round"] == 3)
        self.assertTrue(any(x["stage"] == "verification" for x in search["tasks"]))
        decomposition = next(x for x in operations if x["action"] == "decompose_completed" and x.get("version_id") == "headline:v2")
        verification = next(x for x in operations if x["action"] == "verification_started" and x["round"] == 3)
        self.assertLess(decomposition["sequence"], verification["sequence"])
        self.assertEqual("contradicted", report["fact_status"])
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertEqual("synthetic_annotated_replay", report["evaluation_mode"])

    def test_reanalysis_updates_old_relations_and_keeps_history(self):
        report = run_demo()
        old = [x for x in report["analysis_history"] if x["version_id"] == "dispatch:v1"]
        self.assertEqual(2, len(old))
        self.assertEqual("declared", old[0]["analysis"]["relations"][0]["status"])
        self.assertEqual("direct", old[1]["analysis"]["relations"][0]["status"])
        self.assertTrue(old[1]["revisit"])

    def test_provider_iterable_is_not_consumed_past_budget(self):
        consumed = []
        class InfiniteProvider:
            def search(self, target, tasks, round_number, limit):
                for i in range(1000000):
                    consumed.append(i)
                    yield material(str(i))
        report = run_provenance(TARGET, InfiniteProvider(), config=TraceConfig(max_documents=2))
        self.assertEqual([0, 1], consumed)
        self.assertEqual(2, report["usage"]["documents"])
        self.assertEqual("document_budget", report["stop_reason"])

    def test_revisit_budget_leaves_explicit_gap(self):
        args = build_demo()
        args["config"] = TraceConfig(max_rounds=4, max_decomposition_calls=2)
        report = run_provenance(**args)
        self.assertEqual(2, report["usage"]["decomposition_calls"])
        self.assertEqual("decomposition_budget", report["stop_reason"])
        self.assertTrue(any(x["id"].startswith("revisit:") for x in report["gaps"]))

    def test_invalid_citation_and_plugin_types_fail_closed(self):
        for function in (
            lambda t, m, c: {"fragments": []},
            lambda t, m, c: Analysis(fragments=(Fragment("bad", "invented", Span(m.version_id, 0, 8, "invented"), t.id),)),
        ):
            with self.subTest(function=function):
                report = run_provenance(TARGET, ReplayTraceProvider(((material(),),)), FunctionDecomposer(function))
                self.assertEqual("decomposer_error", report["stop_reason"])
                self.assertEqual("unresolved", report["provenance_status"])

    def test_resolving_gap_without_explicit_original_does_not_create_root(self):
        def decompose(target, value, context):
            return Analysis(resolutions=(Resolution("origin:" + target.id, (full(value),), "Only an early article was found."),))
        report = run_provenance(TARGET, ReplayTraceProvider(((material(),),)), FunctionDecomposer(decompose))
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_support_path_cannot_complete_source_lineage(self):
        a, b = material("a"), material("b")
        target = replace(TARGET, source_version_id="a")
        def decompose(target, value, context):
            if value.version_id == "a":
                return Analysis()
            return Analysis(relations=(Relation("semantic-only", "a", "b", "supports", "direct",
                (full(a), full(b)), "Fixture semantic support, no propagation evidence."),),
                origins=(OriginFinding(target.id, "b", (full(b),), "original_record", "Annotated original record."),),
                resolutions=(Resolution("origin:" + target.id, (full(b),), "Located record."),))
        report = run_provenance(target, ReplayTraceProvider(((a, b),)), FunctionDecomposer(decompose))
        self.assertNotEqual("original_material_located", report["provenance_status"])
        self.assertTrue(any(x["id"] == "lineage:" + target.id for x in report["gaps"]))

    def test_report_is_json_serializable(self):
        serialized = json.dumps(run_demo(), allow_nan=False)
        self.assertIn("analysis_history", json.loads(serialized))


if __name__ == "__main__":
    unittest.main()
