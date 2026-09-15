"""Contract tests with explicit fixtures; these are not model-accuracy evidence."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest

from newsverify.double_loop import DoubleLoopDecomposer, run_double_loop_trace
from newsverify.provenance import Gap, MaterialVersion, Target


def material(id, content, available="2026-01-01T00:00:00Z"):
    return {"version_id": id, "url": "https://fixture.invalid/" + id,
            "content": content, "issuer": "synthetic contract fixture",
            "published_at": available, "available_at": available,
            "retrieved_at": "2026-01-03T00:00:00Z",
            "availability_basis": "Explicit synthetic fixture assertion."}


D1 = material("notice", "The notice reports a measurement of 30 units. Source: https://fixture.invalid/record")
D2 = material("record", "Original measurement record: the result was 30 units.")


def quote(m):
    return {"version_id": m["version_id"], "quote": m["content"]}


def analysis(m, **updates):
    result = {"fragments": [{"id": "claim", "text": m["content"], "quote": m["content"], "qualifiers": []}],
              "relations": [], "gaps": [], "resolutions": [], "origins": [],
              "revisit_versions": [], "notes": "Synthetic test response."}
    result.update(updates)
    return result


def edge(direct):
    return {"id": "source", "from_version": "notice", "to_version": "record" if direct else None,
            "kind": "cites", "status": "direct" if direct else "declared",
            "basis": [quote(D1)], "rationale": "The notice explicitly links the record.",
            "upstream_locator": D2["url"]}


def resolution(id, m=D2):
    return {"gap_id": id, "basis": [quote(m)], "rationale": "The quoted record settles this fixture question."}


def verdict(value="unresolved", **updates):
    result = {"verdict": value, "basis": [], "rationale": "Awaiting the primary measurement record.",
              "gaps": [], "resolutions": []}
    result.update(updates)
    return result


def script():
    return [
        ("decompose", analysis(D1, relations=[edge(False)], gaps=[
            {"id": "need-primary", "question": "Retrieve the explicitly linked primary record.", "stage": "provenance"}])),
        ("verify", verdict(gaps=[{"id": "verification:measurement", "question": "Find the primary measurement evidence.", "stage": "verification"}])),
        ("select", {"version_id": "record", "rationale": "The linked record answers the open measurement question."}),
        ("decompose", analysis(D2, resolutions=[resolution("need-primary"), resolution("origin:target")],
            origins=[{"version_id": "record", "basis": [quote(D2)], "material_kind": "original_record", "rationale": "This fixture is the producing measurement record."}],
            revisit_versions=["notice"])),
        ("decompose", analysis(D1, relations=[edge(True)])),
        ("verify", verdict("supported", basis=[quote(D2)], rationale="The original record confirms 30 units.",
            resolutions=[resolution("verification:measurement")])),
    ]


class ScriptedTransport:
    kind = "local"
    model = "gpt-6-astra"
    reasoning_effort = "medium"

    def __init__(self, steps):
        self.steps = deepcopy(steps)
        self.calls = []
        self.inputs = []

    def generate(self, stage, instructions, packet, schema):
        if not self.steps:
            raise AssertionError("Unexpected extra model call")
        expected, response = self.steps.pop(0)
        if stage != expected:
            raise AssertionError(f"Expected {expected}, received {stage}")
        self.inputs.append({"stage": stage, "instructions": instructions, "packet": deepcopy(packet)})
        self.calls.append({"stage": stage, "success": True, "status": "completed", "wall_seconds": 0.01,
                           "usage": {"input_tokens": 10, "output_tokens": 2}})
        return deepcopy(response)


def payload(extra=()):
    return {"target": {"id": "target", "text": "The measured result was 30 units.",
                       "as_of": "2026-01-03T00:00:00Z", "source_version_id": "notice"},
            "materials": deepcopy([D1, D2, *extra]), "initial_version_ids": ["notice"],
            "config": {"max_rounds": 4, "max_documents": 4, "max_decomposition_calls": 8,
                       "reanalyze_existing_versions": True}}


class DoubleLoopTests(unittest.TestCase):
    def run_script(self, steps=None, data=None, **kwargs):
        transport = ScriptedTransport(script() if steps is None else steps)
        report = run_double_loop_trace(payload() if data is None else data, transport=transport, **kwargs)
        return report, transport

    def test_both_feedback_loops_revise_history_and_resolve_with_evidence(self):
        report, transport = self.run_script()
        self.assertEqual([], report["errors"])
        self.assertEqual("supported", report["fact_status"])
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertEqual("complete", report["stop_reason"])
        self.assertEqual([], transport.steps)
        self.assertEqual(["decompose", "verify", "select", "decompose", "decompose", "verify"], [c["stage"] for c in report["execution"]["model_calls"]])
        requests = report["execution"]["provider_requests"]
        self.assertTrue(any(g["stage"] == "verification" for g in requests[1]["tasks"]))
        self.assertEqual("record", requests[1]["selected_version_id"])
        revisions = [h for h in report["analysis_history"] if h["version_id"] == "notice"]
        self.assertEqual(2, len(revisions))
        self.assertFalse(revisions[0]["revisit"])
        self.assertTrue(revisions[1]["revisit"])
        self.assertEqual("declared", revisions[0]["analysis"]["relations"][0]["status"])
        self.assertEqual("direct", revisions[1]["analysis"]["relations"][0]["status"])
        round2 = [o for o in report["operations"] if o["round"] == 2]
        decomposition = next(o for o in round2 if o["action"] == "decompose_completed" and o["version_id"] == "record")
        verification = next(o for o in round2 if o["action"] == "verification_started")
        self.assertLess(decomposition["sequence"], verification["sequence"])

    def test_omitted_own_open_gap_blocks_false_provenance_completion(self):
        steps = script()
        steps[0][1]["gaps"].append({"id": "confirmation-source",
            "question": "Retrieve the original provenance confirmation record.", "stage": "provenance"})
        report, _ = self.run_script(steps)
        self.assertEqual([], report["errors"])
        self.assertNotEqual("original_material_located", report["provenance_status"])
        self.assertTrue(any(g["id"] == "confirmation-source" for g in report["gaps"]))
        revisited = [h["analysis"] for h in report["analysis_history"] if h["version_id"] == "notice"][-1]
        self.assertTrue(any(g["id"] == "confirmation-source" for g in revisited["gaps"]))
        self.assertIn("Carried forward", revisited["notes"])

    def test_carried_gap_closes_only_after_fetched_evidenced_resolution(self):
        confirmation = material("confirmation", "Original provenance confirmation: record is the producing measurement record.")
        steps = script()
        steps[0][1]["gaps"].append({"id": "confirmation-source",
            "question": "Retrieve the original provenance confirmation record.", "stage": "provenance"})
        steps.extend([
            ("select", {"version_id": "confirmation", "rationale": "Read the missing confirmation record."}),
            ("decompose", analysis(confirmation, resolutions=[resolution("confirmation-source", confirmation)])),
            deepcopy(script()[5]),
        ])
        report, transport = self.run_script(steps, payload(extra=[confirmation]))
        self.assertEqual([], report["errors"])
        self.assertEqual([], transport.steps)
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertFalse(any(g["id"] == "confirmation-source" for g in report["gaps"]))
        self.assertIn("confirmation", report["eligible_version_ids"])

    def test_resolved_prior_gap_is_not_carried_back_into_reanalysis(self):
        report, _ = self.run_script()
        self.assertEqual([], report["errors"])
        revisited = [h["analysis"] for h in report["analysis_history"] if h["version_id"] == "notice"][-1]
        self.assertFalse(any(g["id"] == "need-primary" for g in revisited["gaps"]))
        self.assertEqual("original_material_located", report["provenance_status"])

    def test_reanalysis_does_not_steal_other_material_open_gap(self):
        steps = script()
        steps[3][1]["gaps"].append({"id": "record-owned",
            "question": "Find the producing record's provenance appendix.", "stage": "provenance"})
        report, _ = self.run_script(steps)
        self.assertEqual([], report["errors"])
        revisited = [h["analysis"] for h in report["analysis_history"] if h["version_id"] == "notice"][-1]
        self.assertFalse(any(g["id"] == "record-owned" for g in revisited["gaps"]))
        self.assertTrue(any(g["id"] == "record-owned" for g in report["gaps"]))
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_invalid_resolution_cannot_remove_omitted_open_gap(self):
        steps = script()
        steps[0][1]["gaps"].append({"id": "confirmation-source",
            "question": "Retrieve the original provenance confirmation record.", "stage": "provenance"})
        bad = resolution("confirmation-source")
        bad["basis"][0]["quote"] = "This quotation does not exist."
        steps[4][1]["resolutions"] = [bad]
        report, _ = self.run_script(steps)
        self.assertTrue(report["errors"])
        self.assertTrue(any(g["id"] == "confirmation-source" for g in report["gaps"]))
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_invalid_reanalysis_fragment_retains_prior_gap_obligations(self):
        steps = script()
        steps[0][1]["gaps"].append({"id": "confirmation-source",
            "question": "Retrieve the original provenance confirmation record.", "stage": "provenance"})
        steps[4][1]["fragments"][0]["quote"] = "This quotation does not exist."
        report, _ = self.run_script(steps)
        self.assertTrue(report["errors"])
        self.assertTrue(any(g["id"] == "confirmation-source" for g in report["gaps"]))
        self.assertEqual(1, len([h for h in report["analysis_history"] if h["version_id"] == "notice"]))
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_carry_preserves_full_prior_gap_metadata(self):
        gap = Gap("owned", "Inspect the provenance record.", "provenance",
                  blocking=False, decision_impact="Origin identity context.", locator=D2["url"])
        context = {"current_material_eligible": True, "materials": [D1],
            "analyses": {"notice": {"gaps": [asdict(gap)], "resolutions": []}},
            "gaps": [asdict(gap)]}
        transport = ScriptedTransport([("decompose", analysis(D1))])
        result = DoubleLoopDecomposer(transport).decompose(
            Target(**payload()["target"]), MaterialVersion(**D1), context)
        self.assertEqual((gap,), result.gaps)

    def test_revisit_cannot_redeclare_origin_owned_by_other_material(self):
        steps = script()
        # The revisit of notice proposes record's origin again; only record's
        # own analysis may emit that origin.
        steps[4][1]["origins"] = [{"version_id": "record", "basis": [quote(D2)],
            "material_kind": "original_record", "rationale": "redeclared"}]
        report, _ = self.run_script(steps)
        self.assertEqual([], report["errors"])
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertTrue(any(o["version_id"] == "record" for h in report["analysis_history"]
                            for o in h["analysis"].get("origins", [])))
        self.assertTrue(any("Ignored non-current origin proposals" in h["analysis"].get("notes", "")
                            for h in report["analysis_history"]))

    def test_only_noncurrent_origin_proposal_does_not_resolve_gap(self):
        steps = script()
        steps[3][1]["origins"] = []
        steps[4][1]["origins"] = [{"version_id": "record", "basis": [quote(D2)],
            "material_kind": "original_record", "rationale": "non-current"}]
        steps[3][1]["resolutions"] = []
        report, _ = self.run_script(steps)
        self.assertNotEqual("original_material_located", report["provenance_status"])
        self.assertEqual([], [o for h in report["analysis_history"] for o in h["analysis"].get("origins", [])
                              if h["version_id"] == "notice"])
        self.assertTrue(any(g["id"] == "origin:target" for g in report["gaps"]))

    def test_invalid_current_origin_basis_remains_unresolved(self):
        steps = script()
        steps[3][1]["origins"] = [{"version_id": "record", "basis": [quote(D1)],
            "material_kind": "original_record", "rationale": "wrong version span"}]
        report, _ = self.run_script(steps)
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_duplicate_system_origin_gap_is_ignored_and_remains_owned(self):
        steps = script()
        steps[0][1]["gaps"].append({"id": "origin:target",
            "question": "A paraphrased origin question", "stage": "provenance"})
        report, _ = self.run_script(steps)
        self.assertEqual([], report["errors"])
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertTrue(any("Ignored duplicate system-owned" in h["analysis"].get("notes", "")
                            for h in report["analysis_history"]))

    def test_duplicate_origin_without_basis_cannot_resolve_provenance(self):
        steps = script()
        steps[0][1]["gaps"].append({"id": "origin:target", "question": "Paraphrase", "stage": "provenance"})
        steps[3][1]["resolutions"] = []
        steps[3][1]["origins"] = []
        report, _ = self.run_script(steps)
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_current_material_gap_is_not_dropped_as_reserved(self):
        steps = script()
        steps[0][1]["gaps"].append({"id": "need-local", "question": "A local unresolved obligation", "stage": "provenance"})
        report, _ = self.run_script(steps)
        # The ordinary model-owned gap remains in the history even after a revisit.
        self.assertTrue(any(g["id"] == "need-local" for h in report["analysis_history"]
                            for g in h["analysis"].get("gaps", [])))

    def test_decomposition_preserves_every_source_without_resending_current_full_text(self):
        report, transport = self.run_script()
        self.assertEqual([], report["errors"])
        originals = {m["version_id"]: m for m in (D1, D2)}
        for request in transport.inputs:
            if request["stage"] != "decompose":
                continue
            packet = request["packet"]
            current = packet["material"]
            self.assertEqual(originals[current["version_id"]], current)
            others = packet["context"]["materials"]
            self.assertNotIn(current["version_id"], [m["version_id"] for m in others])
            for item in others:
                self.assertEqual(originals[item["version_id"]], item)
        revisit = transport.inputs[4]["packet"]
        self.assertEqual([D2], revisit["context"]["materials"])
        self.assertIn("record", revisit["context"]["analyses"])
        final = transport.inputs[-1]["packet"]
        self.assertEqual([D1, D2], final["context"]["materials"])

    def test_single_same_url_version_is_inspected_without_a_selection_model_call(self):
        data = payload()
        data["materials"][1]["url"] = data["materials"][0]["url"]
        steps = script()
        del steps[2]
        report, transport = self.run_script(steps, data)
        self.assertEqual([], report["errors"])
        self.assertEqual("supported", report["fact_status"])
        self.assertNotIn("select", [c["stage"] for c in transport.calls])
        self.assertEqual("same_source_version", report["execution"]["provider_requests"][1]["reason"])
        self.assertEqual(["notice", "record"], report["eligible_version_ids"])

    def test_missing_primary_stays_unresolved_without_inventing_retrieval(self):
        data = payload(); data["materials"] = [deepcopy(D1)]
        steps = script()[:2] + [("verify", verdict())]
        report, _ = self.run_script(steps, data)
        self.assertEqual([], report["errors"])
        self.assertEqual("unresolved", report["fact_status"])
        self.assertEqual("empty_results", report["stop_reason"])
        self.assertTrue(any(g["stage"] == "verification" for g in report["gaps"]))
        self.assertEqual("pool_exhausted", report["execution"]["provider_requests"][-1]["reason"])

    def test_call_cap_counts_selection_and_sends_no_over_budget_request(self):
        report, transport = self.run_script(max_model_calls=3)
        self.assertEqual(3, len(transport.calls))
        self.assertEqual(["decompose", "verify", "select"], [c["stage"] for c in transport.calls])
        self.assertEqual("ModelCallBudgetError", report["errors"][0]["type"])
        self.assertEqual("unresolved", report["fact_status"])
        self.assertEqual("decompose", report["execution"]["blocked_calls"][0]["stage"])

    def test_future_content_never_reaches_selection_or_semantic_packets(self):
        future = material("future", "DO_NOT_LEAK_FUTURE_CONTENT", "2026-01-05T00:00:00Z")
        future["retrieved_at"] = "2026-01-05T00:00:00Z"
        report, transport = self.run_script(data=payload([future]))
        self.assertEqual([], report["errors"])
        self.assertIn("future", report["execution"]["pool_exclusions"])
        self.assertNotIn("DO_NOT_LEAK_FUTURE_CONTENT", json.dumps(transport.inputs))
        catalog = transport.inputs[2]["packet"]["catalog"]
        self.assertEqual(["record"], [c["version_id"] for c in catalog])

    def test_unknown_selection_is_a_preserved_provider_error(self):
        steps = script(); steps[2][1]["version_id"] = "invented"
        report, transport = self.run_script(steps)
        self.assertTrue(report["errors"])
        self.assertEqual(3, len(transport.calls))
        self.assertEqual(["notice"], report["eligible_version_ids"])
        self.assertEqual("invented", report["execution"]["model_io"][-1]["response"]["version_id"])

    def test_unknown_revisit_does_not_rewrite_an_old_analysis(self):
        steps = script(); steps[3][1]["revisit_versions"] = ["invented"]
        report, _ = self.run_script(steps)
        self.assertEqual("decomposer", report["errors"][0]["stage"])
        self.assertEqual(1, len(report["analysis_history"]))
        self.assertEqual("unresolved", report["fact_status"])

    def test_ambiguous_quote_rejected_and_raw_output_retained(self):
        data = payload(); data["materials"][0]["content"] = "same passage; same passage"
        steps = script(); steps[0][1]["fragments"][0]["quote"] = "same passage"
        report, _ = self.run_script(steps, data)
        self.assertEqual("decomposer", report["errors"][0]["stage"])
        self.assertEqual("same passage", report["execution"]["model_io"][0]["response"]["fragments"][0]["quote"])

    def test_verifier_cannot_cite_an_unretrieved_pool_version(self):
        steps = script(); steps[1][1]["basis"] = [quote(D2)]
        report, _ = self.run_script(steps)
        self.assertEqual("verifier", report["errors"][0]["stage"])
        self.assertEqual(1, len(report["materials"]))

    def test_verifier_cannot_resolve_provenance_gap(self):
        steps = script(); steps[1][1]["resolutions"] = [resolution("origin:target", D1)]
        report, _ = self.run_script(steps)
        self.assertEqual("verifier", report["errors"][0]["stage"])
        self.assertNotEqual("original_material_located", report["provenance_status"])

    def test_context_keeps_one_analysis_copy_instead_of_flat_duplicate(self):
        report, transport = self.run_script()
        self.assertEqual([], report["errors"])
        context = transport.inputs[-1]["packet"]["context"]
        self.assertIn("analyses", context)
        self.assertIn("materials", context)
        self.assertNotIn("fragments", context)
        self.assertNotIn("origins", context)

    def test_transport_route_mismatch_is_not_a_fallback(self):
        transport = ScriptedTransport(script())
        with self.assertRaisesRegex(ValueError, "kind must match"):
            run_double_loop_trace(payload(), tunnel="api", transport=transport)
        self.assertEqual([], transport.calls)

    def test_revisit_can_reaffirm_a_resolution_known_from_another_analysis(self):
        steps = script()
        steps[4][1]["resolutions"] = [resolution("origin:target")]
        report, transport = self.run_script(steps)
        self.assertEqual([], report["errors"])
        self.assertEqual("supported", report["fact_status"])
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertEqual(6, len(transport.calls))

    def test_verifier_can_reaffirm_its_gap_after_decomposer_resolves_it(self):
        steps = script()
        steps[3][1]["resolutions"].append(resolution("verification:measurement"))
        report, transport = self.run_script(steps)
        self.assertEqual([], report["errors"])
        self.assertEqual("supported", report["fact_status"])
        self.assertEqual([], report["gaps"])
        self.assertEqual(6, len(transport.calls))


if __name__ == "__main__":
    unittest.main()
