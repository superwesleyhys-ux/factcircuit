# Live source tracing V12: incremental review

The two-case development run completed **46 real Astra calls**, all successful, with **3,625,131 input + output tokens**. Both arms fetched the target original studies and passed the frozen final-chain validator. Independent audit confirmed **2/2 complete harness source traces**, and both passed the native full-text repetition checks.

This run tests actual source retrieval and the harness's evidence bookkeeping. It does **not** establish higher fake-news or fabrication-detection accuracy than direct Astra.

| Measure | Direct Astra | Astra + harness |
|---|---:|---:|
| Cases | 2 | 2 |
| Target original study fetched | 2/2 | 2/2 |
| Final origin chain accepted | 2/2 | 2/2 |
| Native source-chain independent audit | Not applicable | 2/2 passed |
| Native full-text repetition audit | Not applicable | 2/2 passed |
| Successful model calls / attempts | 8/8 | 38/38 |
| Calls with known usage | 8/8 | 38/38 |
| Input + output tokens | 896,837 | 2,728,294 |
| Harness / direct tokens | 1.00× | 3.04× |

All model calls succeeded. Two ancillary source requests failed in a701 direct: an archive index request for a 2023 correction, and a supplementary PDF archive request. Their errors remain in the results. A successful primary-study trace does not mean every linked attachment was downloaded.

## What changed and what was actually checked

V12 changes the double loop to incremental review. Within each claim, a source version's full text is sent once to native decomposition and once to native verification. Later calls carry metadata, accepted analyses, exact evidence and unresolved gaps. Previous analyses are not replaced merely because a new source arrives. New cross-source edges and gap resolutions still require explicit evidence.

The actual request audit found **six distinct source versions across the two harness arms**, each with one full-text decomposition and one full-text verification. It checked actual CLI stdin against immutable journal snapshots, request hashes, schemas, model settings and raw CLI responses. All **72 preregistered file hashes** still match the locally frozen code after the run. The freeze was committed locally before dispatch; it was not an external preregistration.

This is stage-specific deduplication. The separate news-research stages, initial retrieval decisions and common final assessment can read the same source again. Retained short evidence quotations can also recur. It is not a claim of one exposure per source across the entire application.

The seven news-research stages (`deconstruct`, `search_plan`, `source_trace`, `source_verify`, `timeline_build`, `direct_response`, `synthesis`) used **1,461,184 tokens (53.6% of harness usage)**. Native decomposition, verification and selection used **572,022**; retrieval and bibliography decisions used **405,893**; the common final assessments used **289,195**. Usage includes cached input tokens as input; cached counts are not added a second time. These are token receipts, not a monetary invoice.

## Per-case observations

