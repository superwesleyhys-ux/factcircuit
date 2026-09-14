import os
import unittest
from unittest.mock import patch

from newsverify.early_risk import build_packet, run_early_risk
from newsverify.cli import main


def case(target_text="The source reports seven."):
    return {
        "target": {"id": "fixture", "text": target_text,
                   "as_of": "2023-12-31T23:59:59Z", "source_version_id": "v1"},
        "materials": [
            {"version_id": "v1", "url": "https://example.invalid/paper", "issuer": "Paper; excerpt",
             "available_at": "2023-01-01T00:00:00Z", "availability_basis": "Archived in 2023.",
             "content": "Source header. The source reports seven. It says the samples followed method A."},
            {"version_id": "v2", "url": "https://example.invalid/paper", "issuer": "Paper; full text",
             "available_at": "2023-01-01T00:00:00Z", "availability_basis": "Same archived paper.",
             "content": "The same publication in full."},
            {"version_id": "future", "url": "https://example.invalid/future", "issuer": "Later notice",
             "available_at": "2025-01-01T00:00:00Z", "availability_basis": "Published later.",
             "content": "SECRET FUTURE FINDING"},
        ],
    }


class FakeTransport:
    kind = "local"
    model = "fixture-model"
    reasoning_effort = "low"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate(self, stage, instructions, packet, schema):
        self.calls.append({"stage": stage, "success": True,
                           "usage": {"input_tokens": 100, "output_tokens": 20}})
        self.packet = packet
        return self.result


class EarlyRiskTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def transport_result(self):
        return FakeTransport({
            "claim_scope": "other_factual", "fact_verdict": "unresolved",
            "evidence_passage_ids": [], "independent_authentication": False,
            "fraud_risk": "low", "risk_signals": [], "rationale": "Fixture only.",
        })

    def test_local_defaults_to_astra_without_api_construction(self):
        with patch("newsverify.early_risk.LocalTunnel", return_value=self.transport_result()) as local, \
                patch("newsverify.early_risk.APITunnel") as api:
            run_early_risk(case())
        local.assert_called_once_with(model="gpt-6-astra", reasoning_effort="low", timeout=180)
        api.assert_not_called()

    def test_local_project_model_and_explicit_override(self):
        os.environ["FACTCIRCUIT_MODEL"] = "gpt-5.6-sol"
        with patch("newsverify.early_risk.LocalTunnel", return_value=self.transport_result()) as local:
            run_early_risk(case())
            self.assertEqual("gpt-5.6-sol", local.call_args.kwargs["model"])
            run_early_risk(case(), model="gpt-5.6-luna", reasoning_effort="high")
            self.assertEqual("gpt-5.6-luna", local.call_args.kwargs["model"])
            self.assertEqual("high", local.call_args.kwargs["reasoning_effort"])

    def test_api_requires_provider_model_and_ignores_local_env(self):
        os.environ["FACTCIRCUIT_MODEL"] = "gpt-6-astra"
        with patch("newsverify.early_risk.APITunnel") as api, \
                patch("newsverify.early_risk.LocalTunnel") as local:
            with self.assertRaisesRegex(ValueError, "API mode requires"):
                run_early_risk(case(), tunnel="api")
            api.assert_not_called()
            local.assert_not_called()
        os.environ["OPENAI_MODEL"] = "provider-api-model"
        with patch("newsverify.early_risk.APITunnel", return_value=self.transport_result()) as api:
            run_early_risk(case(), tunnel="api")
            api.assert_called_once_with(model="provider-api-model", reasoning_effort="low", timeout=180)

    def test_empty_explicit_model_never_falls_back(self):
        os.environ["FACTCIRCUIT_MODEL"] = "gpt-6-astra"
        os.environ["OPENAI_MODEL"] = "provider-api-model"
        for tunnel in ("local", "api"):
            with self.subTest(tunnel=tunnel), \
                    patch("newsverify.early_risk.APITunnel") as api, \
                    patch("newsverify.early_risk.LocalTunnel") as local:
                with self.assertRaisesRegex(ValueError, "nonempty"):
                    run_early_risk(case(), tunnel=tunnel, model=" ")
                api.assert_not_called()
                local.assert_not_called()

    def test_packet_uses_origin_and_excludes_post_cutoff_material(self):
        packet, passages = build_packet(case())
        self.assertEqual({"v1"}, {p.version_id for p in passages.values()})
        serialized = repr(packet)
        self.assertNotIn("SECRET FUTURE FINDING", serialized)
        self.assertNotIn("future", {row["version_id"] for row in packet["source_inventory"]})

    def test_supported_attribution_gets_program_owned_exact_offsets(self):
        packet, passages = build_packet(case())
        passage_id = next(pid for pid, p in passages.items() if "reports seven" in p.text)
        transport = FakeTransport({
            "claim_scope": "source_attribution", "fact_verdict": "supported",
            "evidence_passage_ids": [passage_id], "independent_authentication": False,
            "fraud_risk": "low", "risk_signals": [], "rationale": "The source says this.",
        })
        result = run_early_risk(case(), transport=transport)
        evidence = result["evidence"][0]
        content = case()["materials"][0]["content"]
        self.assertEqual(evidence["quote"], content[evidence["start"]:evidence["end"]])
        self.assertEqual(1, len(result["calls"]))

    def test_risk_cannot_replace_unresolved_factual_verdict(self):
        transport = FakeTransport({
            "claim_scope": "real_world_provenance", "fact_verdict": "contradicted",
            "evidence_passage_ids": ["p001"], "independent_authentication": False,
            "fraud_risk": "high", "risk_signals": ["self-attestation"],
            "rationale": "High risk is not proof.",
        })
        with self.assertRaisesRegex(ValueError, "must remain unresolved"):
            run_early_risk(case("The samples actually came from method A."), transport=transport)

    def test_unknown_passage_id_fails_closed(self):
        transport = FakeTransport({
            "claim_scope": "source_attribution", "fact_verdict": "supported",
            "evidence_passage_ids": ["p999"], "independent_authentication": False,
            "fraud_risk": "low", "risk_signals": [], "rationale": "Citation supplied.",
        })
        with self.assertRaisesRegex(ValueError, "unavailable passage"):
            run_early_risk(case(), transport=transport)

    def test_cli_exposes_local_first_early_risk_command(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "case.json"
            output = Path(folder) / "result.json"
            source.write_text(json.dumps(case()), encoding="utf-8")
            expected = {"fact_verdict": "unresolved", "fraud_risk": "high"}
            with patch("newsverify.early_risk.run_early_risk", return_value=expected) as run:
                self.assertEqual(0, main(["early-risk", str(source), "--output", str(output),
                                          "--model", "fixture-model"]))
            self.assertEqual(expected, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual("local", run.call_args.kwargs["tunnel"])


if __name__ == "__main__":
    unittest.main()
