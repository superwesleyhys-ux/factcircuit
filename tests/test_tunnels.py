"""Transport contracts; every inference call is mocked and networking is blocked."""

from io import BytesIO
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from newsverify.tunnels import APITunnel, LocalTunnel, TunnelError, _local_skill_paths


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}
PACKET = {"content": "Closed evidence: 世界", "as_of": "2026-01-01"}
ANSWER = {"answer": "supported"}
USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
         "input_tokens_details": {"cached_tokens": 10},
         "output_tokens_details": {"reasoning_tokens": 4}}
NORMALIZED_USAGE = {**USAGE, "cached_input_tokens": 10, "reasoning_output_tokens": 4}
SECRET = "test-secret-key-never-report"


def response_envelope(text=None, **updates):
    value = {
        "status": "completed", "error": None, "incomplete_details": None,
        "usage": USAGE,
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(ANSWER) if text is None else text}]}],
    }
    value.update(updates)
    return value


class TunnelTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.socket", side_effect=AssertionError("live network forbidden")).start()
        self.process = patch("newsverify.tunnels.subprocess.run",
                             side_effect=AssertionError("live model forbidden")).start()
        self.connection = patch("newsverify.tunnels.http.client.HTTPSConnection",
                                side_effect=AssertionError("live API forbidden")).start()
        self.which = patch("newsverify.tunnels.shutil.which", return_value="/mock/codex").start()
        self.skill_paths = patch("newsverify.tunnels._local_skill_paths", return_value=()).start()
        self.ssl_context = Mock(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
        self.ssl_context.cert_store_stats.return_value = {"x509_ca": 10}
        self.ssl_factory = patch("newsverify.tunnels.ssl.create_default_context",
                                 return_value=self.ssl_context).start()
        self.addCleanup(patch.stopall)

    def local_run(self, result=ANSWER, events=None, returncode=0, stderr=""):
        if events is None:
            events = [{"type": "item.completed", "item": {"type": "agent_message"}},
                      {"type": "turn.completed", "usage": USAGE}]

        def run(command, **kwargs):
            self.local_command = command
            self.local_kwargs = kwargs
            output = Path(command[command.index("-o") + 1])
            self.local_output = output
            if result is not None:
                output.write_text(json.dumps(result), encoding="utf-8")
            self.local_schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
            return subprocess.CompletedProcess(command, returncode,
                                               "\n".join(json.dumps(item) for item in events), stderr)
        self.process.side_effect = run

    def api_response(self, body=None, status=200):
        if body is None:
            body = response_envelope()
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        if isinstance(body, str):
            body = body.encode("utf-8")
        stream = BytesIO(body)
        response = Mock(status=status)
        response.read1.side_effect = stream.read1
        connection = Mock()
        connection.getresponse.return_value = response
        self.connection.side_effect = None
        self.connection.return_value = connection
        return connection

    def generate(self, tunnel):
        return tunnel.generate("verify", "Use supplied evidence only.", PACKET, SCHEMA)

    def assert_safe_failure(self, tunnel, pattern=None):
        with self.assertRaises(TunnelError) as raised:
            self.generate(tunnel)
        message = str(raised.exception)
        if pattern:
            self.assertIn(pattern, message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn(SECRET, json.dumps(tunnel.calls))
        self.assertNotIn(PACKET["content"], json.dumps(tunnel.calls))
        record = tunnel.calls[-1]
        self.assertEqual(record["status"], "failed")
        self.assertFalse(record["success"])
        self.assertGreaterEqual(record["wall_seconds"], 0)
        self.assertIn("usage", record)

    def test_local_success_is_isolated_and_strips_api_keys(self):
        self.local_run(stderr=SECRET)
        tunnel = LocalTunnel("same-model", reasoning_effort="high", timeout=17)
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET, "CODEX_API_KEY": SECRET,
                                    "KEEP_ME": "yes"}):
            self.assertEqual(self.generate(tunnel), ANSWER)
        command = self.local_command
        self.assertEqual(command[:2], ["/mock/codex", "exec"])
        for flag in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("-C") + 1], str(self.local_output.parent))
        self.assertEqual(command[command.index("-m") + 1], "same-model")
        for feature in ("shell_tool", "apps", "plugins", "multi_agent"):
            self.assertIn(feature, command)
            self.assertEqual(command[command.index(feature) - 1], "--disable")
        self.assertIn('web_search="disabled"', command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertEqual(self.local_schema, SCHEMA)
        self.assertEqual(self.local_kwargs["timeout"], 17)
        self.assertNotIn("OPENAI_API_KEY", self.local_kwargs["env"])
        self.assertNotIn("CODEX_API_KEY", self.local_kwargs["env"])
        self.assertEqual(self.local_kwargs["env"]["KEEP_ME"], "yes")
        self.assertFalse(self.local_output.parent.exists())
        self.assertTrue(tunnel.calls[0]["success"])
        self.assertEqual(tunnel.calls[0]["usage"], NORMALIZED_USAGE)
        self.assertEqual(tunnel.kind, "local")
        self.connection.assert_not_called()

    def test_api_payload_matches_local_prompt_and_preserves_usage(self):
        self.local_run()
        self.generate(LocalTunnel("same-model", "high"))
        connection = self.api_response()
        tunnel = APITunnel("same-model", "high", timeout=17, api_key=SECRET)
        self.assertEqual(self.generate(tunnel), ANSWER)
        self.connection.assert_called_once_with("api.openai.com", timeout=17.0, context=self.ssl_context)
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("POST", "/v1/responses"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + SECRET)
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["model"], "same-model")
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertFalse(payload["store"])
        self.assertEqual(payload["text"]["format"], {
            "type": "json_schema", "name": "harness_result", "strict": True, "schema": SCHEMA,
        })
        self.assertEqual(payload["instructions"] + "\n" + payload["input"], self.local_kwargs["input"])
        self.assertNotIn("tools", payload)
        self.assertEqual(tunnel.calls[0]["usage"], NORMALIZED_USAGE)
        self.assertEqual(tunnel.calls[0]["http_status"], 200)
        self.assertTrue(tunnel.calls[0]["success"])
        self.assertEqual(tunnel.kind, "api")
        connection.close.assert_called_once()
        self.assertNotIn(SECRET, json.dumps(tunnel.calls))

    def test_local_disables_installed_skills_per_process_without_changing_login(self):
        self.local_run()
        self.skill_paths.return_value = ('/installed/gstack-review/SKILL.md', '/installed/世界/SKILL.md')
        tunnel = LocalTunnel("same-model")
        with patch.dict(os.environ, {"CODEX_HOME": "/existing/login-home"}):
            self.assertEqual(self.generate(tunnel), ANSWER)
        config = next(v for v in self.local_command if v.startswith("skills.config="))
        self.assertEqual(tomllib.loads(config)["skills"]["config"], [
            {"path": p, "enabled": False} for p in self.skill_paths.return_value
        ])
        self.assertEqual(self.local_kwargs["env"]["CODEX_HOME"], "/existing/login-home")
        self.assertEqual(tunnel.calls[0]["local_skills_disabled"], 2)
        self.connection.assert_not_called()

    def test_local_skill_isolation_failure_stops_before_inference(self):
        self.skill_paths.side_effect = TunnelError("Local skill isolation could not be established; no model call was started.")
        self.assert_safe_failure(LocalTunnel("model"), "skill isolation")
        self.process.assert_not_called()
        self.connection.assert_not_called()

    def test_api_uses_environment_key_only_when_no_explicit_key(self):
        connection = self.api_response()
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}, clear=True):
            self.generate(APITunnel("model"))
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"], "Bearer " + SECRET)

    def test_api_preserves_working_default_certificate_roots(self):
        self.api_response()
        certifi = Mock()
        with patch.dict(sys.modules, {"certifi": certifi}), patch.dict(os.environ, {}, clear=True):
            self.generate(APITunnel("model", api_key=SECRET))
        self.ssl_factory.assert_called_once_with()
        self.ssl_context.load_verify_locations.assert_not_called()
        certifi.where.assert_not_called()
        self.assertTrue(self.ssl_context.check_hostname)
        self.assertEqual(self.ssl_context.verify_mode, ssl.CERT_REQUIRED)

    def test_api_empty_default_roots_use_optional_certifi(self):
        self.api_response()
        self.ssl_context.cert_store_stats.side_effect = [{"x509_ca": 0}, {"x509_ca": 100}]
        certifi = Mock()
        certifi.where.return_value = "/mock/trusted-ca.pem"
        with patch.dict(sys.modules, {"certifi": certifi}), patch.dict(os.environ, {}, clear=True):
            self.generate(APITunnel("model", api_key=SECRET))
        self.ssl_context.load_verify_locations.assert_called_once_with(cafile="/mock/trusted-ca.pem")
        self.assertTrue(self.ssl_context.check_hostname)
        self.assertEqual(self.ssl_context.verify_mode, ssl.CERT_REQUIRED)

    def test_api_explicit_ca_environment_preserves_lazy_ca_loading(self):
        certifi = Mock()
        for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "/explicit/ca"}, clear=True), \
                    patch.dict(sys.modules, {"certifi": certifi}):
                connection = self.api_response()
                self.ssl_context.cert_store_stats.return_value = {"x509_ca": 0}
                self.assertEqual(self.generate(APITunnel("model", api_key=SECRET)), ANSWER)
                connection.connect.assert_called_once()
        certifi.where.assert_not_called()
        self.ssl_context.load_verify_locations.assert_not_called()
        self.ssl_context.cert_store_stats.assert_not_called()
        self.assertTrue(self.ssl_context.check_hostname)
        self.assertEqual(self.ssl_context.verify_mode, ssl.CERT_REQUIRED)

    def test_api_missing_roots_and_optional_certifi_report_setup_error(self):
        self.ssl_context.cert_store_stats.return_value = {"x509_ca": 0}
        with patch.dict(sys.modules, {"certifi": None}), patch.dict(os.environ, {}, clear=True):
            self.assert_safe_failure(APITunnel("model", api_key=SECRET), "SSL_CERT_FILE")
        self.connection.assert_not_called()

    def test_api_empty_certifi_bundle_is_rejected(self):
        self.ssl_context.cert_store_stats.return_value = {"x509_ca": 0}
        with patch.dict(sys.modules, {"certifi": Mock()}), patch.dict(os.environ, {}, clear=True):
            self.assert_safe_failure(APITunnel("model", api_key=SECRET), "SSL_CERT_FILE")
        self.connection.assert_not_called()

    def test_api_ca_bundle_load_error_is_redacted(self):
        self.ssl_context.cert_store_stats.return_value = {"x509_ca": 0}
        self.ssl_context.load_verify_locations.side_effect = OSError(SECRET)
        with patch.dict(sys.modules, {"certifi": Mock()}), patch.dict(os.environ, {}, clear=True):
            self.assert_safe_failure(APITunnel("model", api_key=SECRET), "SSL_CERT_FILE")
        self.connection.assert_not_called()

    def test_api_tls_verification_failure_is_redacted_and_never_retried(self):
        connection = self.api_response()
        connection.connect.side_effect = ssl.SSLCertVerificationError(1, SECRET)
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "TLS certificate verification failed")
        connection.connect.assert_called_once()
        connection.request.assert_not_called()
        connection.close.assert_called_once()
        self.process.assert_not_called()

    def test_api_missing_key_never_reads_login_or_uses_codex_key(self):
        for env in ({}, {"CODEX_API_KEY": SECRET}):
            with self.subTest(env=list(env)), patch.dict(os.environ, env, clear=True), \
                    patch.object(Path, "read_text", side_effect=AssertionError("auth read forbidden")):
                with self.assertRaisesRegex(TunnelError, "requires OPENAI_API_KEY"):
                    APITunnel("model")
        self.connection.assert_not_called()
        self.process.assert_not_called()

    def test_empty_explicit_key_does_not_fall_back_to_environment(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}):
            with self.assertRaisesRegex(TunnelError, "requires OPENAI_API_KEY"):
                APITunnel("model", api_key="")
        self.connection.assert_not_called()

    def test_header_injection_is_rejected_before_network(self):
        with self.assertRaisesRegex(TunnelError, "authorization header") as raised:
            APITunnel("model", api_key=SECRET + "\r\nInjected: yes")
        self.assertNotIn(SECRET, str(raised.exception))
        self.connection.assert_not_called()

    def test_constructor_rejects_invalid_model_and_timeout(self):
        for constructor in (LocalTunnel, APITunnel):
            for model in (None, "", " ", 7):
                with self.subTest(constructor=constructor, model=model), self.assertRaises(TunnelError):
                    constructor(model)
            for timeout in (0, -1, float("nan"), float("inf"), float("-inf"), True, "12", None, 10 ** 1000):
                with self.subTest(constructor=constructor, timeout=timeout), self.assertRaises(TunnelError):
                    constructor("model", timeout=timeout)

    def test_local_missing_cli_never_uses_api(self):
        self.which.return_value = None
        self.assert_safe_failure(LocalTunnel("model"), "requires the Codex CLI")
        self.connection.assert_not_called()

    def test_local_timeout_is_redacted(self):
        self.process.side_effect = subprocess.TimeoutExpired(SECRET, 1, output=SECRET, stderr=SECRET)
        self.assert_safe_failure(LocalTunnel("model"), "timed out")
        self.connection.assert_not_called()

    def test_local_failure_stderr_is_redacted(self):
        self.local_run(returncode=2, stderr=SECRET)
        self.assert_safe_failure(LocalTunnel("model"), "did not complete successfully")
        self.connection.assert_not_called()

    def test_local_timeout_preserves_private_partial_stream_and_exact_input(self):
        with tempfile.TemporaryDirectory() as directory:
            partial = b'{"type":"error","message":"network unavailable"}\n'
            def timeout(command, **kwargs):
                self.local_kwargs = kwargs
                raise subprocess.TimeoutExpired(command, 1, output=partial, stderr=b'waiting for network')
            self.process.side_effect = timeout
            tunnel = LocalTunnel("model", diagnostic_directory=directory)
            self.assert_safe_failure(tunnel, "timed out")
            request_path, = Path(directory).glob("*.request.json")
            request = json.loads(request_path.read_text())
            self.assertEqual(self.local_kwargs["input"], request["stdin"])
            self.assertEqual(SCHEMA, request["schema"])
            self.assertEqual(tunnel.calls[0]["input_sha256"], request["stdin_sha256"])
            self.assertEqual(0o600, request_path.stat().st_mode & 0o777)
            stdout, = Path(directory).glob("*.stdout")
            stderr, = Path(directory).glob("*.stderr")
            self.assertEqual(partial, stdout.read_bytes())
            self.assertEqual("waiting for network", stderr.read_text())
            self.assertIsNone(tunnel.calls[0]["usage"])
            self.assertNotIn(PACKET["content"], json.dumps(tunnel.calls))

    def test_timeout_receipts_preserve_incomplete_utf8_bytes_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            partial = b'{"incomplete": "' + bytes.fromhex("e282")
            invalid = bytes.fromhex("ff")
            self.process.side_effect = subprocess.TimeoutExpired("mock", 1, output=partial, stderr=invalid)
            tunnel = LocalTunnel("model", diagnostic_directory=directory)
            self.assert_safe_failure(tunnel, "timed out")
            stdout, = Path(directory).glob("*.stdout")
            stderr, = Path(directory).glob("*.stderr")
            self.assertEqual(partial, stdout.read_bytes())
            self.assertEqual(invalid, stderr.read_bytes())

    def test_local_success_exact_input_receipt_survives_later_packet_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            self.local_run()
            tunnel = LocalTunnel("model", diagnostic_directory=directory)
            packet = {"nested": {"value": "initial"}}
            tunnel.generate("stage", "instruction", packet, SCHEMA)
            packet["nested"]["value"] = "later"
            request_path, = Path(directory).glob("*.request.json")
            request = json.loads(request_path.read_text())
            self.assertEqual(self.local_kwargs["input"], request["stdin"])
            self.assertIn('"value": "initial"', request["stdin"])
            self.assertNotIn('"value": "later"', request["stdin"])

    def test_local_opt_in_diagnostics_preserve_failed_event_stream(self):
        self.local_run(events=[{"type": "turn.completed"}, {"type": "error", "message": SECRET}],
                       stderr=SECRET)
        with tempfile.TemporaryDirectory() as directory:
            tunnel = LocalTunnel("model", diagnostic_directory=directory)
            self.assert_safe_failure(tunnel)
            files = sorted(Path(directory).iterdir())
            self.assertEqual({p.suffix for p in files}, {".stdout", ".stderr", ".json"})
            self.assertIn("error", next(Path(directory).glob("*.stdout")).read_text())
            self.assertIn(SECRET, files[0].read_text() + files[1].read_text())
            self.assertTrue(all((p.stat().st_mode & 0o777) == 0o600 for p in files))

    def test_local_opt_in_diagnostics_preserve_ordinary_success(self):
        self.local_run()
        with tempfile.TemporaryDirectory() as directory:
            tunnel = LocalTunnel("model", diagnostic_directory=directory)
            self.assertEqual(self.generate(tunnel), ANSWER)
            self.assertEqual({p.suffix for p in Path(directory).iterdir()}, {".stdout", ".stderr", ".json"})

    def test_local_rejects_missing_duplicate_and_failed_turns(self):
        for events in ([], [{"type": "turn.completed"}, {"type": "turn.completed"}],
                       [{"type": "turn.failed", "error": SECRET}],
                       [{"type": "turn.completed"}, {"type": "error", "message": SECRET}],
                       [{"type": "turn.completed", "status": "incomplete"}]):
            with self.subTest(events=events):
                self.local_run(events=events)
                self.assert_safe_failure(LocalTunnel("model"))

    def test_local_rejects_completed_tool_events(self):
        for item_type in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            with self.subTest(item_type=item_type):
                self.local_run(events=[{"type": "item.completed", "item": {"type": item_type}},
                                       {"type": "turn.completed", "usage": USAGE}])
                tunnel = LocalTunnel("model")
                self.assert_safe_failure(tunnel, "used a tool")
                self.assertEqual(tunnel.calls[0]["usage"], NORMALIZED_USAGE)

    def test_local_rejects_missing_output_and_nonobject_json(self):
        for value in (None, [SECRET], SECRET):
            with self.subTest(value=value):
                self.local_run(result=value)
                self.assert_safe_failure(LocalTunnel("model"))

    def test_local_rejects_invalid_json_without_echoing_it(self):
        def run(command, **kwargs):
            Path(command[command.index("-o") + 1]).write_text(SECRET)
            return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed"}', SECRET)
        self.process.side_effect = run
        self.assert_safe_failure(LocalTunnel("model"), "valid JSON object")

    def test_api_timeout_is_redacted_and_closed_without_fallback(self):
        connection = self.api_response()
        connection.getresponse.side_effect = socket.timeout(SECRET)
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "timed out")
        connection.close.assert_called_once()
        self.process.assert_not_called()

    def test_api_connection_failure_is_redacted(self):
        connection = self.api_response()
        connection.connect.side_effect = OSError(SECRET)
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "connection failed")
        connection.close.assert_called_once()

    def test_api_http_error_does_not_read_or_echo_provider_body(self):
        connection = self.api_response(body=SECRET, status=401)
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "HTTP 401")
        connection.getresponse.return_value.read1.assert_not_called()
        connection.close.assert_called_once()

    def test_api_does_not_follow_redirects(self):
        connection = self.api_response(body=SECRET, status=307)
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "HTTP 307")
        connection.request.assert_called_once()
        self.process.assert_not_called()

    def test_api_refusal_is_redacted(self):
        body = response_envelope()
        body["output"][0]["content"] = [{"type": "refusal", "refusal": SECRET}]
        self.api_response(body)
        tunnel = APITunnel("model", api_key=SECRET)
        self.assert_safe_failure(tunnel, "refused")
        self.assertEqual(tunnel.calls[0]["usage"], NORMALIZED_USAGE)

    def test_api_rejects_incomplete_failed_or_missing_status(self):
        for status in ("incomplete", "failed", "in_progress", None):
            with self.subTest(status=status):
                self.api_response(response_envelope(status=status, incomplete_details={"reason": SECRET}))
                self.assert_safe_failure(APITunnel("model", api_key=SECRET), "not completed")

    def test_api_rejects_incomplete_message(self):
        body = response_envelope()
        body["output"][0]["status"] = "incomplete"
        self.api_response(body)
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "incomplete")

    def test_api_rejects_provider_error_without_details(self):
        self.api_response(response_envelope(error={"message": SECRET}))
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "model error")

    def test_api_rejects_tool_output(self):
        self.api_response(response_envelope(output=[{"type": "function_call", "arguments": SECRET}]))
        self.assert_safe_failure(APITunnel("model", api_key=SECRET), "unexpected output item")

    def test_api_rejects_bad_envelope_json_and_bad_model_json(self):
        for body in (SECRET, "[]", response_envelope(text=SECRET), response_envelope(text="[]"),
                     response_envelope(text='{"number": NaN}')):
            with self.subTest(body_type=type(body).__name__):
                self.api_response(body)
                self.assert_safe_failure(APITunnel("model", api_key=SECRET), "JSON")

    def test_api_response_size_is_bounded(self):
        self.api_response(body=b"x" * 65)
        with patch("newsverify.tunnels.MAX_RESPONSE_BYTES", 64):
            self.assert_safe_failure(APITunnel("model", api_key=SECRET), "size limit")

    def test_local_response_size_is_bounded(self):
        self.local_run()
        with patch("newsverify.tunnels.MAX_RESPONSE_BYTES", 4):
            self.assert_safe_failure(LocalTunnel("model"), "size limit")

    def test_usage_unknown_is_none_and_diagnostics_are_filtered(self):
        for usage, expected in ((None, None), ({"input_tokens": 1, "diagnostic": SECRET},
                {"input_tokens": 1, "output_tokens": None, "cached_input_tokens": None,
                 "reasoning_output_tokens": None})):
            with self.subTest(usage=usage):
                self.api_response(response_envelope(usage=usage))
                tunnel = APITunnel("model", api_key=SECRET)
                self.generate(tunnel)
                self.assertEqual(tunnel.calls[0]["usage"], expected)
                self.assertNotIn(SECRET, json.dumps(tunnel.calls))

    def test_invalid_packet_is_rejected_before_process_or_network(self):
        tunnel = LocalTunnel("model")
        with self.assertRaises(TunnelError):
            tunnel.generate("verify", "Instructions", {"number": float("nan")}, SCHEMA)
        self.assertFalse(tunnel.calls[0]["success"])
        self.process.assert_not_called()
        self.connection.assert_not_called()


