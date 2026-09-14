#!/usr/bin/env python3
"""Offline guard tests, using explicitly synthetic scoring fixtures."""
import copy
import unittest
from run_test import HERE, read, validate_setup, validate_response, request_for, digest
from summarize_test import score_arm

class EvaluationSafeguards(unittest.TestCase):
    def setUp(self):
        self.corpus = read(HERE / "corpus.json")
        self.reg = read(HERE / "REGISTRATION.json")

    def test_both_arms_receive_identical_evidence(self):
        validate_setup(self.corpus, self.reg)
        for case in self.corpus["cases"]:
            a = request_for(case, "direct", self.reg)
            b = request_for(case, "harness", self.reg)
            self.assertEqual(digest(a["packet"]), digest(b["packet"]))
            self.assertNotEqual(a["instructions"], b["instructions"])
            self.assertEqual(a["schema"], b["schema"])

    def test_duplicate_schedule_refused(self):
        self.reg["schedule"][1] = copy.deepcopy(self.reg["schedule"][0])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            validate_setup(self.corpus, self.reg)

    def test_future_passage_refused(self):
        self.corpus["cases"][0]["packet"]["evidence_passages"][0]["available_at"] = "2025-01-01"
        with self.assertRaisesRegex(ValueError, "post-cutoff"):
            validate_setup(self.corpus, self.reg)

    def test_arm_specific_packet_refused(self):
        self.corpus["cases"][0]["harness_packet"] = {}
        with self.assertRaisesRegex(ValueError, "one shared"):
            validate_setup(self.corpus, self.reg)

    def test_outcome_field_refused(self):
        self.corpus["cases"][0]["packet"]["target_label"] = "elevated"
        with self.assertRaisesRegex(ValueError, "unexpected packet"):
            validate_setup(self.corpus, self.reg)

    def test_invalid_references_refused(self):
        result = {"risk": "insufficient_evidence", "cutoff_assessment": "no_specific_concern",
                  "fabrication_established": False, "confidence": "low",
                  "evidence_passage_ids": ["p999"], "rationale": "Synthetic test rationale."}
        with self.assertRaisesRegex(ValueError, "citation"):
            validate_response(result, self.corpus["cases"][0]["packet"])

    def test_failures_and_abstentions_keep_denominators(self):
        # Deliberately synthetic labels and responses. No inference or real scoring here.
        cases = {c["id"]: c for c in self.corpus["cases"]}
        ids = list(cases)
        gold = {cid: {"outcome_risk_target": "elevated" if i < 4 else "ordinary",
                      "cutoff_assessment_target": "no_specific_concern",
                      "fabrication_established_target": False} for i, cid in enumerate(ids)}
        rows = []
        for i, cid in enumerate(ids):
            value = {"risk": "insufficient_evidence" if i < 4 else "ordinary",
                     "cutoff_assessment": "no_specific_concern", "fabrication_established": False,
                     "confidence": "low", "evidence_passage_ids": ["p001"], "rationale": "Synthetic fixture only."}
            rows.append({"case_id": cid, "status": "failed" if i == 0 else "completed", "result": value, "calls": []})
        metrics, _ = score_arm(rows, gold, cases)
        self.assertEqual(metrics["cases"], 8)
        self.assertEqual(metrics["positive_denominator"], 4)
        self.assertEqual(metrics["negative_denominator"], 4)
        self.assertEqual(metrics["abstentions"], 3)
        self.assertEqual(metrics["failures_or_invalid"], 1)
        self.assertEqual(metrics["outcome_accuracy"], 0.5)
        self.assertEqual(metrics["balanced_accuracy"], 0.5)
        self.assertIsNone(metrics["total_tokens"])

if __name__ == "__main__":
    unittest.main()
