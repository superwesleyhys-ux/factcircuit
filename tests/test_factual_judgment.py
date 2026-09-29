"""Fact-verifier boundary tests with scripted inference, never accuracy scores."""
from copy import deepcopy
from dataclasses import asdict
import importlib
import importlib.util
import unittest

from newsverify.provenance import Target, MaterialVersion, Gap, Span


TARGET = Target("change", "The measured count increased by 30%.", "2024-12-31T23:59:59Z")
RECORD = MaterialVersion("raw", "https://example.invalid/raw", "Instrument counts: before 60; after 66.",
    "2026-09-29T00:00:00Z", available_at="2024-01-01T00:00:00Z",
    availability_basis="Synthetic stored record, not a real measurement.")
KINDS = ("target_scope", "primary_record", "method", "calculation", "source_dependence", "counterevidence")


def quote():
    return {"version_id": "raw", "quote": RECORD.content}


def context():
    return {"materials": [asdict(RECORD)], "analyses": {}, "gaps": [],
            "verification_history": [], "relations": [], "origins": []}


def response(verdict="supported"):
    checks = [{"kind": kind, "requirement": "required" if kind in {"target_scope", "primary_record"} else "not_applicable",
        "status": "verified" if kind in {"target_scope", "primary_record"} else "not_applicable",
        "basis": [quote()] if kind in {"target_scope", "primary_record"} else [],
        "rationale": "Synthetic evidence annotation.", "question": "", "action": "search", "locator": None}
        for kind in KINDS]
    return {"evidence_verdict": verdict, "evidence_basis": [quote()],
        "world_verdict": verdict, "world_basis": [quote()],
        "rationale": "Scripted fact assessment, not real model reasoning.",
        "checks": checks, "calculations": []}


def calculation(claimed="30"):
    return {"id": "count-change", "operation": "percent_change",
        "operands": [{"value": "60", "unit": "count", "basis": quote()},
                     {"value": "66", "unit": "count", "basis": quote()}],
        "claimed_value": claimed, "target_quote": "increased by " + claimed + "%",
        "rounding_places": "0"}


class Transport:
    def __init__(self, result):
        self.result = result
        self.inputs = []

    def generate(self, stage, instructions, packet, schema):
        self.inputs.append(deepcopy(packet))
        return deepcopy(self.result)


class ScriptedTransport:
    kind = "local"
    model = "scripted-no-inference"
    reasoning_effort = "low"

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.inputs = []

    def generate(self, stage, instructions, packet, schema):
        expected, result = self.steps.pop(0)
        if stage != expected:
            raise AssertionError(f"Expected {expected}, got {stage}")
        self.inputs.append(deepcopy(packet))
        if isinstance(result, Exception):
            raise result
        self.calls.append({"stage": stage, "success": True, "status": "completed",
            "wall_seconds": 0, "usage": {"input_tokens": 10, "output_tokens": 2}})
        return deepcopy(result)


def decomposition(source):
    return {"fragments": [{"id": "fact", "text": source["content"], "quote": source["content"], "qualifiers": []}],
        "relations": [], "gaps": [], "resolutions": [], "origins": [],
        "revisit_versions": [], "notes": "Scripted synthetic fragment."}


def loop_case():
    notice = asdict(RECORD)
    notice.update(version_id="notice", url="https://example.invalid/notice",
        content="The count increased by 10%. Original instrument log: https://example.invalid/raw")
    target = asdict(TARGET)
    target.update(text="The measured count increased by 10%.", source_version_id="notice")
    return {"target": target, "materials": [notice, asdict(RECORD)], "initial_version_ids": ["notice"],
        "config": {"max_rounds": 2, "max_documents": 2, "max_decomposition_calls": 2}}


def loop_script():
    case = loop_case()
    first = response()
    for field in ("evidence_basis", "world_basis"):
        first[field] = [{"version_id": "notice", "quote": case["materials"][0]["content"]}]
    for check in first["checks"]:
        if check["basis"]:
            check["basis"] = deepcopy(first["evidence_basis"])
    first["checks"][1].update(status="missing", question="Fetch the original instrument log.",
        action="fetch", locator="https://example.invalid/raw")
    second = response()
    second["checks"][3].update(requirement="required", status="verified", basis=[quote()])
    second["calculations"] = [calculation("10")]
    return [("decompose", decomposition(case["materials"][0])), ("fact_verify", first),
        ("select", {"version_id": "raw", "rationale": "The original instrument log answers the measurement gap."}),
        ("decompose", decomposition(case["materials"][1])), ("fact_verify", second)]


class FactualJudgmentTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec("newsverify.factual_judgment")
        self.assertIsNotNone(spec, "The requested fact-judgment adapter is not implemented")
        self.module = importlib.import_module("newsverify.factual_judgment")

    def verify(self, result, packet=None, target=TARGET):
        transport = Transport(result)
        verifier = self.module.FactualVerifier(transport)
        value = verifier.verify(target, packet or context())
        return value, verifier, transport

    def test_missing_primary_record_blocks_world_but_preserves_text_support(self):
        result = response()
        result["checks"][1].update(status="missing", question="Fetch the original instrument log.",
            action="fetch", locator="https://example.invalid/instrument-log")
        value, verifier, _ = self.verify(result)
        self.assertEqual("supported", value.evidence_verdict)
        self.assertEqual("unresolved", value.world_verdict)
        gap = next(g for g in value.gaps if g.id == "fact:change:primary_record")
        self.assertEqual(("world", True, "fetch"), (gap.dimension, gap.blocking, gap.action))
        self.assertEqual("https://example.invalid/instrument-log", gap.locator)
        self.assertEqual("supported", verifier.history[-1]["requested_world_verdict"])

    def test_target_scope_cannot_be_marked_not_applicable_to_admit_a_fact(self):
        result = response()
        result["checks"][0].update(requirement="not_applicable", status="not_applicable", basis=[])
        with self.assertRaises(ValueError):
            self.verify(result)

    def test_relevant_raw_record_can_support_world_judgment(self):
        value, _, _ = self.verify(response())
        self.assertEqual("supported", value.world_verdict)
        self.assertEqual([], list(value.gaps))

    def test_falsifying_record_can_produce_false_judgment(self):
        value, _, _ = self.verify(response("contradicted"))
        self.assertEqual("contradicted", value.world_verdict)
        self.assertEqual("raw", value.world_basis[0].version_id)

    def test_missing_measurement_is_not_turned_into_a_false_judgment(self):
        result = response("contradicted")
        result["checks"][1].update(status="missing", question="Find the missing measurement.")
        value, _, _ = self.verify(result)
        self.assertEqual("unresolved", value.world_verdict)

    def test_failed_calculation_vetoes_claimed_support_and_records_actual_value(self):
        result = response()
        result["checks"][3].update(requirement="required", status="verified", basis=[quote()])
        result["calculations"] = [calculation()]
        value, verifier, _ = self.verify(result)
        self.assertEqual("unresolved", value.world_verdict)
        self.assertEqual("10", verifier.history[-1]["calculations"][0]["computed_value"])
        self.assertEqual("mismatch", verifier.history[-1]["calculations"][0]["status"])
        self.assertTrue(any(g.action == "reanalyse" for g in value.gaps))

    def test_matching_calculation_retains_supported_judgment(self):
        result = response()
        result["checks"][3].update(requirement="required", status="verified", basis=[quote()])
        result["calculations"] = [calculation("10")]
        target = Target("change", "The measured count increased by 10%.", TARGET.as_of)
        value, verifier, _ = self.verify(result, target=target)
        self.assertEqual("supported", value.world_verdict)
        self.assertEqual("match", verifier.history[-1]["calculations"][0]["status"])

    def test_verified_calculation_cannot_omit_recomputable_operands(self):
        result = response()
        result["checks"][3].update(requirement="required", status="verified", basis=[quote()])
        with self.assertRaises(ValueError):
            self.verify(result)

    def test_ungrounded_numeric_operand_is_rejected(self):
        result = response()
        item = calculation()
        item["operands"][0]["value"] = "600"
        result["calculations"] = [item]
        with self.assertRaises(ValueError):
            self.verify(result)

    def test_incompatible_units_are_rejected(self):
        result = response()
        item = calculation()
        item["operands"][1]["unit"] = "kg"
        result["calculations"] = [item]
        with self.assertRaises(ValueError):
            self.verify(result)

    def test_numeric_binding_does_not_drop_a_sign_or_exponent(self):
        for source_value in ("\u221260", "x-60", "1e-60"):
            with self.subTest(source_value=source_value):
                text = f"Instrument counts: before {source_value}; after 66."
                materials = {"raw": {"content": text}}
                item = calculation()
                for operand in item["operands"]:
                    operand["basis"]["quote"] = text
                with self.assertRaises(ValueError):
                    self.module.recompute(item, TARGET, materials)

    def test_zero_denominator_does_not_claim_a_numeric_result(self):
        result = response()
        result["calculations"] = [calculation()]
        result["calculations"][0]["operands"][0]["value"] = "0"
        packet = context()
        packet["materials"][0]["content"] = "Instrument counts: before 0; after 66."
        for check in result["checks"]:
            for basis in check["basis"]:
                basis["quote"] = packet["materials"][0]["content"]
        result["evidence_basis"][0]["quote"] = packet["materials"][0]["content"]
        result["world_basis"][0]["quote"] = packet["materials"][0]["content"]
        for operand in result["calculations"][0]["operands"]:
            operand["basis"]["quote"] = packet["materials"][0]["content"]
        value, verifier, _ = self.verify(result, packet)
        self.assertEqual("unresolved", value.world_verdict)
        self.assertEqual("undefined", verifier.history[-1]["calculations"][0]["status"])

    def test_missing_or_duplicate_check_categories_fail_closed(self):
        for mutate in (lambda c: c.pop(), lambda c: c.__setitem__(5, deepcopy(c[0]))):
            result = response()
            mutate(result["checks"])
            with self.assertRaises(ValueError):
                self.verify(result)

    def test_previous_verdicts_do_not_anchor_the_fact_model_input(self):
        packet = context()
        packet["verification_history"] = [{"rationale": "ANCHOR_SECRET_VERDICT"}]
        packet["analyses"] = {"raw": {"notes": "ANCHOR_SECRET_ANALYSIS"}}
        _, _, transport = self.verify(response(), packet)
        import json
        serialized = json.dumps(transport.inputs)
        self.assertNotIn("ANCHOR_SECRET", serialized)
        self.assertIn(RECORD.content, serialized)

    def test_new_record_explicitly_closes_its_existing_fact_gap(self):
        packet = context()
        packet["gaps"] = [asdict(Gap("fact:change:primary_record", "Fetch the original log.",
            stage="verification", dimension="world", target_id=TARGET.id,
            basis=(Span("raw", 0, len(RECORD.content), RECORD.content),),
            decision_impact="The primary measurement is unverified."))]
        value, _, _ = self.verify(response(), packet)
        self.assertEqual(["fact:change:primary_record"], [r.gap_id for r in value.resolutions])
        self.assertTrue(value.resolutions[0].basis)


