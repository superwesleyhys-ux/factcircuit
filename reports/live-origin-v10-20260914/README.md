# V10: independent audit rejects a premature source-success claim

**Independently audited harness source-tracing success: 0/2. No accuracy advantage is established.** The mechanical result for the first harness case looked successful, but review found that reanalysis silently removed an explicitly unresolved source obligation. The second case also dropped its university-source gap and lacked a natively admitted bibliography endpoint. The preliminary 1/2 success update is corrected here; raw responses and mechanical statuses remain intact.

| Measure | Direct Astra | Astra + harness |
|---|---:|---:|
| Actual target main study fetched | 2/2 | 2/2 |
| Common final primary chain accepted | 2/2 | 2/2 |
| Native wrapper mechanically reports target origin | N/A | 1/2 |
| Independent joint source-tracing audit passes | N/A | **0/2** |
| Actual / successful model calls | 10 / 10 | 35 / 35 |
| Input + output tokens | 1,085,510 | 2,881,775 |

Total: **3,967,285 tokens** across 45 actual calls. The harness used approximately **2.65×** the direct arm's tokens. Mechanical stage completion does not supersede the independent source/gap audit.

## Final predictions actually produced

| Case | Direct Astra | Astra + harness | Intentional fabrication established |
|---|---|---|---|
| a701: Tall el-Hammam airburst | Conflicting evidence; elevated withdrawal risk | Unresolved fact; elevated withdrawal risk | Neither |
| a702: Chicxulub iridium | Supported; ordinary withdrawal risk | Supported; ordinary withdrawal risk | Neither |

The risk classifications agree. The a701 fact classifications differ: direct retrieval additionally obtained a 2022 Matters Arising article and Author Correction, while the harness obtained the credited ECU press release and stopped after the original study. The critique contests the proposed interpretation; the correction acknowledges inappropriate image editing of extraneous features, not proven fabricated measurements. These are pre-cutoff sources. Neither fact label can be accuracy-ranked from this comparison alone without a defensible independent gold assessment.

The archived original study already contains a 2023 editorial warning. Elevated risk based on that warning is not early discovery of hidden intentional fabrication. Ordinary risk does not establish authenticity. The end-of-2026 forecast horizon is not yet fully observed at this run date.

## Native failures found by independent review

- **a701:** the initial ScienceDaily analysis creates the blocking provenance gap `ecu-source-version`. The primary-study analysis and later ScienceDaily revisit both say that gap remains open. The revisit nevertheless returns an empty gap list, which deletes its earlier obligation. ECU was fetched by the host but was never admitted into the native trace. The trace contains a valid direct ScienceDaily-to-study DOI edge, and the separate final chain through ECU is valid, but the unresolved declared obligation was lost. Mechanical success is therefore not counted as clean audited completion.
- **a702:** the same omission deletes `ut-release-source-lineage`. UT is present in the host's fetched pool but absent from native materials. The native proposal links ScienceDaily directly to the study; the actually validated bibliography binding is UT-to-study. The existing wrapper rejects this unsupported native edge and correctly leaves the origin unresolved. A valid separate final chain does not repair the native failure.

All accepted native evidence spans and final quotations checked against their actual source text. The problem is lost obligations and incomplete native lineage, not fabricated quotations. V11 preserves an owning analysis's still-open gaps until an explicit valid evidence resolution. An unchanged-response replay of V10 a701 then returns partial provenance and retains the ECU gap. That replay is a software regression check with zero new model calls, not a new performance result.

## Source and evaluation limitations

Both arms used the same raw ScienceDaily inputs, gpt-6-astra with low reasoning through local Codex CLI login, the same host tools and maximum budgets. Inference remained remote; installed skill catalogs were disabled. Additional sources entered only after each arm requested them. The actual fetched evidence sets differ because the models made different retrieval choices.

Main-study text and the newly fetched critique/correction text match complete extraction of preserved, hash-validated archived HTML with eligible Memento dates. Main articles are complete; supplementary attachments and underlying laboratory datasets were not fetched. Direct a701 preserves two failed DOI source requests. Archive availability changed from the preceding run, so a V9-to-V10 difference is not a controlled estimate of the policy change alone.

The registered mechanical summarizer used a title substring that could mistake an Author Correction for the original article. A separately named post-run audit helper requires the exact article title before the publisher suffix. The registered helper and raw outputs remain unchanged; the corrected audit excludes the correction notice from target-primary matches. Full-body verification is independent of this title check.

A separate logging audit found that the outer `model-io.json` stores mutable object references: later host processing can add fields to earlier responses and later retrieval decisions to earlier packet snapshots. Those outer objects are therefore derived records, not authoritative immutable model inputs or outputs. CLI output files and native call-budget response snapshots preserve original model responses. Source/citation and gap-lifecycle findings were checked against those original outputs; byte-exact reproduction of every submitted input has not been independently established. This logging defect is retained and disclosed rather than rewriting the frozen logs.

Evidence cutoff was 2024-12-31. Modern bibliography metadata was identity-only locator information, followed by archived-page verification; it is not a reconstruction of 2024 search. These two cases have been repeatedly used for development, and model training memory cannot be removed. This is a retrospective development test, not an unseen holdout or proof of prospective prediction.

[Per-case mechanical results](RESULTS.json) · [Mechanical totals](SUMMARY.json) · [Independent audit — authoritative for joint success](AUDIT.json) · [Stage tokens](STAGE_TOKENS.json) · [Receipt hashes](RECEIPTS.json) · Frozen protocol (protocol retained in local frozen source checkout: live-origin-v10-20260914/PROTOCOL.md) · [Separate post-run title audit](../../experiments/live-origin-v10-20260914/audit_completed_run.py)
