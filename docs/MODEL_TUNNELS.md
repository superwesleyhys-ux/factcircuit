# Model execution tunnels

The harness runs locally and provides two model execution paths. Model-backed
execution uses `trace-model`; its default tunnel is `local`.

| Setting | Local tunnel | API tunnel |
| --- | --- | --- |
| Selection | Default or `--tunnel local` | Explicit `--tunnel api` |
| Execution | Local `codex exec` subprocess | HTTPS OpenAI Responses API |
| Authentication | Existing Codex login | `OPENAI_API_KEY` environment variable |
| Inference | May use a hosted model through Codex | Hosted model |
| Extra Python dependencies | None | None with system CA roots; optional `certifi` if roots are missing |
| Fallback to another tunnel | Never | Never |

```bash
python -m newsverify trace-model examples/model_trace.json --tunnel local --output reports/model-local.json
python -m newsverify trace-model examples/model_trace.json --tunnel api --model YOUR_API_MODEL --output reports/model-api.json
```

Both commands accept the same local JSON schema (`target`, `rounds`, optional
`config`). `--model` and `--reasoning-effort` select model settings.
Local model selection uses `--model`, then `FACTCIRCUIT_MODEL`, then
`gpt-6-astra`. It does not inherit the global Codex model. For the local path, omitted reasoning
effort is read from the Codex configuration, with `medium` as the fallback.
The API path does not read that configuration and defaults to `medium`.
The API path requires `--model` or `OPENAI_MODEL` for a model available to that
API account; Codex access does not establish API access.
Provide API credentials through the environment, never through fixture files.

HTTPS certificate and hostname verification remain enabled. The API path uses
Python's default trust store. If it is empty and no explicit certificate override
is set, an already installed `certifi` bundle is used when available. Otherwise
configure `SSL_CERT_FILE` with a trusted CA bundle. Explicit `SSL_CERT_FILE` or
`SSL_CERT_DIR` settings are respected; errors never disable TLS verification.

`--timeout` is a positive per-call timeout in seconds, default 180. Harness
round/document/decomposition limits bound orchestration; they do not constitute
a total token or monetary ceiling. The harness does not retry logical calls;
the installed CLI may perform internal transport retries.

## Shared behavior

`model_runner.py` supplies the same decomposition and verification prompts,
packets, schemas and validation to either tunnel. Each eligible material is
decomposed before verification. Exact quoted spans are located in the preserved
original text; missing, ambiguous or invented quotes fail validation. Definite
verdicts require evidence under the core contract.

Ineligible material still passes through conservative local decomposition for
the audit. Its content is not sent to the semantic model and its analysis does
not enter subsequent verification. Gold labels and extra fixture fields are
not routed to model packets.

The local tunnel starts isolated, ephemeral Codex calls with tools disabled and
rejects tool activity. It also disables discovered personal and system skills
for each subprocess: `--ignore-user-config` alone does not remove installed
skill descriptions. These overrides preserve installed skills and the existing
login. Built-in Codex instructions remain, so a local baseline is not a bare
base-model API request. The API tunnel creates independent Responses requests
with structured output and `store: false`. It does not reuse a conversation or
send Codex login credentials. API refusals, incomplete outputs and HTTP failures
are explicit errors. This follows the official
[Structured Outputs contract](https://developers.openai.com/api/docs/guides/structured-outputs)
and the local
[Codex non-interactive interface](https://learn.chatgpt.com/docs/non-interactive-mode).

Both paths perform claim extraction and fact verification. Automatic provenance
relations, origin resolution and active follow-up retrieval are not implemented
in these semantic adapters. A supported fact can therefore retain `partial`
provenance and open source questions. This is separate from the full core's
plugin capability. The opt-in `run_double_loop_trace` adapter in
`newsverify/double_loop.py` adds model-selected follow-up retrieval and source
reanalysis over a finite material pool. Its fresh gstack-guided comparison is
documented in the [public evaluation report](../reports/model-evaluation-20260908/README.md).

## Reports and errors

The ordinary trace report additionally contains:

```json
{
  "execution_mode": "model_trace",
  "execution": {
    "tunnel": "local",
    "model": "configured-model-id",
    "reasoning_effort": "medium",
    "model_calls": []
  }
}
```

Each recorded call includes stage, elapsed time, outcome and available token
usage. Unknown usage is not silently replaced with zero. Model settings and
usage are execution records, not an independent model-version attestation or
an actual bill. Local and API environments can have different model instructions
and availability even when their requested model IDs match.

The CLI returns 0 for a completed run, including a legitimate unresolved verdict;
1 when the saved trace contains an execution error; and 2 for invalid input or
configuration, such as a missing API key. No error silently changes the selected
tunnel. Reports never include authorization headers, API keys or raw HTTP error
bodies.

For fully offline operation, continue using:

```bash
python -m newsverify trace examples/local_trace.json --output reports/local-trace.json
```

That command preserves snapshots without model inference and reports factual
status as `not_checked`.

## Verification

Run `python -m unittest discover -s tests -v` for transport, routing, failure and
harness regression checks. Transport tests replace network/subprocess execution
with controlled responses. Live smoke results, when available, are recorded in
`reports/TWO_TUNNELS_VALIDATION.md`; synthetic examples establish integration,
not real-news accuracy or equal-compute superiority.
