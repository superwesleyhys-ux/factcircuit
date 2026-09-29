# Factual Judgment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inspectable real-world checks and deterministic arithmetic to the existing fact-verification loop.

**Architecture:** A FactualVerifier implements the existing Verifier protocol. Its typed checks produce evidence-backed world verdicts, executable gaps and explicit resolutions; the existing trace engine owns material admission and lifecycle integrity.

**Tech Stack:** Python 3.11+, standard library, unittest, existing local/API transport.

**Spec:** docs/FACTUAL_JUDGMENT.md

## Global Constraints

- Source location and textual support remain separate outputs.
- Local Codex is the default; API remains explicit, with no automatic fallback.
- Missing records alone never establish falsity.
- Supported numeric operations are sum, difference, mean, ratio, percentage and percent change.
- Rounding is explicit (0–6 decimal places).
- Code tests use scripted inference and synthetic records; their counts are not real-news accuracy.

## Review Focus

- A later primary record must close only its matching registered check gap.
- Arithmetic must not bind 60 to 600 or silently mix measurement units.
- Existing trace commands and past experiment schemas remain reproducible.
- Future/unknown-availability source bodies must never reach the factual verifier.
- A bounded inference error must remain an audited failure rather than a fact verdict.

### Task 1: Grounded factual verifier

**Files:** newsverify/factual_judgment.py; tests/test_factual_judgment.py.
**Interfaces:** Produces FactualVerifier(transport).verify(Target, context) -> VerificationResult and .history audit records. Consumes eligible material dictionaries, Gap/Resolution/Span and transport.generate.

- [x] Write missing-record, true/false, scoped checks, arithmetic mismatch/zero/unit/binding, anchoring and gap-resolution tests.
- [x] Run `python3 -m unittest discover -s tests -p test_factual_judgment.py -q`; expected missing-adapter failures.
- [x] Implement structured checks, strict quotes, Decimal calculations, preserved gap identities and evidence-backed resolutions.
- [x] Run the targeted tests; expected all pass.
- [x] Commit the verifier and contract.

### Task 2: Wire the fact-first entry points

**Files:** newsverify/double_loop.py; newsverify/news_tracing_runner.py; newsverify/cli.py; factcircuit/factual_judgment.py; examples/factual_judgment.json; tests/test_factual_judgment.py; README.md.
**Interfaces:** Consumes FactualVerifier. Produces run_double_loop_trace(..., factual_judgment=False), `judge-facts` CLI and `trace-news --factual-judgment`.

- [x] Write integration tests for a real two-round record-fetch and matching gap closure, historical exclusion, explicit failure, CLI and news flag routing.
- [x] Run targeted tests; expected integration failures before wiring.
- [x] Integrate the verifier under the existing shared budget; expose check history in reports and provide a synthetic numeric example.
- [x] Run `python3 -m unittest discover -s tests -q`; expected a green suite.
- [x] Review the whole branch, fix material issues with RED→GREEN tests and commit the reviewed result.
- [ ] Create a draft PR with validation and the explicit semantic/measurement limits.
