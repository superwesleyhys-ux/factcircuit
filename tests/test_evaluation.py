import copy
import json
from pathlib import Path
import unittest

from newsverify.evaluation import evaluate, ratio

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.gold = json.loads((EXAMPLES / "evaluation_gold.json").read_text())
        self.predictions = json.loads((EXAMPLES / "evaluation_predictions.json").read_text())

    def test_all_seven_metrics_against_hand_counted_fixture(self):
        report = evaluate(self.gold, self.predictions)
        expected = {"VP": .5, "FR": .5, "TR": .5, "SR": 2/3,
                    "EN": 2/3, "CA": .65625, "HFAR": .5}
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertAlmostEqual(report["metrics"][name]["value"], value)
        self.assertAlmostEqual(report["metrics"]["CA"]["brier_multiclass"], .6875)
        self.assertAlmostEqual(report["diagnostics"]["ECE10"]["value"], .4375)
        self.assertEqual(report["diagnostics"]["unknown_source_false_promotion"]["value"], 1)
        self.assertEqual(report["diagnostics"]["duplicate_pair_f1"]["value"], 1)
        # Independently derived product for this particular handwritten fixture.
        expected_score = 25 * .5 ** .6 * (2/3) ** .3 * .65625 ** .1
        self.assertAlmostEqual(report["aggregate"]["value"], expected_score)
        self.assertFalse(report["aggregate"]["official_benchmark"])

    def test_abstain_all_cannot_score_as_perfect(self):
        for output in self.predictions["cases"]:
            output["decision"] = "unverifiable"
        report = evaluate(self.gold, self.predictions)
        self.assertEqual(report["metrics"]["FR"]["value"], 1)
        self.assertEqual(report["metrics"]["TR"]["value"], 0)
        self.assertIsNone(report["metrics"]["VP"]["value"])
        self.assertIsNone(report["aggregate"]["value"])

    def test_missing_case_cannot_shrink_denominator(self):
        self.predictions["cases"].pop()
        with self.assertRaisesRegex(ValueError, "fixed gold case set"):
            evaluate(self.gold, self.predictions)

    def test_extra_or_duplicate_case_rejected(self):
        for identifier in ("t1", "new-target"):
            with self.subTest(identifier=identifier):
                payload = copy.deepcopy(self.predictions)
                extra = copy.deepcopy(payload["cases"][0])
                extra["id"] = identifier
                payload["cases"].append(extra)
                with self.assertRaises(ValueError):
                    evaluate(self.gold, payload)

    def test_original_root_without_path_does_not_count(self):
        self.predictions["cases"][0]["source"]["edges"] = []
        report = evaluate(self.gold, self.predictions)
        self.assertAlmostEqual(report["metrics"]["SR"]["value"], 1/3)

    def test_extra_invented_edge_invalidates_source_claim(self):
        self.predictions["cases"][0]["source"]["edges"].append(
            {"from": "t1", "to": "other", "relation": "cites"})
        self.assertFalse(evaluate(self.gold, self.predictions)["cases"][0]["source_correct"])

    def test_support_relationship_cannot_substitute_for_provenance(self):
        self.predictions["cases"][0]["source"]["edges"][0]["relation"] = "supports"
        with self.assertRaisesRegex(ValueError, "unsupported provenance relation"):
            evaluate(self.gold, self.predictions)

    def test_unjudged_evidence_requires_adjudication(self):
        self.predictions["cases"][0]["evidence"][0]["id"] = "not-in-gold"
        with self.assertRaisesRegex(ValueError, "unjudged evidence"):
            evaluate(self.gold, self.predictions)

    def test_duplicate_items_cannot_inflate_metric(self):
        self.predictions["cases"][0]["evidence"].append(
            copy.deepcopy(self.predictions["cases"][0]["evidence"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate(self.gold, self.predictions)

    def test_unknown_and_earliest_sources_not_promoted_to_original(self):
        output = self.predictions["cases"][3]
        output["source"] = {"status": "earliest_accessible", "roots": [], "edges": []}
        report = evaluate(self.gold, self.predictions)
        self.assertEqual(report["diagnostics"]["unknown_source_false_promotion"]["value"], 0)

    def test_same_source_pair_missed_is_penalized(self):
        self.predictions["cases"][0]["evidence"][1]["origin"] = "separate"
        self.assertEqual(evaluate(self.gold, self.predictions)["diagnostics"]["duplicate_pair_f1"]["value"], 0)

    def test_distinct_origins_falsely_joined_is_penalized(self):
        self.predictions["cases"][0]["evidence"][2]["origin"] = "group-x"
        pair = evaluate(self.gold, self.predictions)["diagnostics"]["duplicate_pair_f1"]
        self.assertEqual(pair["fp"], 2)
        self.assertEqual(pair["value"], .5)

    def test_no_probabilities_means_no_ca_or_aggregate(self):
        del self.predictions["cases"][0]["probabilities"]
        report = evaluate(self.gold, self.predictions)
        self.assertIsNone(report["metrics"]["CA"]["value"])
        self.assertIsNone(report["aggregate"]["value"])

    def test_invalid_probability_values_fail_closed(self):
        for value in (float("nan"), float("inf"), -.1, 1.1, True, "1"):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.predictions)
                payload["cases"][0]["probabilities"]["true"] = value
                with self.assertRaises(ValueError):
                    evaluate(self.gold, payload)

    def test_probability_sum_and_label_keys(self):
        for distribution in ({"true": 1}, dict.fromkeys(("true", "false", "disputed", "unverifiable"), .3)):
            self.predictions["cases"][0]["probabilities"] = distribution
            with self.assertRaises(ValueError):
                evaluate(self.gold, self.predictions)

    def test_cutoff_mismatch_prevents_hindsight_scoring(self):
        self.predictions["cases"][0]["as_of"] = "2026-09-06T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "cutoffs"):
            evaluate(self.gold, self.predictions)

    def test_zero_high_risk_denominator_not_zero_error(self):
        for case in self.gold["cases"]:
            case["high_risk"] = False
        report = evaluate(self.gold, self.predictions)
        self.assertIsNone(report["metrics"]["HFAR"]["value"])
        self.assertIsNone(report["aggregate"]["value"])

    def test_wilson_reference_values(self):
        interval = ratio(5, 10)["wilson95"]
        self.assertAlmostEqual(interval[0], .236593090512564, places=12)
        self.assertAlmostEqual(interval[1], .763406909487436, places=12)
        self.assertGreater(ratio(0, 10)["wilson95"][1], 0)
        self.assertIsNone(ratio(0, 0)["value"])

    def test_multiple_required_roots_need_complete_paths(self):
        source = self.gold["cases"][0]["source"]
        source["acceptable_root_sets"] = [["r1", "r1b"]]
        source["valid_edges"].append({"from": "t1", "to": "r1b", "relation": "quotes"})
        self.assertFalse(evaluate(self.gold, self.predictions)["cases"][0]["source_correct"])
        self.predictions["cases"][0]["source"]["roots"].append("r1b")
        self.predictions["cases"][0]["source"]["edges"].append(source["valid_edges"][-1])
        self.assertTrue(evaluate(self.gold, self.predictions)["cases"][0]["source_correct"])


if __name__ == "__main__":
    unittest.main()
