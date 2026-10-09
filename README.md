# FactCircuit

> **Trace every claim. Close the evidence loop.**

[![Policy tests](https://github.com/superwesleyhys-ux/factcircuit/actions/workflows/ci.yml/badge.svg)](https://github.com/superwesleyhys-ux/factcircuit/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-2ea44f.svg)](LICENSE)
[![Research preview](https://img.shields.io/badge/status-research_preview-orange.svg)](#project-status)

**FactCircuit turns claim verification from a one-shot label into a replayable
evidence circuit.** Most verification systems return an answer. FactCircuit
preserves the path: immutable source versions, exact evidence spans, staged
semantic checks, unresolved gaps, bounded retrieval tasks, stop reasons,
hashes, and resource usage.

It is an MIT-licensed, adapter-driven Python harness for building and
evaluating auditable verification loops over changing news evidence. The
standard-library core runs offline. Model-backed tracing runs through a local
Codex login by default, with an explicit opt-in OpenAI API route.

This is the canonical repository. The earlier Accuracy Tracing / NewsVerify
Harness milestone is preserved separately in
[factcircuit-development-archive](https://github.com/superwesleyhys-ux/factcircuit-development-archive).

## Why FactCircuit?

News verification fails in ways that a final `true` or `false` label cannot
show. URLs are revised. Syndicated stories look independent. A nearby number,
date, negation, or actor can silently attach to the wrong event. A retrieval
loop can also forget why it searched, reuse stale feedback, or leak material
published after the evaluation cutoff.

FactCircuit makes those failure surfaces explicit:

- **Evidence is versioned, not overwritten.** Same-URL revisions retain
  separate identities, exact source spans, timestamps, and content hashes.
- **Every material return is decomposed.** A document cannot influence the
  evidence graph merely because a retriever found it.
- **Evidence and reality are judged separately.** "Does this snapshot support
  the claim?" is isolated from "Did the event happen in the world?"
- **Source lineage is distinct from semantic support.** Ten copies of one wire
  story are not ten independent sources, and a contradiction does not identify
  the original publisher.
- **Uncertainty becomes work.** Missing information is emitted as an exact
  fetch, search, or reanalysis task and re-enters the same bounded loop.
- **Bad state fails closed.** Invalid schemas, ungrounded spans, duplicate
  probes, stale task receipts, cross-event bindings, and exhausted budgets are
  recorded instead of being partially committed.
- **Evaluation is a first-class feature.** Fixed targets, cutoff checks, gold
  isolation, paired comparisons, bootstrap intervals, and machine-readable
  traces are part of the harness rather than an afterthought.

## The loop

```text
Frozen claim + evidence cutoff
             │
             ▼
    Deterministic TargetPlan
             │
             ▼
retrieval → immutable snapshot → cutoff admission
   ▲                                  │
   │                                  ▼
   │                    atoms → lineage → decomposition critic
   │                                  │
   │                                  ▼
   │                       atomic Python assembly
   │                                  │
   │                    ┌─────────────┴─────────────┐
   │                    ▼                           ▼
   │                 evidence                    world
   │                    ▼                           ▼
   │             evidence critic              world critic
   │                    └─────────────┬─────────────┘
   │                                  ▼
   └──── exact unresolved tasks ← decision + audit trace
                   bounded by rounds, calls, output, and time
```

## Quick start

Start with the pinned **v0.3.2** release. You need Git and Python 3.11 or
newer. Installation downloads build tools; the first example then runs
entirely offline with no API key, model download, or runtime dependencies.

### macOS / Linux

```bash
git clone --branch v0.3.2 --depth 1 https://github.com/superwesleyhys-ux/factcircuit.git
cd factcircuit
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m factcircuit --version
.venv/bin/python -m factcircuit quickstart --output runs/first-run
```

### Windows PowerShell

```powershell
git clone --branch v0.3.2 --depth 1 https://github.com/superwesleyhys-ux/factcircuit.git
cd factcircuit
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe -m factcircuit --version
.venv\Scripts\python.exe -m factcircuit quickstart --output runs/first-run
```

Expected terminal output:

```text
factcircuit 0.3.2
Offline annotated example: contradicted; 3 rounds; 0 model calls.
Read runs/first-run/SUMMARY.md
```

Open `runs/first-run/SUMMARY.md`. The same directory contains `inputs.json`,
`config.json`, and `trace.json`. Four fictional evidence versions pass through
three retrieval rounds, including source tracing and a publisher correction.
The semantic annotations are hand-authored; this checks the working evidence
loop, not model accuracy. Existing output directories are never overwritten.

See the [first-run guide](docs/QUICKSTART.md) for configuration, expected
fields, troubleshooting, and running your own claims with a local model.

### Offline commands

Everything below runs in the local Python process with no API key, HTTP
service, model SDK, or remote inference. Source URLs are audit metadata only
and are never fetched.

```bash
# Trace supplied snapshots; text is preserved, fact status stays not_checked
python3 -m factcircuit trace examples/local_trace.json --output reports/local-trace.json

# Four material versions over three retrieval rounds with hand-authored judgments
python3 -m factcircuit trace-demo --output reports/trace-demo-local.json

# Every subcommand documents itself
python3 -m factcircuit --help

# Score fixed predictions against separate gold labels
python3 -m factcircuit score examples/evaluation_gold.json \
  examples/evaluation_predictions.json --output reports/all-metrics-local.json

# Paired comparison with event-cluster bootstrap intervals
python3 -m factcircuit compare examples/evaluation_gold.json \
  examples/comparison_baseline.json examples/comparison_candidate.json \
  --bootstrap-samples 100 --seed 0 --output reports/comparison-local.json

# Regression suite
python3 -m unittest discover -s tests -v
```

The bundled predictions are deliberately imperfect handwritten fixtures for
checking arithmetic. They are not model-performance results.

### Model-backed tracing

`trace-model`, `trace-news`, `early-risk`, and `double-loop` run the same
decomposition and verification adapters through one of three tunnels.
**Local is the default**: it runs the installed Codex CLI with its existing
login and uses `gpt-6-astra` unless `--model` or `FACTCIRCUIT_MODEL` selects
another locally available model. Local orchestration does not mean offline
weights; the hosted model is still reached through Codex. `--tunnel api` uses
the OpenAI Responses API (`OPENAI_API_KEY`, `--model` or `OPENAI_MODEL`);
`--tunnel anthropic` uses the Anthropic Messages API (`ANTHROPIC_API_KEY`,
`--model` or `ANTHROPIC_MODEL`), so Claude models can run the same harness.
No tunnel falls back to another.

```bash
# Extract claims and verify facts over supplied snapshots
python3 -m factcircuit trace-model examples/model_trace.json --output reports/model-local.json
python3 -m factcircuit trace-model examples/model_trace.json --tunnel api --model YOUR_API_MODEL --output reports/model-api.json

# Fetch a live article, follow its upstream links, report origin and support per claim
python3 -m factcircuit trace-news examples/news_tracing.json --output reports/news-trace.json

# Single-pass provenance-risk assessment
python3 -m factcircuit early-risk examples/early_risk_case.json --reasoning-effort low --output reports/early-risk.json

# Double loop: model-selected follow-up retrieval from a fixed local snapshot pool
python3 -m factcircuit double-loop examples/double_loop_case.json --output reports/double-loop.json --max-model-calls 10
```

Reports record `execution.tunnel`, model settings, and measured usage for every
call. Failed calls remain errors in the selected path. Exit status is 1 for an
audited execution failure and 2 for invalid input or unavailable configuration,
including a missing Codex CLI or API key, which is reported before any model
work starts. Input fields are validated by name: a missing or unknown field in
`target`, a material, or `config` names the field in the error. `trace-news`
uses live public pages; finding an original source does not by itself
establish truth. See [model execution tunnels](docs/MODEL_TUNNELS.md)
and [news tracing](docs/NEWS_TRACING.md) for budgets, snapshots, and
limitations.

The double-loop runner adds model-generated source relations, evidence gaps,
gap resolutions, and requests to reopen earlier analyses. Each immutable
version is decomposed and verified once per claim; prior findings and open
gaps remain in later context. It does not search the open web, and the call
cap covers selection, decomposition, and verification together.

### Fresh local inference and archived replay

The experimental source-checkout scripts under `experiments/` run the staged
verifier against a local GGUF model (`scripts/configure_local.py`, then
`experiments/local_inference.py`) or replay archived model responses
deterministically with no network (`experiments/local_replay.py`). The
historical cutoff contract can be audited offline with
`experiments/historical_compare.py audit`. See the
[first-run guide](docs/QUICKSTART.md) and the
[historical 2023 benchmark contract](docs/HISTORICAL_2023_BENCHMARK.md).

### Head-to-head: bare model versus harness

`experiments/head_to_head.py` runs one model through one tunnel in two arms,
direct single call and double-loop harness, over a frozen case pool with the
gold file sealed by hash before inference, and declares a win only by a
registered rule (strictly higher accuracy, every output valid, tokens within a
registered ratio). See the [protocol](docs/HEAD_TO_HEAD.md). The first
run, [dev-001](reports/head-to-head-dev-001/SUMMARY.md), is a three-case
synthetic mechanics check with Astra: 3/3 against 3/3, and 4.34× the
provider-reported tokens, most of it the Codex CLI's fixed per-call prompt
paid on 5 calls instead of 1. On the eight-case synthetic dev pool
([dev-002a](reports/head-to-head-dev-002a/SUMMARY.md),
[dev-002b](reports/head-to-head-dev-002b/SUMMARY.md)) the bare model scored
8/8 both times; the harness scored 7/8 with early verification and 8/8 with
verification deferred until the provenance chain is complete, which is now
the double loop's default. None of this establishes accuracy. The sections
below are the earlier, mostly corrected, comparisons.

## What the experiments show so far

All published runs are small, and most of them are corrections of earlier
over-claims. They are kept public so a failed test cannot be silently
discarded.

### Live source tracing (September 14, V12)

Starting from two raw news articles, both direct Astra and the harness
retrieved archived sources available by December 31, 2024 and reached both
target original papers. Independent audits confirmed **2/2 native harness
source chains** with no repeated full-text review inside native decomposition
or verification. All 46 Astra calls succeeded; direct Astra used 896,837
tokens, the harness 2,728,294. Withdrawal-risk categories matched. This is a
source-tracing and bookkeeping result on two repeatedly used development cases.
It does **not** establish better fake-news detection accuracy. See the
[V12 report, per-case audits, token receipts, and prior failed rounds](reports/live-origin-v12-20260914/README.md).

### Unequal-evidence comparisons (corrected September 14)

The v5, v4, and earlier source-trace comparison runners send frozen text
packets to a model and do not run an automated retrieval stage. Their
candidate arms received extra curated concern/correction passages that the
direct arms did not. The earlier description of these scores as proof of an
automated source-tracing advantage was unsupported. The recorded outputs are
preserved:

| Run | Model | Direct matches | Candidate matches | Evidence design |
|---|---|---:|---:|---|
| [v5](reports/honest-system-holdout-v5-astra-20260912/scored-v1/README.md) | Astra | 4/8 | 8/8 | Extra curated traces for candidate |
| [v4](reports/honest-system-holdout-v4-20260911/scored-v1/README.md) | Luna | 4/8 | 8/8 | Extra curated traces for candidate |
| [Earlier run](reports/honest-system-holdout-20260911/scored-v1/README.md) | Luna | 4/8 | 8/8 | Extra curated traces for candidate |

These measure responses to different supplied evidence, not independent source
finding or representative accuracy. The
[v3 infrastructure failure](reports/honest-system-holdout-v3-20260911/POSTMORTEM.md)
is preserved and excluded from valid-win claims.

### Invalidated early-risk diagnostic (September 11)

`gpt-5.6-luna` alone versus the same model inside the single-pass
provenance-risk harness scored 2/8 versus 8/8 on strict-cutoff fact accuracy
and 0/4 versus 4/4 on later-false cases, at 0.39× the tokens. **This result is
invalid as evidence of advance fabrication detection**: the policy mapped
unauthenticated provenance claims to high risk while every negative control
asked only what a paper reported, so the label was recoverable from the claim
type. The [full record](reports/early-risk-total-20260911/README.md) stays
published. A replacement test needs unseen event families and same-task
provenance controls frozen before inference.

### Historical 2023 pilot (original vs staged loop)

On a frozen two-case pilot using 2023 propositions and official outcomes
available by the end of 2024, the staged loop resolved 2/2 and the monolithic
loop 1/2, at 5.5× the model calls and 3.17× the tokens. A separate
full-evidence control exposed a staged-path error and left the run as
`has_errors`. See the
[summary](reports/historical-2023-pilot2-v3-live-001/SUMMARY.md) and
[scores](reports/historical-2023-pilot2-v3-live-001/scores.json).

Earlier September 8 comparisons (six constructed Astra cases, a two-paper
historical-cutoff test, and a news-tracing integration run) are described in
the protocols under `experiments/`, but their report artifacts are not
published in this repository and are not relied on here.

## Integration surface

```python
from factcircuit.provenance import run_provenance

run_provenance(target, provider, decomposer=None, verifier=None, config=None)
```

Typed provider, decomposer, and verifier contracts are documented in
[`docs/TRACE_ADAPTER.md`](docs/TRACE_ADAPTER.md). The engine keeps immutable
material versions with exact spans; decomposes every valid return before graph
or verifier admission; keeps same-URL revisions as distinct identities;
distinguishes direct, declared, inferred, unresolved, and excluded relations;
requires an explicit finding plus a direct lineage path before an original
source is considered complete; triggers reanalysis through `revisit_versions`;
and enforces round, material, and decomposition budgets with explicit
no-progress and error results. Historical admission requires an exact version
availability declaration; it is not proof against a pretrained model already
knowing later events.

## Evaluation toolkit

Gold labels and predictions live in separate files. Missing, extra, or
duplicate target IDs; cutoff mismatches; unjudged evidence; and malformed
probability vectors fail validation.

| Metric | What it measures |
| --- | --- |
| VP | Precision of claims admitted to a trusted feed |
| FR | Fraction of false claims withheld |
| TR | Fraction of true claims admitted |
| SR | Correct original-root sets with valid provenance paths |
| EN | Precision of evidence asserted to support a target |
| CA | Probability quality using a normalized four-class Brier score |
| HFAR | High-risk false claims incorrectly admitted |

The scorer also reports source precision, evidence recall, edge
precision/recall, duplicate-pair F1, confusion matrices, coverage, ECE, and an
experimental `NVScore` whose weights are not empirically validated. `compare`
checks declared equal model/corpus/budget settings and produces paired
event-cluster bootstrap intervals; it cannot attest that external usage logs
are authentic. Scores measure agreement with supplied gold; they do not
establish truth by themselves.

## Project status

Version 0.3.2 is the current release of this **research preview**; the local
Astra default, incremental double loop, and news-tracing integration are in
the unreleased changes listed in the [changelog](CHANGELOG.md). GitHub Actions
runs the regression suite on Python 3.11, 3.12, and 3.13 and the installed
first-run command on Linux, macOS, and Windows.

### Name and compatibility

Version 0.3.1 renamed the project and Python distribution from Accuracy
Tracing / NewsVerify Harness to **FactCircuit**. New integrations should use
the `factcircuit` import package and command. The `newsverify` imports and
command remain supported as compatibility entry points, and the original
v0.2/v0.3 reports retain their historical names. `python -m factcircuit demo`,
`verify`, and `benchmark` run the v0.1 annotated-evidence policy runner; its
22 synthetic scenarios are regression tests, not real-news accuracy estimates.

Current boundaries are deliberate and visible:

- The bundled provider searches supplied snapshots; only `trace-news` opens
  live public pages, and it does not authenticate them independently.
- Present-day model weights cannot be rolled back to an earlier knowledge
  cutoff. The historical harness isolates supplied evidence, not model memory.
- Internal hashes prove repository consistency, not third-party authenticity
  of a remote source or API response.
- No published run establishes a general accuracy or advance
  fabrication-detection advantage. A blind, event-separated benchmark with
  same-task controls and an equal-compute ablation remain roadmap work.

## Documentation

- [FactCircuit specification index](docs/FACTCIRCUIT_SPEC.md)
- [Installation, configuration, and complete first run](docs/QUICKSTART.md)
- [Model execution tunnels](docs/MODEL_TUNNELS.md)
- [Head-to-head protocol](docs/HEAD_TO_HEAD.md)
- [News tracing](docs/NEWS_TRACING.md)
- [Trace adapter API](docs/TRACE_ADAPTER.md)
- [Evaluation schema](docs/EVALUATION_SCHEMA.md)
- [Staged validation loop](docs/STAGED_VALIDATION_LOOP.md)
- [Historical 2023 benchmark contract](docs/HISTORICAL_2023_BENCHMARK.md)
- [Original design specification and metric definitions](docs/ACCURACY_TRACING_SPEC.md)
- [v0.3.2 release notes](docs/releases/v0.3.2.md) and
  [validation record](reports/VALIDATION_V0.3.2.md)
- [Roadmap](docs/ROADMAP.md)
- [Changelog](CHANGELOG.md)

## Community

Small, reproducible contributions are welcome. Start with
[`CONTRIBUTING.md`](CONTRIBUTING.md), open an issue for a defect or proposal,
or use the [Accuracy decline discussion category](https://github.com/superwesleyhys-ux/factcircuit/discussions/categories/accuracy-decline)
to report a measurable regression with its corpus, configuration, budgets, and
trace artifacts. Security-sensitive findings should follow
[`SECURITY.md`](SECURITY.md).

## License

Released under the [MIT License](LICENSE).
