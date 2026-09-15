# FactCircuit development archive

> This public repository preserves the early Accuracy Tracing / NewsVerify
> Harness development milestone. The project is now **FactCircuit**; use the
> [canonical repository](https://github.com/superwesleyhys-ux/factcircuit) for
> the current package, documentation, releases, and contributions. Historical
> names below are intentionally retained as part of the archived record.

Version 0.2.0: a bounded, auditable news provenance loop with **decomposition on every retrieval return**, a separate verification feedback stage, and a fixed-target evaluation toolkit.

**Status: local harness with offline replay, opt-in live news tracing, and two model execution paths.** Semantic judgments in the demo are hand-authored annotations. The default decomposer preserves original text and leaves source questions unresolved. Model-backed tracing supports local Codex execution and an optional OpenAI API tunnel. Earlier comparisons used unequal, manually curated evidence packets. Their scores do not establish automated retrieval performance or a same-evidence advantage. See the current evaluation and methodology notes below.

Repository Discussions are enabled, and the repository includes a prepared **Accuracy decline** reporting form for reproducible metric regressions or weaker trace outcomes. Reports should identify the affected metric or behavior, include the run configuration, and avoid treating synthetic fixtures as real-world performance evidence.

## Literal words and phrases: no-summary tracing

The `trace-phrases` command traces individual original-text occurrences with exact positions, preceding/following context and full source documents. It separates textual matches, source attribution and empirical judgments; it does not call the research-summary stages. See the [usage and limitations](docs/literal-phrase-tracing.md) and the [fixed DART comparison protocol](experiments/exact-phrase-v14-20260915/PROTOCOL.md). Exact citations do not guarantee factual truth.

The [real DART diagnostic results](reports/exact-phrase-v14-20260915/README.md) contain 12 actual Astra calls across three related original-text occurrences. Both arms reached the NASA source and preserved the observation-versus-threshold distinction. No factual-accuracy improvement was established; the report retains usage, errors, exact-input audits and comparison limits.

## Latest live source-tracing test: two audited chains

The September 14 V12 development run started from two raw news articles and
actually retrieved archived sources available by December 31, 2024. Both direct
Astra and the harness fetched both target original papers. Independent audits
confirmed **2/2 native harness source chains**, with **no repeated full-text
review within either native decomposition or native verification**.

All **46 real Astra calls succeeded**. Direct Astra used **896,837 tokens**;
the harness used **2,728,294**. Their withdrawal-risk categories matched. This
is a source-tracing and bookkeeping result on two repeatedly used development
cases; it does **not** establish better fake-news detection accuracy. Other
research stages still read source text, and two ancillary downloads in the
direct arm failed.

See the [complete V12 report, per-case evidence audits, token receipts and prior
failed rounds](reports/live-origin-v12-20260914/README.md). The new incremental
double-loop behavior preserves prior findings and unresolved gaps while
requiring explicit evidence for each new source relation.

## Earlier same-evidence Astra test (v6): tie

On **September 14, 2026**, v6 tested eight new anonymized research papers
published before 2024. Both arms received exactly the same three text summaries
per paper, using only source content dated through **December 31, 2024**.
The candidate added a new artifact-and-rebuttal verification policy. All **16
real Astra calls** used local Codex login, low reasoning, one call per arm/case,
and no retries or API fallback. Local login still uses remote model inference.

| Measure | Direct Astra | Astra + v6 policy |
|---|---:|---:|
| Later-outcome forecast matches | **7/8 (87.5%)** | **7/8 (87.5%)** |
| Later-retracted papers flagged elevated | 3/4 | 3/4 |
| Controls predicted ordinary | 4/4 | 4/4 |
| Forecast abstentions | 1 | 1 |
| Cutoff record-state annotation matches | 8/8 | 8/8 |
| Unsupported assertions of established fabrication | 0 | 0 |
| Input + output tokens | **76,960** | 78,750 |

**The candidate did not outperform direct Astra.** Both abstained on the same
paper: the supplied pre-cutoff abstract contained no specific integrity concern,
while the checked public criticism first appeared in 2025. That abstention counts
as a miss for outcome prediction, not as a false factual assertion. The candidate
used **2.3% more tokens**; paired predictions were identical (McNemar p = 1.0).

This is a small, retrospectively selected **same-evidence decision-stage test**.
The source editor knew the outcomes, curated the summaries and annotated cutoff
states; these are not independent truth labels. Models did not retrieve sources,
inspect images, or verify raw experiments. Retraction for unreliable data is not
proof of deliberate fraud, and controls without a located retraction are not
proved authentic. These numbers are not general fake-news detection accuracy.

See the [full v6 report and per-case results](reports/astra-same-evidence-v6-20260914/scored-v1/README.md),
[parsed outputs and call receipts](reports/astra-same-evidence-v6-20260914/run-001/predictions.json),
and [frozen protocol and source limitations](experiments/astra-same-evidence-v6-20260914/PROTOCOL.md).
The setup was pushed as `904d65eb` before inference. All registered integrity
checks passed; **461 regression tests**, **7 evaluation safeguards**, and the
installed-wheel local smoke check passed.

## Earlier unequal-evidence comparisons: interpretation corrected

**Methodology correction, September 14:** the v5, v4 and earlier source-trace
comparison runners send frozen text packets to a model. They do not execute an
automated retrieval stage during evaluation. Their candidate arms receive extra
curated concern/correction passages that the direct arms do not receive. The
previous description of these scores as proof of an automated source-tracing
system advantage was unsupported.

The recorded outputs are preserved:

| Run | Model | Direct outcome matches | Candidate outcome matches | Evidence design |
|---|---|---:|---:|---|
| [v5](reports/honest-system-holdout-v5-astra-20260912/scored-v1/README.md) | Astra | 4/8 | 8/8 | Extra curated traces for candidate |
| [v4](reports/honest-system-holdout-v4-20260911/scored-v1/README.md) | Luna | 4/8 | 8/8 | Extra curated traces for candidate |
| [Earlier source-trace run](reports/honest-system-holdout-20260911/scored-v1/README.md) | Luna | 4/8 | 8/8 | Extra curated traces for candidate |

These selected, eight-case comparisons measure responses to different supplied
evidence. They do not show independent source finding, better reasoning on the
same evidence, representative fake-news accuracy, or proof of deliberate fraud.
Sealing later labels does not make outcome-aware source selection independently
blind. Original predictions, registrations and receipts remain unchanged.

The [v3 infrastructure failure](reports/honest-system-holdout-v3-20260911/POSTMORTEM.md)
is also preserved and excluded from valid-win claims.

## Invalidated local historical diagnostic

The September 11 development run compared `gpt-5.6-luna` directly with the
same model inside the new single-pass provenance-risk harness. Both used the
local Codex-login route at low reasoning. The inference batch contained only
material available by December 31, 2023; the 2025 fabrication findings were
kept outside inference and loaded only during scoring.

| Measure | Direct Luna | Luna + single-pass harness | Registered goal |
|---|---:|---:|---:|
| Strict cutoff fact accuracy | 2/8 (25%) | **8/8 (100%)** | ≥6/8 (75%) |
| Later-false cases identified early | 0/4¹ | **4/4** | 4/4 |
| Control cases flagged high risk | — | **0/4** | diagnostic |
| Model calls | 8 | 8 | ≤ one/case |
| Input + output tokens | 164,490 | **64,444** | ≤328,980 |
| Harness / direct token ratio | 1.00× | **0.39×** | ≤2.00× |

**This result is invalid as evidence of advance fabrication detection.** The
policy directly mapped unauthenticated real-world provenance claims to high
risk, while every negative control asked only what a paper reported. The label
could therefore be recovered from the claim type, without detecting a signal
that distinguished fabricated from genuine provenance claims.

¹ The direct baseline had no separate risk output, and none of its cutoff fact
verdicts identified the four claims later shown to involve fabrication.

The [invalidated run, per-case table and reproducibility record](reports/early-risk-total-20260911/README.md)
remain published so the failed test cannot be silently discarded. A replacement
test requires unseen event families and same-task provenance controls, frozen
before inference. Until that test exists, FactCircuit has not demonstrated an
advance fabrication-detection improvement.

Run the single-pass path through the local tunnel with:

```bash
python -m factcircuit early-risk CASE.json --model gpt-5.6-luna --reasoning-effort low --output RESULT.json
```

## Run

Python 3.11+; standard library only. From this project directory:

```bash
python -m newsverify trace examples/local_trace.json --output reports/local-trace.json
python -m newsverify trace-demo --output reports/trace-demo-v0.2.json
python -m newsverify score examples/evaluation_gold.json examples/evaluation_predictions.json --output reports/all-metrics-v0.2.json
python -m newsverify compare examples/evaluation_gold.json examples/comparison_baseline.json examples/comparison_candidate.json --bootstrap-samples 100 --seed 0 --output reports/comparison-v0.2.json
python -m unittest discover -s tests -v
```

The trace demo follows four material versions over three retrieval rounds, reopens affected old analyses, and routes a verification-requested correction through decomposition. It preserves the original target and separates lineage from semantic contradiction.

The metric example is a deliberately imperfect set of four **handwritten predictions**, used to verify arithmetic against independent expected values. The comparison example uses identical handwritten runs to check paired differences. Neither example is a model performance result.

## Local harness execution

`trace <input.json>` runs directly in the local Python process. It reads a JSON
object with `target`, `rounds` (lists of material-version objects), and optional
`config`; see [local_trace.json](examples/local_trace.json). Source URLs are audit
metadata only and are never fetched. No API key, HTTP service, model SDK, or
remote inference is needed. Reports are written locally with `--output`.

This command preserves supplied text using `ConservativeDecomposer` and leaves
fact status as `not_checked` and original-source judgments unresolved. It does not turn a
snapshot into an automatic fact check. For semantic work, the hosting local
harness can pass its own Python `Decomposer` and `Verifier` objects directly to
`run_provenance`; a remote API is not part of the required integration.

The [public model comparison](reports/model-evaluation-20260908/README.md)
records Astra alone versus Astra inside the full harness. The local CLI uses a
hosted model through the existing Codex login; this is not offline inference,
an equal-compute experiment, or a held-out real-news accuracy result.

## Two model execution tunnels

For news articles, `trace-news` integrates the uploaded news-tracing project with
the double-loop harness. It fetches the article, follows its upstream links,
then reports source origin and factual support separately for every assessed
claim. Local Codex execution is the default; the API route remains explicit.

```sh
python -m newsverify trace-news examples/news_tracing.json --model gpt-6-astra --output reports/news-trace.json
```

This command uses live public pages and model calls. See
[news tracing](docs/NEWS_TRACING.md) for snapshots, budgets, limitations, and the
exact imported-code manifest. Original sources may contain false claims;
finding one does not by itself establish truth.
The [real integration test](reports/news-tracing-integration-20260908/README.md)
records a partial source chain, its missing research paper, and measured usage.

Both tunnels run the same decomposition and verification adapters in the local
harness. **Local is the default** for `trace-model`. Select API explicitly:

```bash
# Tunnel 1: local Codex CLI, using the existing Codex login
python -m newsverify trace-model examples/model_trace.json --output reports/model-local.json

# Tunnel 2: OpenAI Responses API, using OPENAI_API_KEY from the environment
python -m newsverify trace-model examples/model_trace.json --tunnel api --model YOUR_API_MODEL --output reports/model-api.json
```

The local default model is `gpt-6-astra`; `--model` explicitly selects another
model available through the local Codex CLI. `FACTCIRCUIT_MODEL` provides a
project-level default without changing the user's global Codex setting.
`--reasoning-effort` continues to use the configured global effort when omitted.
`--timeout` sets the per-call timeout
in seconds (default 180). The API tunnel uses the standard library and requires
`OPENAI_API_KEY` and an explicit `--model` or `OPENAI_MODEL`; the local tunnel
does not use an API key. Local Codex execution
can still use a hosted model, so it is distinct from offline inference.

Reports contain `execution.tunnel`, model settings and measured model-call usage.
Both paths use the same output schemas, exact-quote checks and historical
eligibility rules. Ineligible materials receive local preservation and do not
reach either model. Failed calls remain errors in the selected path; there is
no automatic fallback. Exit status is 1 for an audited execution failure and 2
for invalid input or unavailable configuration.

`trace-model` extracts claims and verifies facts over supplied snapshots. These
adapters do not yet infer a source graph or perform active follow-up retrieval;
a fact verdict does not mean provenance is complete. `trace` remains the fully
offline snapshot command described above. See [the tunnel contract](docs/MODEL_TUNNELS.md)
for configuration, reporting and validation details.

## Opt-in double-loop model runner

`newsverify.double_loop` adds model-generated source relations, evidence gaps,
gap resolutions and requests to reopen earlier analyses. Its provider selects
additional eligible documents from a fixed local snapshot pool in response to
the actual open questions. Verification follow-up enters that same retrieval
and decomposition path. It does not search the open web.

```bash
python -m newsverify.double_loop CASE.json --output REPORT.json --max-model-calls 10
```

The input contains `target`, `materials`, one `initial_version_ids` entry, and
optional round/document/decomposition `config` limits. Local is the default;
`--tunnel api` selects the separate API route. Reports preserve all model-stage
inputs and outputs, call usage, source-selection requests and analysis revisions.
The call cap includes selection, decomposition and verification. There is no
enforced total-token ceiling.

[Experiment code and reproducibility limits](experiments/README.md) describe
the public receipts and locally retained evidence archive. The 176 automated
tests use controlled fixtures; real model outcomes and actual loop execution
are reported separately. Verification questions reopened by newer evidence
cannot be silently closed by an older resolution, and provenance gap identities
remain protected after retirement. Local model subprocesses exclude installed
skill catalogs while retaining normal Codex instructions.

The [completed local Astra comparison](reports/model-evaluation-20260908/README.md)
records six constructed cases across three research topics. Original registered
label matches were 6/6 for Astra alone and 5/6 with the harness. The difference
exposed a corpus defect: the harness correctly flagged a news-date qualifier
missing from the supplied text. Both matched the five undisputed cases and a
separately reported corrected follow-up. The harness used 5.72 times the tokens
in the original batch. All 49 original and follow-up model calls succeeded.
Neither the flawed original tally nor the selected repair establishes general
accuracy superiority. Full source captures remain local; public numeric
receipts can be independently checked without redistributing publisher text.

The separate [historical-cutoff comparison](reports/historical-evaluation-20260908/README.md)
tests two pre-2024 research papers with public fabrication findings in 2025.
Only historical paper text available by December 31, 2023 enters the model;
later findings are held separately for scoring. Named cases and identity-masked
variants are reported separately, with true attribution controls. This is a
small retrospective test of supplied evidence, not a way to remove later
knowledge from the model's training. Abstaining on authenticity does not count
as detecting fabrication.

Both paths left both named authenticity claims unresolved and correctly answered
both attribution controls. The harness used 363,147 tokens versus 92,818 for
Astra alone (3.91 times as many). The masked cases retained one timeout in
each arm; its missing usage prevents an exact whole-batch token total. A separate
[Inspect AI replay](reports/historical-inspect-audit-20260908/README.md)
checks the saved outcomes without new model calls. These results show no advance
fabrication-detection benefit on this two-event sample.

## Trace engine

`run_provenance(target, provider, decomposer=None, verifier=None, config=None)` uses typed plug-ins documented in [TRACE_ADAPTER.md](docs/TRACE_ADAPTER.md).

- Immutable material versions, exact source spans and observation records.
- Every valid return is saved and decomposed before graph or verifier admission.
- Same-URL revisions keep distinct identities; an ID/content collision fails unresolved.
- Candidate relations distinguish direct, declared, inferred, unresolved and excluded evidence.
- Original-source completion requires an explicit finding and a direct lineage path from the target's source version. Support/contradiction edges do not substitute for that path.
- `revisit_versions` triggers affected earlier analyses; current results are rebuilt while history remains available.
- Verification gaps follow the same retrieval/decomposition route.
- Round, material and decomposition budgets, plus explicit no-progress and error results.
- Historical admission requires an exact version availability declaration and basis. There is no arbitrary age cutoff for old original records.

Historical graph admission is not proof against all future-information leakage: a stateful adapter might retain excluded content, and a pretrained model may already know later events. Strict historical inference isolation, live timeouts and model token accounting remain adapter work.

## Evaluation

Gold labels and predictions are separate files. Cases are fixed before inference. Missing/extra/duplicate target IDs, mismatched cutoffs, unjudged evidence IDs and invalid probabilities fail validation.

| Metric | Measures |
|---|---|
| VP | Precision of claims admitted to the trusted feed |
| FR | Fraction of false claims withheld from the feed |
| TR | Fraction of true claims admitted |
| SR | Correct original-root sets with valid provenance paths on traceable targets |
| EN | Precision of evidence asserted to support its target |
| CA | One minus half the four-class Brier score; probability quality, not pure calibration |
| HFAR | High-risk false claims incorrectly admitted |

Also reports source precision, false promotion of unknown origins, edge precision/recall, evidence recall, duplicate-pair F1, confusion matrix, coverage and ECE. A zero denominator is `null`. Missing probability vectors make CA and the aggregate unavailable. Scores never establish truth by themselves.

The experimental `NVScore` preserves the weights discussed in the design. It is not Terminal-Bench or an official benchmark, and its weights are not empirically validated. `compare` checks **declared** equal model/corpus/budget settings and supplied usage, then produces paired event-cluster bootstrap intervals. It cannot attest that external model usage logs are authentic.

The trace engine and scorer have separate schemas. A production exporter and independently reviewed data are still required; no implicit conversion turns plug-in judgments into gold labels.

## Documents

- [Chinese design conclusion and all metric definitions](docs/ACCURACY_TRACING_SPEC.md)
- [Codex execution plan and remaining implementation sequence](docs/CODEX_EXECUTION_PLAN.md)
- [Trace adapter API](docs/TRACE_ADAPTER.md)
- [Evaluation schema](docs/EVALUATION_SCHEMA.md)
- [Observed validation report](reports/VALIDATION_V0.2.md)

## Legacy compatibility

`python -m newsverify demo`, `verify`, and `benchmark` retain the v0.1 annotated-evidence policy runner in `core.py`. Its publisher/origin grouping and 72-hour default window are legacy policy choices, not the v0.2 provenance algorithm. Its 22 synthetic scenarios remain regression tests, not real-news accuracy estimates. The earlier [adapter contract](docs/ADAPTER_CONTRACT.md) applies to that runner only.

Released under the [MIT License](LICENSE). Current project:
[superwesleyhys-ux/factcircuit](https://github.com/superwesleyhys-ux/factcircuit).
