#!/usr/bin/env python3
"""Run the head-to-head arms with a mailbox transport: a serving agent answers each call.

This is how a model that has no API tunnel here (a Claude session acting as a
subagent, a person, any agent with file access) can be the model inside the
harness. It is not an API run: no provider usage is reported, so the token rule
cannot be evaluated; call records carry input_chars and output_chars instead.
The serving agent must be allowed to read only the mailbox directory, and the
sealed gold file must live outside anything it can reach.

    python experiments/head_to_head_mailbox.py RUN --cases CASES.json --arm direct --mailbox MAIL
    python experiments/head_to_head_mailbox.py RUN --cases CASES.json --arm harness --mailbox MAIL

Every model call is written as <mailbox>/<arm>/NNNN.request.json
({stage, instructions, packet, schema}). The transport waits for
<mailbox>/<arm>/NNNN.response.json (a JSON object matching the schema) and
returns it to the harness. Usage is not measurable through this transport; the
call record carries input_chars and output_chars instead. A DONE file is written
when the arm has finished so the serving agent can stop.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

import head_to_head  # noqa: E402
from newsverify.tunnels import TunnelError, _Tunnel  # noqa: E402


class MailboxTunnel(_Tunnel):
    kind = "mailbox"

    def __init__(self, mailbox: Path, model: str, reasoning_effort: str = "agent", timeout: float = 1800):
        super().__init__(model, reasoning_effort, timeout)
        self.mailbox = mailbox
        self.mailbox.mkdir(parents=True, exist_ok=True)
        self.counter = len(list(self.mailbox.glob("*.request.json")))

    def _generate(self, instructions, evidence, schema, record):
        self.counter += 1
        stem = f"{self.counter:04d}"
        request = self.mailbox / f"{stem}.request.json"
        response = self.mailbox / f"{stem}.response.json"
        # evidence is the rendered packet text; keep the structured packet too for the agent.
        request.write_text(json.dumps({
            "call": stem, "stage": record["stage"], "instructions": instructions,
            "evidence": evidence, "schema": schema,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while not response.exists():
            if time.monotonic() > deadline:
                raise TunnelError("The mailbox transport timed out waiting for the serving agent.")
            time.sleep(2)
        time.sleep(0.5)  # let the writer finish
        raw = response.read_text(encoding="utf-8")
        record["output_chars"] = len(raw)
        try:
            value = json.loads(raw)
        except ValueError:
            raise TunnelError("The serving agent wrote invalid JSON.") from None
        if not isinstance(value, dict):
            raise TunnelError("The serving agent response must be a JSON object.")
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--arm", choices=("direct", "harness"), required=True)
    parser.add_argument("--mailbox", type=Path, required=True)
    parser.add_argument("--model-label", required=True,
                        help="who is serving the mailbox, recorded as the model, e.g. 'claude-fable-5.1 (serving agent via mailbox)'")
    arguments = parser.parse_args(argv)
    mailbox = arguments.mailbox / arguments.arm
    mailbox.mkdir(parents=True, exist_ok=True)

    def make_transport(tunnel, model, effort, timeout):
        return MailboxTunnel(mailbox, model=arguments.model_label)

    head_to_head.make_transport = make_transport
    head_to_head.TUNNELS = ("mailbox",)
    import newsverify.double_loop as double_loop
    double_loop.check_tunnel = lambda tunnel: None  # the mailbox transport is supplied, never constructed
    try:
        code = head_to_head.main(["run", str(arguments.run), "--cases", str(arguments.cases),
                                  "--arm", arguments.arm, "--tunnel", "mailbox",
                                  "--model", arguments.model_label])
    finally:
        (mailbox / "DONE").write_text("done\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
