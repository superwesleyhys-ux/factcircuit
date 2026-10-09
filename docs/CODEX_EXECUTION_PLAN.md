# Codex execution plan — accuracy tracing

This file is the executable engineering handoff. The current Codex workspace has already started and implemented the v0.2 reference milestone; do not recreate it from scratch. Use `ACCURACY_TRACING_SPEC.md` as the product and evaluation contract.

## Ownership and location

- User-selected project: **accuracy tracing**.
- Python distribution: `newsverify-harness`, package: `newsverify`.
- Source package is intended to be extracted into a workspace named `accuracy-tracing`.
- This workspace publishes the historical milestone to
  `superwesleyhys-ux/factcircuit-development-archive`. Current FactCircuit work
  belongs in the canonical repository linked from the README. No private UCA
  code is needed or modified.
- The application does not expose an action for moving this chat into the screenshot's project. Import the delivered source into that project's connected workspace when one is available; do not report that the UI move has happened merely because a similarly named directory exists.

## Execution mode: two model tunnels, local by default

Run the harness in the local process. `trace-model` selects the local Codex CLI
by default; `--tunnel api` explicitly selects the OpenAI Responses API. Both
paths share semantic adapters and validation. Local uses the existing Codex
login; API alone requires `OPENAI_API_KEY`. Never silently switch paths on an
error. The user's two-tunnel request supersedes the earlier local-only plan.

`python -m newsverify trace examples/local_trace.json` remains a fully offline
snapshot replay. `trace-model examples/model_trace.json` performs model-backed
claim extraction and fact verification; neither its current local nor API
adapter constructs a complete source graph or active retrieval plan yet.
See `MODEL_TUNNELS.md` for commands and limitations.

The opt-in `newsverify.double_loop` runner now adds source relations, adaptive
selection from an eligible local pool, and reanalysis of earlier evidence.
Its completed development comparison protocol is in
`experiments/gstack-harness-eval-20260908/`; the report artifacts, full source
captures and historical experiment packets remain local and are not published
in this repository.

## Non-negotiable behavior

1. Preserve the target text and all raw material versions. Revised interpretations are appended with history.
2. Every valid retrieval return visits decomposition before its analysis enters the graph or verifier. Duplicate observations also receive a decomposition comparison. Invalid object schemas or version collisions produce explicit errors.
3. Reopen affected old analyses through `revisit_versions`. Preserve the old revision and rebuild active derived state.
4. Separate provenance relation types from semantic support/contradiction. Preserve declared/inferred/unknown status.
5. Treat unavailable original materials and anonymous sources as explicit gaps. Earliest accessible is not automatically original.
6. Verification follow-up must retrieve material and send it through the same decomposition route.
7. Stop on documented resource or progress conditions, never by pretending missing evidence was found.
8. No accuracy claim from synthetic replay, repeated model agreement, or the hand-authored metric fixtures.

## Implemented milestone

| Work item | Code / artifact | State |
|---|---|---|
| Versioned trace engine and typed adapter boundaries | `newsverify/provenance.py` | Implemented |
| Synthetic replay spanning both loops | `newsverify/trace_demo.py` | Implemented |
| Seven metrics, uncertainty handling, diagnostic scores | `newsverify/evaluation.py` | Implemented |
| Matching-budget checks and paired event-cluster comparison | `newsverify/comparison.py` | Implemented |
| Console entry points | `newsverify/cli.py` | Implemented |
| Behavioral, metric and comparison tests | `tests/` | Implemented; final results in validation report |
| Complete design and metric definitions | `docs/ACCURACY_TRACING_SPEC.md` | Written |

The root task handed the trace-engine implementation to a bounded coding task and completed evaluation/integration in the same shared workspace. This describes work actually performed, not a queued external chat message.

## Run the delivered milestone

From the extracted project root, using Python 3.11 or later:

```bash
python -m unittest discover -s tests -v
python -m newsverify trace-demo --output reports/trace-demo-v0.2.json
python -m newsverify score examples/evaluation_gold.json examples/evaluation_predictions.json --output reports/all-metrics-v0.2.json
python -m newsverify compare examples/evaluation_gold.json examples/comparison_baseline.json examples/comparison_candidate.json --bootstrap-samples 100 --seed 0 --output reports/comparison-v0.2.json
```

The comparison example uses identical, explicitly handwritten predictions and zero model usage. It checks the comparison machinery; it is not a single-pass-versus-loop model experiment. Legacy `demo`, `verify` and `benchmark` commands remain compatible and refer to the older annotated-evidence policy runner.

## Next implementation sequence

### A. Local material adapter