class LocalSkillDiscoveryTests(unittest.TestCase):
    def test_nested_system_skills_and_directory_aliases_are_disabled_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            user_home = base / "user"
            codex_root = base / "configured-codex"
            skills = codex_root / "skills"
            system = skills / ".system" / "one"
            agent = user_home / ".agents" / "skills" / "two"
            runtime = base / "external-runtime"
            for directory in (system, agent, runtime):
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text("Instructions must never be read during discovery.")
            (skills / "gstack-alias").symlink_to(runtime, target_is_directory=True)
            (skills / "duplicate-alias").symlink_to(runtime, target_is_directory=True)
            (skills / "cycle").symlink_to(skills, target_is_directory=True)
            (skills / "broken-alias").symlink_to(base / "missing", target_is_directory=True)
            # A skill may contain a large runtime; discovery stops at its manifest.
            (runtime / "nested").mkdir()
            (runtime / "nested" / "SKILL.md").write_text("Not a separately installed skill.")
            with patch.object(Path, "home", return_value=user_home), \
                    patch.object(Path, "read_text", side_effect=AssertionError("skill contents are private")):
                found = _local_skill_paths({"CODEX_HOME": str(codex_root)})
            self.assertEqual(found, tuple(sorted(str((p / "SKILL.md").resolve()) for p in (system, agent, runtime))))

    def test_unreadable_skill_inventory_fails_closed_without_path_details(self):
        with patch.object(Path, "stat", side_effect=PermissionError(SECRET)):
            with self.assertRaisesRegex(TunnelError, "skill isolation") as raised:
                _local_skill_paths({})
        self.assertNotIn(SECRET, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
