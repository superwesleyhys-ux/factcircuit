"""Shared model-harness behavior, with both transports replaced by local fakes."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from newsverify.cli import main
from newsverify.model_runner import run_model_trace


def snapshot():
    return {
        "target": {
            "id": "model-route-test",
            "text": "The factory opened on Monday.",
            "as_of": "2026-09-04T20:00:00Z",
            "source_version_id": "record:v1",
        },
        "rounds": [[{
            "version_id": "record:v1",
            "url": "https://example.invalid/record",
            "content": "The factory opened on Monday.",
            "retrieved_at": "2026-09-05T01:00:00Z",
            "available_at": "2026-09-04T12:00:00Z",
            "availability_basis": "Synthetic fixture snapshot.",
            "issuer": "fixture-publisher",
        }]],
        "config": {"max_rounds": 1, "max_documents": 10,
                   "max_decomposition_calls": 10},
    }


def packet_materials(value):
    """Find source records without depending on the prompt envelope's layout."""
    if isinstance(value, dict):
        if "version_id" in value and "content" in value:
            yield value
        else:
            for child in value.values():
                yield from packet_materials(child)
    elif isinstance(value, list):
        for child in value:
            yield from packet_materials(child)


class FakeTunnel:
    """Return evidence-backed deterministic responses and capture model inputs."""

    def __init__(self, kind="local", model="test-model", reasoning_effort=None):
        self.kind = kind
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.calls = []
        self.transform = None
        self.failure = None

    def generate(self, stage, instructions, packet, schema):
        self.calls.append(deepcopy({"stage": stage, "instructions": instructions,
                                    "packet": packet, "schema": schema}))
        if self.failure:
            raise self.failure
        material = next(packet_materials(packet))
        if "fragments" in schema["properties"]:
            result = {"fragments": [{"text": material["content"],
                                      "quote": material["content"],
                                      "qualifiers": []}], "notes": "Fixture decomposition."}
        else:
            result = {"verdict": "supported", "basis": [{
                "version_id": material["version_id"], "quote": material["content"],
            }], "rationale": "The source states the target claim."}
        return self.transform(result) if self.transform else result


