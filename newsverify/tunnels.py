"""Explicit local-CLI and Responses API transports for the same harness prompts.

The local transport uses the installed Codex login; it does not imply offline
model weights. Neither transport retries or falls back to the other transport.
Only safe metadata is retained in ``calls``; prompts and raw errors are not.
"""

from __future__ import annotations

import http.client
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
from typing import Any


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024 * 1024
_API_HOST = "api.openai.com"
_API_PATH = "/v1/responses"
_USAGE_FIELDS = {
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
    "cached_tokens", "reasoning_tokens", "reasoning_output_tokens", "input_tokens_details",
    "output_tokens_details",
}


class TunnelError(ValueError):
    """A safe transport failure that does not contain provider diagnostic text."""


def _verified_ssl_context() -> ssl.SSLContext:
    """Use system trust, with optional certifi for Python installs without roots."""
    message = "The API tunnel has no usable TLS CA bundle; set SSL_CERT_FILE to a trusted CA bundle."
    try:
        context = ssl.create_default_context()
        # OpenSSL can load directory certificates lazily during verification.
        # Explicit trust settings must be checked by the verified handshake.
        if "SSL_CERT_FILE" in os.environ or "SSL_CERT_DIR" in os.environ:
            return context
        if context.cert_store_stats().get("x509_ca", 0) == 0:
            try:
                import certifi
            except ImportError:
                pass
            else:
                context.load_verify_locations(cafile=certifi.where())
            if context.cert_store_stats().get("x509_ca", 0) == 0:
                raise TunnelError(message)
    except (OSError, ValueError) as exc:
        if isinstance(exc, TunnelError):
            raise
        raise TunnelError(message) from None
    return context


def _numeric_usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = {}
    for key, item in value.items():
        if key not in _USAGE_FIELDS:
            continue
        if isinstance(item, dict):
            result[key] = _numeric_usage(item)
        elif isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result[key] = item
    return result or None


def _usage(value: Any) -> dict | None:
    result = _numeric_usage(value)
    if result is None:
        return None
    input_details = result.get("input_tokens_details") or {}
    output_details = result.get("output_tokens_details") or {}
    result.setdefault("input_tokens", None)
    result.setdefault("output_tokens", None)
    result.setdefault("cached_input_tokens", input_details.get("cached_tokens"))
    result.setdefault("reasoning_output_tokens", output_details.get("reasoning_tokens", result.get("reasoning_tokens")))
    return result


def _reject_nonfinite(_: str) -> None:
    raise ValueError("Nonfinite JSON number")


