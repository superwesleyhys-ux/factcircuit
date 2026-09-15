"""Synthetic contracts for one analysis per immutable source, not model accuracy."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import unittest

from newsverify.double_loop import DoubleLoopDecomposer, DoubleLoopVerifier, run_double_loop_trace
from newsverify.provenance import MaterialVersion, ReplayTraceProvider, Target, TraceConfig, run_provenance
from tests.test_double_loop import (
    D1, D2, ScriptedTransport, analysis, edge, material, payload, quote,
    resolution, script, verdict,
)


def incremental_payload():
    data = payload()
    data["config"].pop("reanalyze_existing_versions")
    return data


def incremental_script():
    steps = script()
    steps[3][1]["relations"] = [edge(True)]
    del steps[4]  # The new record supplies the edge; the old notice stays immutable.
    return steps


class IncrementalDoubleLoopTests(unittest.TestCase):
    def run_steps(self, steps, data=None):
        transport = ScriptedTransport(steps)
        report = run_double_loop_trace(data or incremental_payload(), transport=transport)
        self.assertEqual([], report["errors"])
        self.assertEqual([], transport.steps)
        return report, transport

    def test_new_source_integrates_lineage_and_resolutions_without_old_reanalysis(self):
        data = incremental_payload()
        marker = "UNRETAINED OLD BODY DETAIL: 8ee7b406."
        data["materials"][0]["content"] += "\n" + marker
        report, transport = self.run_steps(incremental_script(), data)
        self.assertEqual("original_material_located", report["provenance_status"])
        self.assertEqual("supported", report["fact_status"])
        self.assertEqual(Counter(notice=1, record=1), Counter(
            h["version_id"] for h in report["analysis_history"]))
        self.assertEqual(2, report["usage"]["decomposition_calls"])
        self.assertEqual("declared", report["analyses"]["notice"]["relations"][0]["status"])
        self.assertEqual("direct", report["analyses"]["record"]["relations"][0]["status"])
        self.assertEqual({"need-primary", "origin:target", "verification:measurement"},
                         {r["gap_id"] for r in report["resolutions"]})
        current = [x["packet"] for x in transport.inputs if x["stage"] == "decompose"][-1]
        self.assertEqual([], current["context"]["materials"])
        self.assertEqual("notice", current["context"]["prior_materials"][0]["version_id"])
        self.assertNotIn("content", current["context"]["prior_materials"][0])
        self.assertNotIn(marker, json.dumps(current))
        self.assertIn(D1["content"], json.dumps(current))  # Its retained exact evidence remains.
        checks = [x["packet"]["context"] for x in transport.inputs if x["stage"] == "verify"]
        self.assertEqual(["notice"], [m["version_id"] for m in checks[0]["materials"]])
        self.assertEqual(["record"], [m["version_id"] for m in checks[1]["materials"]])
        self.assertEqual(["notice"], checks[1]["verified_version_ids"])
        self.assertNotIn(marker, json.dumps(checks[1]))
        self.assertTrue(checks[1]["verification_history"])
        self.assertTrue(any(o["action"] == "reanalysis_skipped" for o in report["operations"]))

    def test_new_source_cannot_automatically_promote_old_declared_edge(self):
        steps = incremental_script()
        steps[3][1]["relations"] = []
        report, _ = self.run_steps(steps)
        self.assertNotEqual("original_material_located", report["provenance_status"])
        self.assertTrue(any(g["id"] == "lineage:target" for g in report["gaps"]))
        self.assertTrue(all(r["status"] == "declared" for r in report["relations"]))
        self.assertEqual(2, len(report["analysis_history"]))

    def test_unresolved_old_gap_survives_new_source_without_replacement(self):
        steps = incremental_script()
        gap = {"id": "unobtained-confirmation", "question": "Obtain the confirmation record.",
               "stage": "provenance"}
        steps[0][1]["gaps"].append(gap)
        report, _ = self.run_steps(steps)
        self.assertNotEqual("original_material_located", report["provenance_status"])
        self.assertIn(gap["id"], [g["id"] for g in report["gaps"]])
        self.assertEqual(1, sum(h["version_id"] == "notice" for h in report["analysis_history"]))

    def test_same_url_new_content_or_availability_is_still_reviewed(self):
        for change in ({"content": "A revised edition records 31 units."},
                       {"available_at": "2026-01-02T00:00:00Z"}):
            with self.subTest(change=change):
                newer = deepcopy(D1)
                newer.update(version_id="new-edition", **change)
                data = incremental_payload()
                data["materials"] = [deepcopy(D1), newer]
                steps = [("decompose", analysis(D1)), ("verify", verdict()),
                         ("decompose", analysis(newer)), ("verify", verdict())]
                report, transport = self.run_steps(steps, data)
                self.assertEqual(["notice", "new-edition"],
                                 [h["version_id"] for h in report["analysis_history"]])
                self.assertEqual({}, report["execution"]["pool_aliases_not_admitted"])
                self.assertEqual(newer["content"], transport.inputs[2]["packet"]["material"]["content"])

    def test_alias_is_not_selected_or_forged_as_an_admitted_version(self):
        alias = {**D1, "version_id": "alias", "retrieved_at": "2026-01-03T01:00:00Z"}
        future = {**D1, "version_id": "future", "available_at": "2027-01-01T00:00:00Z"}
        data = incremental_payload()
        data["materials"] = [alias, future, deepcopy(D1)]  # Initial ID wins regardless of order.
        report, _ = self.run_steps([("decompose", analysis(D1)), ("verify", verdict())], data)
        self.assertEqual({"alias": "notice"}, report["execution"]["pool_aliases_not_admitted"])
        self.assertIn("future", report["execution"]["pool_exclusions"])
        self.assertEqual(["notice"], [m["version_id"] for m in report["materials"]])
        self.assertEqual(1, report["usage"]["decomposition_calls"])

    def test_duplicate_pool_version_id_fails_before_any_model_call(self):
        data = incremental_payload()
        data["materials"].append({**D1, "content": "Changed bytes under the same version ID."})
        transport = ScriptedTransport([])
        with self.assertRaisesRegex(ValueError, "version IDs must be unique"):
            run_double_loop_trace(data, transport=transport)
        self.assertEqual([], transport.inputs)

    def test_policy_must_be_a_boolean_and_generic_default_is_legacy(self):
        self.assertTrue(TraceConfig().reanalyze_existing_versions)
        data = incremental_payload()
        data["config"]["reanalyze_existing_versions"] = 0
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            run_double_loop_trace(data, transport=ScriptedTransport([]))


class IncrementalReturnIntegrityTests(unittest.TestCase):
    def fixture(self):
        first = material("notice", "The original observation records 30 units.")
        initial = analysis(first, gaps=[{"id": "still-open", "question": "Obtain another record.",
                                        "stage": "provenance"}],
            origins=[{"version_id": "notice", "basis": [quote(first)],
                      "material_kind": "original_observation", "rationale": "Synthetic original."}],
            resolutions=[resolution("origin:target", first)])
        check = verdict(gaps=[{"id": "verify-open", "question": "Obtain corroborating measurements.",
                               "stage": "verification"}])
        transport = ScriptedTransport([("decompose", initial), ("verify", check)])
        target = Target("target", "The result was 30 units.", "2026-01-03T00:00:00Z",
                        source_version_id="notice")
        return first, target, transport

    def run_returns(self, first, target, transport, later):
        return run_provenance(target, ReplayTraceProvider(((MaterialVersion(**first),), (later,))),
            DoubleLoopDecomposer(transport, incremental=True),
            DoubleLoopVerifier(transport, incremental=True),
            TraceConfig(max_rounds=4, reanalyze_existing_versions=False))

    def test_exact_duplicate_cannot_close_or_reopen_gaps_or_repeat_verification(self):
        first, target, transport = self.fixture()
        later = replace(MaterialVersion(**first), retrieved_at="2026-01-03T01:00:00Z")
        report = self.run_returns(first, target, transport, later)
        self.assertEqual([], report["errors"])
        self.assertEqual(["decompose", "verify"], [x["stage"] for x in transport.inputs])
        self.assertEqual(1, len(report["analysis_history"]))
        self.assertEqual(1, len(report["verification_history"]))
        self.assertEqual({"still-open", "verify-open"}, {g["id"] for g in report["gaps"]})
        self.assertIn("origin:target", [g["gap_id"] for g in report["resolutions"]])
        self.assertTrue(any(o["action"] == "decomposition_reused" for o in report["operations"]))

    def test_collision_precedes_cached_reuse_for_content_or_eligibility_changes(self):
        for change in ({"content": "Different immutable bytes."},
                       {"available_at": "2027-01-01T00:00:00Z"}):
            with self.subTest(change=change):
                first, target, transport = self.fixture()
                later = replace(MaterialVersion(**first), **change)
                report = self.run_returns(first, target, transport, later)
                self.assertEqual("integrity_error", report["stop_reason"])
                self.assertIn("version_id_collision", report["observations"][-1]["reasons"])
                self.assertEqual(2, len(transport.inputs))
                self.assertFalse(any(o["action"] == "decomposition_reused" for o in report["operations"]))

    def test_new_ineligible_version_never_enters_incremental_model_context(self):
        first, target, transport = self.fixture()
        later = replace(MaterialVersion(**first), version_id="future", content="FUTURE DATA",
                        available_at="2027-01-01T00:00:00Z")
        report = self.run_returns(first, target, transport, later)
        self.assertEqual([], report["errors"])
        self.assertEqual(["notice"], report["eligible_version_ids"])
        self.assertEqual(2, len(transport.inputs))
        self.assertNotIn("FUTURE DATA", json.dumps(transport.inputs))
        self.assertFalse(report["analysis_history"][-1]["accepted"])


if __name__ == "__main__":
    unittest.main()