| Case | Direct fact / withdrawal risk | Harness fact / withdrawal risk | Target study |
|---|---|---|---|
| a701: Tall el-Hammam airburst | Conflicting / elevated | Unresolved / elevated | [Nature original study](https://www.nature.com/articles/s41598-021-97778-3) |
| a702: Chicxulub iridium | Supported / ordinary | Supported / ordinary | [Science Advances original study](https://www.science.org/doi/10.1126/sciadv.abe3647) |

Both arms gave the same two withdrawal-risk categories. Neither established intentional fabrication. Direct a701 additionally retrieved a critique and an author correction; the harness retrieved the university release instead. The different empirical verdicts therefore are not a same-evidence reasoning comparison. Both arms had the same initial raw news, tools and maximum budgets, but chose different evidence.

The a701 harness's final chain is **ScienceDaily → East Carolina University → Nature**. The a702 harness's final chain is **ScienceDaily → University of Texas → Science Advances**. The latter university-to-paper step uses separately validated bibliography identity, not a hyperlink present in the university article.

For a701 direct, the model's rationale describes a DOI/publisher step as bibliographic identification. The actual supporting record is an observed DOI hyperlink followed by archived fetch redirects; no bibliography lookup occurred in that arm. This description error is preserved, and the fetch record is the chain's audit authority.

For a702, modern Crossref metadata reports February 26, 2021 while the university citation and archived article display February 24. The title, author, journal, subject and DOI binding are checked separately. Modern metadata is an identity locator, not historical evidence or a reconstruction of 2024 search.

The independent audit found 22 exact native evidence spans in a701 and 30 in a702. In a701, the origin obligation closed on explicit paper evidence while the empirical verification gap stayed open. In a702, the verification gap closed on the paper evidence, and the origin and lineage obligations stayed open until the university material was admitted and the bibliography edge was explicitly established. No gap disappeared by omission. See the [consolidated source audit](AUDIT.json).

## Protocol and limits

Both arms used **gpt-6-astra / low reasoning / local Codex login**, with remote inference. Each started from the same frozen, unprepared ScienceDaily article. Eligible evidence had to be available by **December 31, 2024**. Each case/arm had the same ceilings: 36 actual model calls, 6 document attempts including the seed, 4 retrieval decisions and 2 bibliography lookups. There were no same-arm retries, replacement cases or API fallback. Installed skills and model-side web, shell, app and multi-agent tools were disabled; the host exposed the same archive and bibliography operations to both arms.

These two cases have been repeatedly inspected during development. The cutoff controls supplied evidence, not the model's training memory. Later outcome labels were not loaded during inference. This is neither an unseen holdout nor a clean prospective forecast, and the end-of-2026 horizon is not fully observed. Ordinary risk is not proof of authenticity, retraction is not proof of deliberate fraud, and locating a paper does not authenticate its measurements or laboratory records.

Complete original article bodies are audited against raw archived HTML, extraction hashes and identity metadata. Supplementary files and raw laboratory records are not claimed obtained. Public artifacts contain results, validation records and hashes; original article bodies and raw private model packets remain local. Hashes alone cannot let a reader replay unavailable private bytes.

## Earlier failures remain part of the record

| Round | Independently valid harness source traces | Model attempts / successes | Measured input + output tokens |
|---|---:|---:|---:|
| [V8](../live-origin-v8-20260914/README.md) | 0/2 | 37/37 | 2,360,179 |
| [V9](../live-origin-v9-20260914/README.md) | 0/2 | 42/42 | 3,669,428 |
| [V10](../live-origin-v10-20260914/README.md) | 0/2 | 45/45 | 3,967,285 |
| [V11](../live-origin-v11-20260914/README.md) | 1/2 | 39/35 | ≥3,648,014 |
| V12 | 2/2 | 46/46 | 3,625,131 |

V8–V12 used at least **17,270,037 measured tokens**. Three V11 timeouts have unknown usage, so this total is a lower bound. Successful model transport in V8–V10 did not imply successful tracing. Earlier failures included overly narrow bibliography filtering, invalid alias-cycle checks, provenance ownership errors and dropped source gaps. V11 retained gaps but one native trace failed after timeouts and a DNS-error-bearing stream; it also still repeated source review.

The earlier [journal audit](../live-origin-journal-audit-20260914/AUDIT.json) found mutable-reference contamination in saved historical journal objects. Those original logs were not rewritten. V12 instead freezes request/response snapshots and retains exact private stdin and raw output receipts. Past costs and failures are preserved; they are not rescored into wins. Public-export manifests record path redactions and original/public artifact hashes.

## Artifacts and reproduction boundaries

- [Frozen V12 protocol](../../experiments/live-origin-v12-20260914/PROTOCOL.md), [registration](../../experiments/live-origin-v12-20260914/REGISTRATION.json) and [preflight checks](../../experiments/live-origin-v12-20260914/PREFLIGHT.json).
- [Per-case results](RESULTS.json), [mechanical summary](SUMMARY.json), [stage tokens](STAGE_TOKENS.json) and [private-file receipt hashes](RECEIPTS.json).
- [Independent source audit](AUDIT.json), [a701 source checks](A701_SOURCE_AUDIT.json), [a702 source checks](A702_SOURCE_AUDIT.json), and [whole-run receipt/freeze audit](COMPLETE_RECEIPT_AUDIT.json), and [direct-arm source checks](DIRECT_SOURCE_AUDIT.json).
- [Exact-request dedup audit](DEDUP_AUDIT.json). Its `source_trace_success: null` intentionally defers source success to the independent audit.

The runner and offline receipt checker are published with their frozen hashes. A new live run uses a new output directory, checks the registered code and seed hashes, and makes new model calls. Archive availability and model outputs may differ. The V11 replay preflight requires the original local response artifacts and does not make new model calls or reproduce new model behavior.

Publication checks: **530 regression tests passed**, the synthetic benchmark completed, and the built wheel passed its first-run check outside the checkout. These checks made no model calls. See [local validation receipts](LOCAL_CHECKS.json) and [pre-existing documentation/PR issues](KNOWN_ISSUES.md).
