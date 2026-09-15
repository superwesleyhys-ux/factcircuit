# V8 raw-news comparison: retrieval improved, source tracing still failed

**The harness did not complete either primary-study trace (0/2). This run does not show an accuracy advantage.** The direct arm fetched both studies; the harness fetched one. All 37 model calls returned, but successful model calls did not guarantee a valid workflow or final assessment.

| Measure | Direct Astra | Astra + harness |
|---|---:|---:|
| Primary study actually fetched | 2/2 | 1/2 |
| Final primary-origin chain accepted by frozen V8 validator | 1/2 | 0/2 |
| Native harness primary-origin trace completed without errors | N/A | 0/2 |
| Model calls / successful model calls | 9 / 9 | 28 / 28 |
| Input + output tokens | 718,348 | 1,641,831 |

Both arms used the same two raw ScienceDaily inputs, gpt-6-astra with low reasoning, local Codex login, the same host tools and maximum budgets. Inference remained remote. Full archived text was supplied; no hand-prepared evidence packet replaced the news. Capture cutoff was 2024-12-31. Modern bibliography metadata was available only as a locator aid, so this is a retrospective development test, not an uncontaminated historical or prospective prediction experiment.

## What actually failed

- **a701 / direct:** read the original Tall el-Hammam study. Two linked critique/correction DOI archive requests failed. A later formal bibliography request was rejected by an overly narrow citation-context validator. No final forecast was produced.
- **a701 / harness:** read the study, but native decomposition redefined the runner-owned `origin:a701:c1` gap and triggered an ownership error. The shared final response was generated: empirical fact unresolved, withdrawal risk elevated, deliberate fabrication not established. The frozen final validator then incorrectly treated adjacent DOI/publisher aliases as a cycle. That assessment remains marked failed in V8; it is not counted as a successful source trace.
- **a702 / harness:** the bibliography service returned the correct paper, but the local filter rejected it because only one requested phrase appeared in the title. The model stopped after reading the university release. Its final response was unresolved / insufficient evidence, with no established fabrication. Native tracing returned, but did not locate the primary study.
- **a702 / direct:** revised its bibliography query to include the named crater, retrieved the archived primary study, and produced a validated chain. Its final response was supported / ordinary withdrawal risk, with no established fabrication.

## Interpretation and audit

Finding a study is not authenticating its measurements. An unresolved answer is not advance detection of fabrication. The first study's archived editorial-concern notice is pre-cutoff evidence of a reliability issue, not proof of intentional fabrication. Risk categories are separate from the empirical fact verdict. These two events have been used for development and are not an unseen holdout.

The initial derived summary checked for the DOI only near the page beginning. Nature displays its DOI near the article footer, so that audit rule undercounted actual retrieval. The corrected derived totals above check the complete article title and DOI in the complete fetched text. Raw predictions, statuses, model calls and their hashes were unchanged; the frozen V8 final-validator failures remain failures.

[Per-case results](RESULTS.json) · [Token totals](SUMMARY.json) · [Stage tokens](STAGE_TOKENS.json) · [Private receipt hashes](RECEIPTS.json) · Frozen protocol (protocol retained in local frozen source checkout: live-origin-v8-20260914/PROTOCOL.md)