class FactualIntegrationTests(unittest.TestCase):
    def test_later_arithmetic_refutation_closes_the_earlier_mismatch_gap(self):
        from newsverify.double_loop import run_double_loop_trace
        case = loop_case()
        case["target"].update(text=TARGET.text, source_version_id="raw")
        case["initial_version_ids"] = ["raw"]
        first = response()
        first["checks"][3].update(requirement="required", status="verified", basis=[quote()])
        first["calculations"] = [calculation()]
        second = deepcopy(first)
        second.update(evidence_verdict="contradicted", world_verdict="contradicted")
        second["rationale"] = "The same scoped log gives 10%, so the target's 30% is contradicted."
        steps = [("decompose", decomposition(case["materials"][1])), ("fact_verify", first),
            ("select", {"version_id": "notice", "rationale": "Inspect the linked dispatch for the discrepancy."}),
            ("decompose", decomposition(case["materials"][0])), ("fact_verify", second)]
        transport = ScriptedTransport(steps)
        report = run_double_loop_trace(case, transport=transport, factual_judgment=True)
        self.assertEqual([], report["errors"])
        self.assertEqual("contradicted", report["fact_status"])
        self.assertFalse(any(g["id"] == "fact:change:calculation:count-change" for g in report["gaps"]))
        self.assertEqual(["unresolved", "contradicted"],
            [h["world_verdict"] for h in report["factual_judgment"]["history"]])
        self.assertEqual([], transport.steps)

    def test_actual_loop_fetches_primary_record_and_closes_matching_gap(self):
        from newsverify.double_loop import run_double_loop_trace
        transport = ScriptedTransport(loop_script())
        report = run_double_loop_trace(loop_case(), transport=transport, factual_judgment=True)
        self.assertEqual([], report["errors"])
        self.assertEqual("supported", report["fact_status"])
        self.assertEqual(["notice", "raw"], report["eligible_version_ids"])
        self.assertFalse(any(g["id"] == "fact:change:primary_record" for g in report["gaps"]))
        history = report["factual_judgment"]["history"]
        self.assertEqual(["unresolved", "supported"], [h["world_verdict"] for h in history])
        self.assertEqual("10", history[-1]["calculations"][0]["computed_value"])
        self.assertEqual([], transport.steps)

    def test_future_material_never_enters_fact_model_packet(self):
        from newsverify.double_loop import run_double_loop_trace
        case = loop_case()
        case["materials"][1].update(available_at="2025-01-01T00:00:00Z", content="FUTURE_FACT_SENTINEL")
        transport = ScriptedTransport(loop_script()[:2])
        report = run_double_loop_trace(case, transport=transport, factual_judgment=True)
        self.assertEqual("unresolved", report["fact_status"])
        import json
        self.assertNotIn("FUTURE_FACT_SENTINEL", json.dumps(transport.inputs))

    def test_fact_model_error_is_an_audited_failure(self):
        from newsverify.double_loop import run_double_loop_trace
        from newsverify.tunnels import TunnelError
        steps = loop_script()[:2]
        steps[1] = ("fact_verify", TunnelError("Scripted fact-model failure"))
        report = run_double_loop_trace(loop_case(), transport=ScriptedTransport(steps), factual_judgment=True)
        self.assertEqual("unresolved", report["fact_status"])
        self.assertTrue(report["errors"])
        self.assertEqual([], report["factual_judgment"]["history"])

    def test_cli_uses_fact_judgment_and_preserves_input(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from newsverify.cli import main
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            output = Path(directory) / "result.json"
            source.write_text(json.dumps(loop_case()))
            original = source.read_bytes()
            with patch("newsverify.double_loop.LocalTunnel", return_value=ScriptedTransport(loop_script())), redirect_stdout(StringIO()):
                code = main(["judge-facts", str(source), "--model", "scripted-no-inference", "--output", str(output)])
            self.assertEqual(0, code)
            self.assertEqual(original, source.read_bytes())
            self.assertEqual("supported", json.loads(output.read_text())["fact_status"])

    def test_news_flag_routes_real_pipeline_to_factual_verifier(self):
        from test_news_tracing_integration import NewsFixtureTransport, COPY, WIRE, RECORD as NEWS_RECORD
        from newsverify.news_tracing_runner import run_news_tracing

        class NewsFacts(NewsFixtureTransport):
            def generate(self, stage, instructions, packet, schema):
                if stage != "fact_verify":
                    return super().generate(stage, instructions, packet, schema)
                result = response()
                source = next((m for m in packet["materials"] if m["version_id"] == "record"), packet["materials"][0])
                basis = [{"version_id": source["version_id"], "quote": source["content"]}]
                result["evidence_basis"] = deepcopy(basis)
                result["world_basis"] = deepcopy(basis)
                for check in result["checks"]:
                    if check["basis"]:
                        check["basis"] = deepcopy(basis)
                return result

        data = {"news": [{"id": "fixture", "text": "The synthetic laboratory measured 30 units.",
            "claims": ["The synthetic laboratory measured 30 units."], "source_version_id": "copy",
            "as_of": "2023-12-31T23:59:59Z", "materials": [COPY, WIRE, NEWS_RECORD]}],
            "config": {"depth": 0, "research_mode": "claim", "factual_judgment": True}}
        report = run_news_tracing(data, transport=NewsFacts())
        trace = report["results"][0]["claims"][0]["trace"]
        self.assertIsNotNone(trace)
        self.assertTrue(trace["factual_judgment"]["history"])


if __name__ == "__main__":
    unittest.main()
