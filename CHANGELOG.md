# Changelog

All notable changes to FactCircuit are documented here. The project uses
semantic versioning for its public releases.

## Unreleased

- Added `TraceConfig.defer_verification_until_provenance_complete` (default
  off): while a blocking provenance gap is open and retrieval can still act
  on it, the round skips verification; the verifier runs once the gap is
  resolved or retrieval can go no further. Saves one model call per fetched
  document in the double loop. Tunnel call records now carry `input_chars`.
- `head_to_head.py` records the resolved model, saves per-case detail files
  (full harness report; direct packet and raw response), accepts registered
  `--harness-config` overrides for A/B runs, and reports model calls, mean
  input tokens per call and a packet-only estimate beside the provider-token
  rule. Added `examples/head_to_head_dev_pool.json` and published
  `reports/head-to-head-dev-001`.
- Added an `anthropic` tunnel (Anthropic Messages API, `ANTHROPIC_API_KEY`,
  `--model` or `ANTHROPIC_MODEL`) so Claude models run the same harness
  prompts; schema compliance comes from one forced tool call. The OpenAI and
  Anthropic transports share one bounded HTTPS exchange.
- Added `experiments/head_to_head.py`: a preregistered direct-model versus
  harness comparison with hash-sealed gold, alternating arm order, a registered
  success rule, and `SUMMARY.md` output; see `docs/HEAD_TO_HEAD.md`.
- Added a `double-loop` subcommand to the main CLI (the
  `python -m factcircuit.double_loop` module entry point remains), help text for
  every subcommand, and runnable example inputs for `early-risk` and
  `double-loop`.
- JSON inputs for `trace`, `trace-model`, `double-loop` and `trace-news` are now
  validated by field name: missing or unknown fields in `target`, a material or
  `config` produce a named error instead of a constructor message, and
  list-valued `evidence_scope` is accepted on every path (previously only
  `trace`).
- A missing Codex CLI is reported when the local tunnel is constructed, so
  every local-tunnel command exits 2 before any model work instead of recording
  a failed call per stage.
- Added a ruff lint job to CI and an end-to-end CLI test module.
- Restored the canonical README that PR #7 had overwritten with the
  development-archive README (wrong title, stale v0.2.0 version, `newsverify`
  commands, five links to unpublished September 8 reports). Docs and
  CONTRIBUTING now use the `factcircuit` command; added
  `python -m factcircuit.double_loop` and `factcircuit.early_risk` re-exports.
- Added incremental double-loop source analysis: each immutable version is
  decomposed and verified once per claim with full retained text, while prior findings
  and open gaps remain in later context. Legacy reanalysis requires the
  explicit boolean `reanalyze_existing_versions` option. Added cutoff-bound
  historical collector checks, strict bibliography identity binding that does
  not imply factual truth, and opt-in private LocalTunnel diagnostics that
  preserve failed-call status.

- Default local model-backed tracing to Astra; select other Codex models with
  `--model` or `FACTCIRCUIT_MODEL` without changing global model settings.
- Route the standalone news-tracing entry point through local FactCircuit by
  default. The legacy API workflow requires explicit `--tunnel api` and provider
  model settings; local mode does not load the API dependencies.

## 0.3.2 — 2026-09-08

- Added a packaged offline `quickstart` command with input/config/trace artifacts
  and a readable summary; existing output directories are preserved.
- Added configurable example budgets, `--version`, a first-run guide for
  macOS/Linux/Windows, and a minimal input for custom local model runs.
- Included isolated local GGUF inference and archived-response replay from the
  local-execution branches, with explicit separation from fresh Astra inference.
- Added wheel installation and first-run CI checks outside the source checkout,
  and release assets gated on passing checks.

This is a usability release of the research preview. It does not establish new
accuracy results or resolve the documented experimental semantic control error.

## 0.3.1 — 2026-09-06

### FactCircuit rebrand

- Renamed the project and GitHub repository to **FactCircuit**.
- Renamed the Python distribution to `factcircuit` and added the canonical
  `factcircuit` import package and command.
- Retained the `newsverify` import package and command as compatibility entry
  points for existing integrations.
- Updated current documentation, repository links, package metadata, security
  guidance, and CI examples without rewriting v0.2/v0.3 audit artifacts.

This release changes branding and entry points, not verification policy,
historical results, or the evidence-loop algorithm.

## 0.3.0 — 2026-09-06

### Highlights

- Added the staged validation loop: seven single-responsibility semantic stages
  with deterministic Python validation and judgement assembly.
- Added bounded retrieval feedback with exact task attribution, immutable
  material versions, reanalysis of affected history, and explicit stop reasons.
- Added fail-closed target grounding for actors, actions, objects, quantities,
  time scopes, negation, comparisons, and cross-event conflicts.
- Added the frozen Historical 2023 comparison harness with cutoff enforcement,
  gold isolation, raw call traces, resource accounting, and offline scoring.
- Expanded evaluation, repair, release, provenance, and staged semantic
  regression coverage to 250 passing tests.

### Observed pilot result

On the frozen two-case post-hoc pilot, the original monolithic adapter scored
1/2 and the staged adapter scored 2/2. The staged arm used 5.5× as many calls
and 3.17× as many total tokens. A separate full-evidence diagnostic failed in
the staged arm, so the run-level status is `has_errors` even though all scored
main cases completed. See the
[full report](reports/historical-2023-pilot2-v3-live-001/SUMMARY.md).

This small pilot is an engineering signal, not a general accuracy estimate or
an equal-compute causal result.

### Compatibility

- Python 3.11 or newer.
- The core package remains standard-library-only.
- Model-backed experiments use the optional `model` dependency group and run
  from a source checkout.
- Legacy `demo`, `verify`, and `benchmark` commands remain available.

## 0.2.0 — 2026-09-05

- Introduced the bounded provenance trace engine and fixed-target evaluation
  toolkit.
- Added immutable evidence versions, lineage-aware source grouping,
  reanalysis, explicit budgets, and deterministic offline fixtures.
- Added the MIT license, packaging metadata, contribution guide, and CI.