def _decode_object(raw: str | bytes, error: str) -> dict:
    try:
        value = json.loads(raw, parse_constant=_reject_nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise TunnelError(error) from None
    if not isinstance(value, dict):
        raise TunnelError(error)
    return value


def _prompt_parts(instructions: str, packet: dict) -> tuple[str, str]:
    if not isinstance(instructions, str) or not isinstance(packet, dict):
        raise TunnelError("Instructions must be text and the evidence packet must be an object.")
    try:
        evidence = json.dumps(packet, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise TunnelError("The evidence packet must contain valid JSON values.") from None
    return instructions, "EVIDENCE PACKET:\n" + evidence


class _Tunnel:
    kind: str

    def __init__(self, model: str, reasoning_effort: str = "medium", timeout: float = 180):
        if not isinstance(model, str) or not model.strip():
            raise TunnelError("A nonempty model name is required.")
        if not isinstance(reasoning_effort, str) or not reasoning_effort.strip():
            raise TunnelError("A nonempty reasoning effort is required.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TunnelError("Timeout must be a finite positive number of seconds.")
        try:
            timeout = float(timeout)
        except OverflowError:
            raise TunnelError("Timeout must be a finite positive number of seconds.") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise TunnelError("Timeout must be a finite positive number of seconds.")
        self.model = model.strip()
        self.reasoning_effort = reasoning_effort.strip()
        self.timeout = timeout
        self.calls: list[dict] = []

    def generate(self, stage: str, instructions: str, packet: dict, schema: dict) -> dict:
        started = time.perf_counter()
        record = {
            "stage": stage, "tunnel": self.kind, "model": self.model,
            "reasoning_effort": self.reasoning_effort, "timeout_seconds": self.timeout,
            "wall_seconds": 0.0, "success": False, "status": "running", "usage": None,
        }
        self.calls.append(record)
        try:
            instructions, evidence = _prompt_parts(instructions, packet)
            if not isinstance(schema, dict):
                raise TunnelError("The output schema must be a JSON object.")
            try:
                json.dumps(schema, allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                raise TunnelError("The output schema must contain valid JSON values.") from None
            value = self._generate(instructions, evidence, schema, record)
        except TunnelError as exc:
            record.update(status="failed", error=str(exc))
            raise
        except Exception:
            # Exception details can include credentials, evidence, or provider stderr.
            message = f"The {self.kind} tunnel failed; no fallback was attempted."
            record.update(status="failed", error=message)
            raise TunnelError(message) from None
        else:
            record.update(success=True, status="completed")
            return value
        finally:
            record["wall_seconds"] = time.perf_counter() - started

    def _generate(self, instructions: str, evidence: str, schema: dict, record: dict) -> dict:
        raise NotImplementedError


def _local_skill_paths(env: dict[str, str]) -> tuple[str, ...]:
    """Find personal skill manifests without reading instructions or following cycles.

    ``--ignore-user-config`` does not disable skill discovery in Codex. Project
    skills are excluded by the temporary working directory and plugin skills by
    the plugins flag; personal and bundled system skills need explicit overrides.
    A directory containing SKILL.md is one skill, so do not traverse its runtime.
    """
    user_home = Path.home()
    codex_home = Path(env.get("CODEX_HOME", str(user_home / ".codex"))).expanduser()
    pending = [codex_home / "skills", user_home / ".agents" / "skills"]
    visited: set[tuple[int, int]] = set()
    paths: set[str] = set()
    try:
        while pending:
            directory = pending.pop()
            try:
                info = directory.stat()
            except FileNotFoundError:
                continue
            identity = (info.st_dev, info.st_ino)
            if not stat.S_ISDIR(info.st_mode) or identity in visited:
                continue
            visited.add(identity)
            manifest = directory / "SKILL.md"
            try:
                manifest_info = manifest.stat()
            except FileNotFoundError:
                manifest_info = None
            if manifest_info is not None and stat.S_ISREG(manifest_info.st_mode):
                paths.add(str(manifest.resolve(strict=True)))
                continue
            for entry in directory.iterdir():
                try:
                    entry_info = entry.stat()
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(entry_info.st_mode):
                    pending.append(entry)
    except (OSError, RuntimeError):
        raise TunnelError("Local skill isolation could not be established; no model call was started.") from None
    return tuple(sorted(paths))


class LocalTunnel(_Tunnel):
    """Run a bounded Codex turn with the installed CLI and its existing login."""

    kind = "local"

    def __init__(self, model: str, reasoning_effort: str = "medium", timeout: float = 180,
                 diagnostic_directory: str | Path | None = None):
        super().__init__(model, reasoning_effort, timeout)
        if diagnostic_directory is None:
            self.diagnostic_directory = None
        else:
            path = Path(diagnostic_directory)
            if not path.is_absolute():
                raise TunnelError("Diagnostic directory must be an absolute path.")
            self.diagnostic_directory = path
            try:
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not stat.S_ISDIR(path.stat().st_mode):
                    raise OSError
                os.chmod(path, 0o700)
            except OSError:
                raise TunnelError("Diagnostic directory could not be established.") from None

    def _save_diagnostics(self, call_number: int, stdout: object, stderr: object) -> None:
        if self.diagnostic_directory is None:
            return
        def raw_bytes(value: object) -> bytes:
            if isinstance(value, bytes):
                return value
            return value.encode("utf-8") if isinstance(value, str) else b""
        stem = f"{call_number:04d}-{time.time_ns()}"
        try:
            for suffix, value in (("stdout", stdout), ("stderr", stderr)):
                target = self.diagnostic_directory / f"{stem}.{suffix}"
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw_bytes(value))
        except OSError:
            raise TunnelError("The local diagnostic receipt could not be written.") from None

    def _save_request(self, call_number: int, stdin: str, schema: dict) -> str | None:
        """Persist exact submitted input only in the opt-in private receipt directory."""
        if self.diagnostic_directory is None:
            return None
        digest = hashlib.sha256(stdin.encode("utf-8")).hexdigest()
        target = self.diagnostic_directory / f"{call_number:04d}-{time.time_ns()}.request.json"
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"stdin": stdin, "stdin_sha256": digest, "schema": schema,
                           "model": self.model, "reasoning_effort": self.reasoning_effort},
                          stream, ensure_ascii=False, allow_nan=False)
        except OSError:
            raise TunnelError("The local input receipt could not be written.") from None
        return digest

    def _generate(self, instructions: str, evidence: str, schema: dict, record: dict) -> dict:
        executable = shutil.which("codex")
        if not executable:
            raise TunnelError("The local tunnel requires the Codex CLI on PATH.")
        env = {key: value for key, value in os.environ.items()
               if key not in {"OPENAI_API_KEY", "CODEX_API_KEY"}}
        skill_paths = _local_skill_paths(env)
        skills_override = "skills.config=[" + ",".join(
            "{path=" + json.dumps(path) + ",enabled=false}" for path in skill_paths
        ) + "]"
        record["local_skills_disabled"] = len(skill_paths)
        with tempfile.TemporaryDirectory(prefix="newsverify-model-") as isolated:
            folder = Path(isolated)
            schema_path = folder / "schema.json"
            output_path = folder / "response.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            command = [
                executable, "exec", "--ignore-user-config", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "-C", isolated,
                "-m", self.model, "-c", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}",
                "-c", skills_override,
                "-c", 'web_search="disabled"', "--disable", "shell_tool",
                "--disable", "apps", "--disable", "plugins", "--disable", "multi_agent",
                "--json", "--output-schema", str(schema_path), "-o", str(output_path), "-",
            ]
            stdin = instructions + "\n" + evidence
            input_hash = self._save_request(len(self.calls), stdin, schema)
            if input_hash is not None:
                record["input_sha256"] = input_hash
            try:
                result = subprocess.run(
                    command, input=stdin, text=True,
                    capture_output=True, timeout=self.timeout, env=env,
                )
            except subprocess.TimeoutExpired as exc:
                self._save_diagnostics(len(self.calls), exc.stdout, exc.stderr)
                raise TunnelError("The local model call timed out; no fallback was attempted.") from None
            except OSError:
                raise TunnelError("The Codex CLI could not be started.") from None
            record["exit_code"] = result.returncode
            self._save_diagnostics(len(self.calls), result.stdout, result.stderr)
            if result.returncode:
                raise TunnelError("The Codex CLI did not complete successfully.")
            if len(result.stdout.encode("utf-8")) > MAX_EVENT_BYTES:
                raise TunnelError("The local model event stream exceeded the size limit.")
            events = []
            for line in result.stdout.splitlines():
                if line.lstrip().startswith("{"):
                    events.append(_decode_object(line, "The local model returned invalid event JSON."))
            turns = [event for event in events if event.get("type") == "turn.completed"]
            if turns:
                record["usage"] = _usage(turns[-1].get("usage"))
            if any(event.get("type") in {"error", "turn.failed", "turn.cancelled", "turn.incomplete"}
                   for event in events):
                raise TunnelError("The local model turn failed or was incomplete.")
            if len(turns) != 1 or turns[0].get("status", "completed") != "completed":
                raise TunnelError("The local model did not return exactly one completed turn.")
            for event in events:
                if event.get("type") == "item.completed":
                    item = event.get("item")
                    if not isinstance(item, dict) or item.get("type") not in {"agent_message", "reasoning"}:
                        raise TunnelError("The local model used a tool; closed-packet execution was rejected.")
                    if item.get("status", "completed") != "completed":
                        raise TunnelError("The local model returned an incomplete output item.")
                    if item.get("refusal") or item.get("type") == "refusal":
                        raise TunnelError("The local model refused the request.")
            try:
                if output_path.stat().st_size > MAX_RESPONSE_BYTES:
                    raise TunnelError("The local model response exceeded the size limit.")
                raw = output_path.read_bytes()
            except OSError:
                raise TunnelError("The local model did not produce a readable response.") from None
            return _decode_object(raw, "The local model response must be a valid JSON object.")