class ModelTraceTests(unittest.TestCase):
    def setUp(self):
        blocker = patch("socket.socket", side_effect=AssertionError("network forbidden in route tests"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_dir.cleanup)
        environment = patch.dict(os.environ, {"CODEX_HOME": self.config_dir.name}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_default_selects_only_local_and_records_execution(self):
        tunnel = FakeTunnel()
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel) as local, \
                patch("newsverify.model_runner.APITunnel") as api:
            report = run_model_trace(snapshot(), model="test-model")
        local.assert_called_once()
        api.assert_not_called()
        self.assertEqual(report["execution_mode"], "model_trace")
        self.assertEqual(report["execution"]["tunnel"], "local")
        self.assertEqual(report["execution"]["model"], "test-model")
        self.assertEqual(report["execution"]["model_calls"], tunnel.calls)
        self.assertEqual(report["fact_status"], "supported")
        self.assertEqual(report["errors"], [])

    def test_api_is_selected_only_by_explicit_option(self):
        tunnel = FakeTunnel(kind="api")
        with patch("newsverify.model_runner.LocalTunnel") as local, \
                patch("newsverify.model_runner.APITunnel", return_value=tunnel) as api:
            report = run_model_trace(snapshot(), tunnel="api", model="test-model")
        local.assert_not_called()
        api.assert_called_once()
        self.assertEqual(report["execution"]["tunnel"], "api")
        self.assertEqual(report["fact_status"], "supported")
        self.assertEqual(report["errors"], [])

    def test_both_tunnels_receive_identical_prompts_packets_and_schemas(self):
        local = FakeTunnel()
        api = FakeTunnel(kind="api")
        with patch("newsverify.model_runner.LocalTunnel", return_value=local), \
                patch("newsverify.model_runner.APITunnel", return_value=api):
            local_report = run_model_trace(snapshot(), model="test-model")
            api_report = run_model_trace(snapshot(), tunnel="api", model="test-model")
        self.assertEqual(local.calls, api.calls)
        self.assertGreaterEqual(len(local.calls), 2)
        self.assertEqual(local_report["fragments"], api_report["fragments"])
        self.assertEqual(local_report["verification_history"], api_report["verification_history"])

    def test_reference_answers_do_not_enter_model_prompts(self):
        payload = snapshot()
        payload.update({"gold": "SECRET_GOLD_LABEL", "expected_status": "SECRET_EXPECTED_LABEL",
                        "reference_answer": "SECRET_REFERENCE_ANSWER"})
        tunnel = FakeTunnel()
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel):
            report = run_model_trace(payload, model="test-model")
        self.assertEqual(report["errors"], [])
        captured = json.dumps(tunnel.calls)
        for secret in ("SECRET_GOLD_LABEL", "SECRET_EXPECTED_LABEL", "SECRET_REFERENCE_ANSWER"):
            self.assertNotIn(secret, captured)

    def test_invalid_decomposition_quote_fails_unresolved(self):
        tunnel = FakeTunnel()

        def invent_quote(response):
            if "fragments" in response:
                response["fragments"][0]["quote"] = "Words never present in the source."
            return response

        tunnel.transform = invent_quote
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel):
            report = run_model_trace(snapshot(), model="test-model")
        self.assertEqual(report["fact_status"], "unresolved")
        self.assertEqual(report["errors"][0]["stage"], "decomposer")
        self.assertEqual(report["verification_history"], [])
        self.assertEqual(report["fragments"], [])

    def test_non_unique_decomposition_quote_fails_unresolved(self):
        for content, quote in (("Repeated sentence. Repeated sentence.", "Repeated sentence."),
                               ("aaa", "aa")):
            with self.subTest(content=content, quote=quote):
                payload = snapshot()
                payload["rounds"][0][0]["content"] = content
                tunnel = FakeTunnel()

                def ambiguous_quote(response):
                    if "fragments" in response:
                        response["fragments"][0]["quote"] = quote
                    return response

                tunnel.transform = ambiguous_quote
                with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel):
                    report = run_model_trace(payload, model="test-model")
                self.assertEqual(report["fact_status"], "unresolved")
                self.assertEqual(report["errors"][0]["stage"], "decomposer")

    def test_invalid_verification_quote_fails_unresolved(self):
        tunnel = FakeTunnel()

        def invent_quote(response):
            if "verdict" in response:
                response["basis"][0]["quote"] = "Unsupported invented quote."
            return response

        tunnel.transform = invent_quote
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel):
            report = run_model_trace(snapshot(), model="test-model")
        self.assertEqual(report["fact_status"], "unresolved")
        self.assertEqual(report["errors"][0]["stage"], "verifier")
        self.assertEqual(report["verification_history"], [])

    def test_response_schema_rejects_unexpected_fields_and_wrong_types(self):
        mutations = (
            ("decomposer", lambda response: dict(response, undeclared="unexpected")),
            ("decomposer", lambda response: dict(response, notes=123)),
            ("decomposer", lambda response: dict(response, fragments=[])),
            ("verifier", lambda response: dict(response, verdict="probably_supported")),
            ("verifier", lambda response: dict(response, basis="not a list")),
        )
        for stage, mutate in mutations:
            with self.subTest(stage=stage, mutation=mutate):
                tunnel = FakeTunnel()
                tunnel.transform = lambda response: mutate(response) if (
                    (stage == "decomposer") == ("fragments" in response)) else response
                with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel):
                    report = run_model_trace(snapshot(), model="test-model")
                self.assertEqual(report["fact_status"], "unresolved")
                self.assertEqual(report["errors"][0]["stage"], stage)

    def test_future_material_is_audited_without_reaching_either_model_stage(self):
        payload = snapshot()
        future = deepcopy(payload["rounds"][0][0])
        future.update({"version_id": "future:v2", "content": "SECRET_FUTURE_MATERIAL",
                       "available_at": "2026-09-05T00:00:00Z"})
        payload["rounds"][0].insert(0, future)
        tunnel = FakeTunnel()
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel):
            report = run_model_trace(payload, model="test-model")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["eligible_version_ids"], ["record:v1"])
        self.assertNotIn("SECRET_FUTURE_MATERIAL", json.dumps(tunnel.calls))
        self.assertNotIn("future:v2", json.dumps(tunnel.calls))
        audit = next(item for item in report["analysis_history"] if item["version_id"] == "future:v2")
        self.assertFalse(audit["accepted"])
        self.assertIn("version_available_after_as_of", audit["exclusion_reasons"])
        self.assertEqual(audit["analysis"]["fragments"][0]["span"]["quote"], "SECRET_FUTURE_MATERIAL")

    def test_local_failure_never_falls_back_to_api(self):
        tunnel = FakeTunnel()
        tunnel.failure = RuntimeError("local process failed")
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel), \
                patch("newsverify.model_runner.APITunnel") as api:
            report = run_model_trace(snapshot(), model="test-model")
        api.assert_not_called()
        self.assertEqual(report["fact_status"], "unresolved")
        self.assertEqual(report["errors"][0]["message"], "local process failed")

    def test_unknown_tunnel_is_a_setup_error(self):
        with patch("newsverify.model_runner.LocalTunnel") as local, \
                patch("newsverify.model_runner.APITunnel") as api:
            with self.assertRaises(ValueError):
                run_model_trace(snapshot(), tunnel="automatic", model="test-model")
        local.assert_not_called()
        api.assert_not_called()

    def test_astra_project_default_ignores_global_model_but_keeps_reasoning(self):
        Path(self.config_dir.name, "config.toml").write_text(
            'model = "configured-test-model"\nmodel_reasoning_effort = "high"\n', encoding="utf-8")
        tunnel = FakeTunnel(model="gpt-6-astra", reasoning_effort="high")
        with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel) as local:
            report = run_model_trace(snapshot())
        local.assert_called_once_with(model="gpt-6-astra", reasoning_effort="high", timeout=180)
        self.assertEqual(report["execution"]["model"], "gpt-6-astra")
        self.assertEqual(report["execution"]["reasoning_effort"], "high")
        self.assertIn('model = "configured-test-model"', Path(self.config_dir.name, "config.toml").read_text())

    def test_no_config_defaults_to_local_astra_without_api_key(self):
        with patch("newsverify.model_runner.LocalTunnel", return_value=FakeTunnel(model="gpt-6-astra")) as local, \
                patch("newsverify.model_runner.APITunnel") as api:
            run_model_trace(snapshot())
        local.assert_called_once_with(model="gpt-6-astra", reasoning_effort="medium", timeout=180)
        api.assert_not_called()

    def test_project_environment_and_explicit_local_model_override(self):
        with patch.dict(os.environ, {"FACTCIRCUIT_MODEL": "project-model"}), \
                patch("newsverify.model_runner.LocalTunnel", return_value=FakeTunnel()) as local:
            run_model_trace(snapshot())
            self.assertEqual(local.call_args.kwargs["model"], "project-model")
            run_model_trace(snapshot(), model="gpt-5.6-luna")
            self.assertEqual(local.call_args.kwargs["model"], "gpt-5.6-luna")

    def test_api_requires_explicit_provider_model_before_construction(self):
        with patch("newsverify.model_runner.LocalTunnel") as local, \
                patch("newsverify.model_runner.APITunnel") as api:
            with self.assertRaisesRegex(ValueError, "API mode requires"):
                run_model_trace(snapshot(), tunnel="api")
        local.assert_not_called()
        api.assert_not_called()

    def test_api_uses_explicit_provider_environment(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "provider-model", "FACTCIRCUIT_MODEL": "local-only-model"}), \
                patch("newsverify.model_runner.APITunnel", return_value=FakeTunnel(kind="api")) as api, \
                patch("newsverify.model_runner.LocalTunnel") as local:
            run_model_trace(snapshot(), tunnel="api")
        self.assertEqual(api.call_args.kwargs["model"], "provider-model")
        local.assert_not_called()

    def test_api_ignores_invalid_local_config_and_defaults_to_medium(self):
        Path(self.config_dir.name, "config.toml").write_text("invalid = [", encoding="utf-8")
        with patch("newsverify.model_runner.APITunnel", return_value=FakeTunnel(kind="api")) as api, \
                patch("newsverify.model_runner.LocalTunnel") as local:
            run_model_trace(snapshot(), tunnel="api", model="provider-model")
        api.assert_called_once_with(model="provider-model", reasoning_effort="medium", timeout=180)
        local.assert_not_called()

    def test_api_does_not_inherit_local_reasoning_effort(self):
        Path(self.config_dir.name, "config.toml").write_text(
            'model_reasoning_effort = "high"\n', encoding="utf-8")
        with patch("newsverify.model_runner.APITunnel", return_value=FakeTunnel(kind="api")) as api:
            run_model_trace(snapshot(), tunnel="api", model="provider-model")
        api.assert_called_once_with(model="provider-model", reasoning_effort="medium", timeout=180)

    def test_explicit_api_reasoning_effort_is_preserved(self):
        Path(self.config_dir.name, "config.toml").write_text("invalid = [", encoding="utf-8")
        with patch("newsverify.model_runner.APITunnel", return_value=FakeTunnel(kind="api")) as api:
            run_model_trace(snapshot(), tunnel="api", model="provider-model", reasoning_effort="low")
        api.assert_called_once_with(model="provider-model", reasoning_effort="low", timeout=180)

    def test_explicit_empty_model_does_not_fall_back(self):
        with patch("newsverify.model_runner.LocalTunnel") as local, \
                patch("newsverify.model_runner.APITunnel") as api:
            for tunnel in ("local", "api"):
                with self.assertRaisesRegex(ValueError, "nonempty"):
                    run_model_trace(snapshot(), tunnel=tunnel, model="  ")
        local.assert_not_called()
        api.assert_not_called()

    def test_empty_project_model_override_is_an_error(self):
        with patch.dict(os.environ, {"FACTCIRCUIT_MODEL": ""}), \
                patch("newsverify.model_runner.LocalTunnel") as local:
            with self.assertRaisesRegex(ValueError, "nonempty"):
                run_model_trace(snapshot())
        local.assert_not_called()

    def test_cli_defaults_local_and_accepts_explicit_api(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "snapshot.json"
            output = Path(directory) / "report.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            for kind, arguments in (("local", []), ("api", ["--tunnel", "api"])):
                with self.subTest(tunnel=kind):
                    local = FakeTunnel()
                    api = FakeTunnel(kind="api")
                    with patch("newsverify.model_runner.LocalTunnel", return_value=local) as local_factory, \
                            patch("newsverify.model_runner.APITunnel", return_value=api) as api_factory, \
                            redirect_stdout(StringIO()):
                        result = main(["trace-model", str(source), "--model", "test-model",
                                       "--output", str(output), *arguments])
                    self.assertEqual(result, 0)
                    report = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(report["execution"]["tunnel"], kind)
                    self.assertEqual(local_factory.call_count, int(kind == "local"))
                    self.assertEqual(api_factory.call_count, int(kind == "api"))

    def test_cli_refuses_to_overwrite_input_before_starting_model(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "snapshot.json"
            original = json.dumps(snapshot())
            source.write_text(original, encoding="utf-8")
            with patch("newsverify.model_runner.LocalTunnel") as local, \
                    patch("newsverify.model_runner.APITunnel") as api, \
                    redirect_stderr(StringIO()):
                result = main(["trace-model", str(source), "--model", "test-model",
                               "--output", str(source)])
            self.assertEqual(result, 2)
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            local.assert_not_called()
            api.assert_not_called()

    def test_cli_passes_model_reasoning_and_timeout_to_selected_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "snapshot.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            with patch("newsverify.model_runner.APITunnel", return_value=FakeTunnel(kind="api")) as api, \
                    redirect_stdout(StringIO()):
                result = main(["trace-model", str(source), "--tunnel", "api", "--model", "chosen-model",
                               "--reasoning-effort", "high", "--timeout", "27"])
        self.assertEqual(result, 0)
        api.assert_called_once_with(model="chosen-model", reasoning_effort="high", timeout=27)

    def test_cli_saves_runtime_failure_report_and_returns_one(self):
        tunnel = FakeTunnel()
        tunnel.failure = RuntimeError("local process failed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "snapshot.json"
            output = Path(directory) / "report.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            with patch("newsverify.model_runner.LocalTunnel", return_value=tunnel), \
                    redirect_stdout(StringIO()):
                result = main(["trace-model", str(source), "--model", "test-model",
                               "--output", str(output)])
            self.assertEqual(result, 1)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["fact_status"], "unresolved")
        self.assertEqual(report["errors"][0]["message"], "local process failed")

    def test_cli_returns_two_for_setup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "snapshot.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            with patch("newsverify.model_runner.LocalTunnel", side_effect=ValueError("local executable missing")), \
                    redirect_stderr(StringIO()) as errors:
                result = main(["trace-model", str(source), "--model", "test-model"])
        self.assertEqual(result, 2)
        self.assertIn("local executable missing", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