Implement `TraceProvider.search` from `TRACE_ADAPTER.md`. Supply exact version identity, full text, observation time and a documented availability basis. Keep retrieval result ranking and following citations separate. Never use a search snippet as if it were the complete original. Read already available local snapshots or the hosting harness’s material store. Count calls, wall time and returned bytes; do not introduce a network API dependency.

Acceptance: source text can be reopened; all spans refer to the stored version; revisions retain identity history; availability unknown remains unknown; missing or invalid local material leaves usable audit output. Use locally available material with documented provenance. API credentials are not a prerequisite.

### B. Structured semantic decomposition adapter — implemented for the opt-in runner

`DoubleLoopDecomposer` implements the `Decomposer` protocol with structured output conforming to `Analysis`. The opt-in runner uses the selected model tunnel with shared prompts, schema validation and source-span checks. Inputs contain the target, eligible evidence, current analyses, relations and gaps, without gold labels. Outputs include exact spans, typed candidate relations, explicit gap resolutions and old versions to revisit. Broader event coverage and semantic graph validation remain production evaluation work.

Acceptance: number/unit/denominator preservation; attribution and negation scope; same event versus same wording distinctions; joint sources and alternative source hypotheses; actual reanalysis on new evidence. Default `ConservativeDecomposer` remains available as a conservative fallback.

### C. Historical isolation and identity integrity

Before a stateful semantic model sees a version, isolate or reject versions that cannot qualify at the cutoff. The orchestrator currently invokes decomposition for excluded versions and prevents graph admission; this alone does not stop stateful-plugin leakage. Route excluded returns through an isolated metadata-analysis path or a stateless worker, retain the return audit, and test that subsequent eligible calls cannot see excluded content.

Also validate cross-analysis fragment/relation identity collisions and explicit dependency invalidation. The current version fingerprint prevents material ID collisions; it does not implement every semantic graph constraint or prove a publisher's historical claims.

### D. Evidence verification adapter — implemented for the opt-in runner

`DoubleLoopVerifier` implements `Verifier.verify` in the same harness process, using the selected model tunnel. It requires valid source spans for definite verdicts and emits gaps and revisit requests when evidence is missing. Further material enters through the provider and mandatory decomposition route. Independent evaluation of entailment, contradiction, temporal relevance and shared-source dependence remains necessary: an official source can be an original for its own announcement without making every claim in it true.

Acceptance: source found with truth unresolved; conflicting evidence; shared-source copies; unsupported certainty; legitimate updates; verification gap followed by a new material entering psi before the next verdict.

### E. Strict prediction export

Map trace outputs to evaluation case IDs without merging or dropping hard targets. Keep raw `origins` as candidate investigation findings until reviewed against task-level conditions. Translate relation names explicitly: `translates` to `translated_from`, `derives` to `derived_from`, and reject unsupported mappings. Do not treat `supports` as a citation edge. Derive source paths from the graph; do not simply copy a root's URL into the result.

Probability vectors require actual model output with a declared generation/calibration method. No output means `null` and no CA/composite. The scorer and runner use deliberately separate schemas; a general exporter is not yet implemented and must not be silently assumed.

### F. Independent reference dataset

Build independently reviewed claim labels, acceptable root sets, valid paths, evidence stances and origin groups. Fix each exact material version and investigation cutoff. Preserve annotator rationale and disagreement resolution. Split by event and time and keep the final test labels unavailable to inference. First trial should cover all cases specified in the design document; sample size is chosen from the needed uncertainty, not from a desired headline score.

Acceptance: every gold source answer has a path, every prediction ID is mandatory, new evidence needs adjudication, and event groups remain intact across splits. Public synthetic examples may be used for unit tests but not for the final accuracy headline.

### G. Equal-budget model experiments

Run four variants: single pass; multi-round retrieval with a fixed decomposition; re-decomposition provenance; re-decomposition plus verification feedback. Hold model and corpus snapshots fixed. Count all model calls and tokens across stages, including retries, and log per-target usage. Use `compare` for paired differences. Predeclare the main endpoint and guardrails before looking at the hidden test; report coverage and failure categories with every headline metric.

Acceptance: measured source gain is attributable to the correct ablation; unknown-source false promotion and HFAR do not deteriorate beyond a predeclared tolerance; uncertainty reflects independent event groups; no optional stopping on attractive test results. Thresholds must be chosen and justified on development data before final testing.

### H. Publication package

Update README with observed results and exact limitations, install the package in a clean environment, include data licenses and reproducible manifests, and then create the requested repository/release when a destination exists. Do not claim an external repo, application-project transfer, deployment or programme acceptance occurred without a successful corresponding operation.

## Stop and handoff records

For any blocked local adapter or missing local material, complete the available code and report the precise missing interface. Do not reinterpret a resource ceiling as user permission needed, and do not keep retrying unavailable services. Preserve a validation report with tests run, errors, datasets used, actual model use, and remaining tasks.