class APITunnel(_Tunnel):
    """Call the fixed OpenAI Responses endpoint using an explicit API credential."""

    kind = "api"

    def __init__(self, model: str, reasoning_effort: str = "medium", timeout: float = 180,
                 api_key: str | None = None):
        super().__init__(model, reasoning_effort, timeout)
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise TunnelError("The API tunnel requires OPENAI_API_KEY or an explicitly supplied API key.")
        if "\r" in key or "\n" in key:
            raise TunnelError("The API credential is not valid for an authorization header.")
        self._api_key = key.strip()

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TunnelError("The API model call timed out; no fallback was attempted.")
        return remaining

    def _set_timeout(self, connection: http.client.HTTPSConnection, deadline: float) -> None:
        remaining = self._remaining(deadline)
        connection.timeout = remaining
        if connection.sock is not None:
            connection.sock.settimeout(remaining)

    def _generate(self, instructions: str, evidence: str, schema: dict, record: dict) -> dict:
        payload = {
            "model": self.model, "instructions": instructions, "input": evidence,
            "reasoning": {"effort": self.reasoning_effort}, "store": False,
            "text": {"format": {
                "type": "json_schema", "name": "harness_result", "strict": True, "schema": schema,
            }},
        }
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        connection = None
        deadline = time.perf_counter() + self.timeout
        try:
            context = _verified_ssl_context()
            connection = http.client.HTTPSConnection(_API_HOST, timeout=self.timeout, context=context)
            connection.connect()
            self._set_timeout(connection, deadline)
            connection.request("POST", _API_PATH, body=body, headers={
                "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json",
                "Accept": "application/json",
            })
            self._set_timeout(connection, deadline)
            response = connection.getresponse()
            record["http_status"] = response.status
            if not 200 <= response.status < 300:
                raise TunnelError(f"The API request failed with HTTP {response.status}; no fallback was attempted.")
            chunks = []
            size = 0
            while True:
                self._set_timeout(connection, deadline)
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise TunnelError("The API response exceeded the size limit.")
            result = _decode_object(b"".join(chunks), "The API returned invalid response JSON.")
            self._remaining(deadline)
        except (socket.timeout, TimeoutError):
            raise TunnelError("The API model call timed out; no fallback was attempted.") from None
        except ssl.SSLCertVerificationError:
            raise TunnelError("API TLS certificate verification failed; set SSL_CERT_FILE to a trusted CA bundle.") from None
        except ssl.SSLError:
            raise TunnelError("The API TLS connection failed; check the TLS trust configuration and SSL_CERT_FILE.") from None
        except (OSError, http.client.HTTPException):
            raise TunnelError("The API connection failed; no fallback was attempted.") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        record["usage"] = _usage(result.get("usage"))
        if result.get("error") is not None:
            raise TunnelError("The API response reported a model error.")
        if result.get("status") != "completed" or result.get("incomplete_details") is not None:
            raise TunnelError("The API model response was not completed.")
        output = result.get("output")
        if not isinstance(output, list):
            raise TunnelError("The API response did not contain model output.")
        texts = []
        for item in output:
            if not isinstance(item, dict):
                raise TunnelError("The API response contained an invalid output item.")
            if item.get("type") == "reasoning":
                if item.get("status", "completed") != "completed":
                    raise TunnelError("The API model returned an incomplete reasoning item.")
                continue
            if item.get("type") != "message":
                raise TunnelError("The API model returned an unexpected output item.")
            if item.get("status") != "completed" or item.get("role") != "assistant":
                raise TunnelError("The API model returned an incomplete or invalid message.")
            content = item.get("content")
            if not isinstance(content, list):
                raise TunnelError("The API model returned invalid message content.")
            for part in content:
                if not isinstance(part, dict):
                    raise TunnelError("The API model returned invalid message content.")
                if part.get("type") == "refusal":
                    raise TunnelError("The API model refused the request.")
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise TunnelError("The API model returned unexpected message content.")
                texts.append(part["text"])
        if not texts:
            raise TunnelError("The API model did not return response text.")
        return _decode_object("".join(texts), "The API model response must be a valid JSON object.")
