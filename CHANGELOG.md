# Changelog

All notable changes to FactCircuit are documented here. The project uses
semantic versioning for its public releases.

## Unreleased

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
