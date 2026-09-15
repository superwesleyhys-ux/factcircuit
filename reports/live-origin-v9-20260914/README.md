# V9: both studies retrieved, final predictions tied, native tracing failed

**No prediction advantage was observed.** Direct Astra and Astra with the harness produced the same two fact/risk classifications. Both fetched the target studies and passed the common final source-chain validator. The harness still failed both native traces, so it met the registered joint source-tracing outcome on **0/2 cases**.

| Measure | Direct Astra | Astra + harness |
|---|---:|---:|
| Target primary study fetched | 2/2 | 2/2 |
| Final primary-source chain accepted | 2/2 | 2/2 |
| Native primary trace completed without errors | N/A | 0/2 |
| Joint registered primary tracing success | N/A | 0/2 |
| Actual model calls / successful model calls | 10 / 10 | 32 / 32 |
| Input + output tokens | 1,005,243 | 2,664,185 |

Total usage was **3,669,428 tokens** across 42 actual calls. The harness used **2.65×** the direct arm's tokens. Successful model calls and final JSON responses are not counted as successful native traces.

## Actual final responses

| Raw-news case | Direct Astra | Astra + harness | Intentional fabrication established |
|---|---|---|---|
| a701: Tall el-Hammam airburst claim | Fact unresolved; withdrawal risk elevated | Fact unresolved; withdrawal risk elevated | Neither arm |
| a702: Chicxulub iridium claim | Fact supported; withdrawal risk ordinary | Fact supported; withdrawal risk ordinary | Neither arm |

For a701, the supplied archived paper already contains a February 2023 editorial warning about data and conclusions. The elevated risk response uses an existing warning; it is not advance discovery of hidden intentional fabrication. For a702, the paper reports original core measurements and analyses, but neither arm independently authenticated its raw laboratory data. Ordinary risk does not prove authenticity, and the end-of-2026 risk horizon is not fully observed at this run date.

## Why native tracing failed

Both harness arms first registered an original-observation finding while analyzing the actual study. A later revisit of the downstream news article proposed that same study as an origin again, with a different rationale/evidence record. The engine rejected these conflicting findings across material owners (`conflicting origin id across materials`). The native workflows remain failed even though the separate final assessment produced valid source chains.

V10 addresses record ownership in a new frozen policy: each material analysis registers only its own originality finding, while other materials can reference the existing record through validated citation edges. Its prompt also distinguishes missing source identity from missing empirical verification. V9 predictions, native failures and receipts have not been rewritten.

## Evidence and comparison limits

Both arms used the same complete raw archived ScienceDaily news inputs, gpt-6-astra with low reasoning through local Codex CLI login, the same host tools and maximum budgets. Inference remained remote. Each arm independently chose links or source-quoted bibliography searches before receiving additional text. The harness read the credited university pages; the direct arm used shorter available citation paths. The two arms did not receive manually constructed answers or later outcome labels from the runner.

Historical evidence was limited to captures no later than 2024-12-31. A present-day bibliographic index supplied identity-only locator metadata, followed by archived publisher-page verification. This is not a reconstructed 2024 search environment. These cases were already used for development; current model training memory cannot be removed. This is a retrospective development comparison, not an unseen holdout or clean prospective forecast. A two-case tie cannot establish general accuracy equivalence or superiority.

The primary title/DOI check in the mechanical summary identifies the returned study but does not alone establish body completeness. A separate independent audit verifies the actual main-article extraction against preserved raw capture bytes and substantive methods/results sections. Supplementary attachment files were not fetched. Direct a701 also preserves four failed ancillary correction/discussion fetches; the comparison was not free of source errors.

[Per-case results](RESULTS.json) · [Totals](SUMMARY.json) · [Stage tokens](STAGE_TOKENS.json) · [Receipt hashes](RECEIPTS.json) · [Independent source and joint-outcome audit](AUDIT.json) · Frozen protocol (protocol retained in local frozen source checkout: live-origin-v9-20260914/PROTOCOL.md)
