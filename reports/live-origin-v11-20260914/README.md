# V11: one source trace passes, one fails during transport

**Independent harness primary-source tracing: 1/2. Both final forecasts match direct Astra; no prediction-accuracy advantage is established.**

| Measure | Direct Astra | Astra + harness |
|---|---:|---:|
| Main study fetched in full | 2/2 | 2/2 |
| Common final source chain accepted | 2/2 | 2/2 |
| Native + final source audit passes | N/A | **1/2** |
| Actual / successful model calls | 9 / 9 | 30 / 26 |
| Calls with known token usage | 9 | 27 |
| Measured input + output tokens | 1,134,170 | 2,513,844 |
| Total including unknown usage | 1,134,170 | **Unknown** |

The measured sum is **3,648,014 tokens**, a lower bound. Three timeouts have unknown usage. A fourth failed call has measured usage and is included despite its response being rejected.

## Predictions actually returned

| Case | Direct Astra | Astra + harness |
|---|---|---|
| a701: Tall el-Hammam airburst | Unresolved; elevated withdrawal risk | Unresolved; elevated withdrawal risk |
| a702: Chicxulub iridium | Supported; ordinary withdrawal risk | Supported; ordinary withdrawal risk |

Neither arm established intentional fabrication. This is retrospective evaluation at a December 31, 2024 evidence cutoff, not prospective discovery. The first paper already carried a 2023 editorial warning. These cases were repeatedly used for development; model training memory cannot be removed. The end-of-2026 horizon is not fully observed.

## Independent source findings

- **a701 passes.** Native tracing admitted ScienceDaily, ECU and the original Nature paper. The ECU gap survived the seed revisit and was explicitly resolved after ECU admission, with exact evidence. All ordinary provenance gaps have valid lifecycle records. The empirical verification gap remains open and the fact verdict remains unresolved.
- **a702 fails.** The host fetched the seed, UT and the full Science Advances paper, and the final chain is valid. Native tracing admitted only the seed and accepted no decomposition. Calls 6–8 timed out. Call 9 emitted DNS/reconnection errors before a completed output; the frozen transport rule rejected that error-bearing stream. No output was salvaged to convert failure into success. A final forecast does not repair the failed native trace.

All four main-paper instances match complete archived HTML extraction and eligible capture metadata. Supplementary attachments and laboratory records were not fetched. One ancillary archive failure in a701 direct is retained. Both arms had the same initial news, permissions and maximum budgets; their actual evidence sets differ because their retrieval choices differ.

## Integrity limits

Outer journal packets/responses were stored by mutable reference and can acquire later host fields. Original CLI outputs and native response snapshots were used to audit actual model responses. Exact CLI stdin was not retained, so input reconstruction has limits. See the [journal audit](../live-origin-journal-audit-20260914/AUDIT.json). A snapshot correction was tested offline but not retroactively applied to this frozen run.

The timeout path did not save CLI streams for calls 6–8; their individual causes are unknown. Call 9 explicitly recorded DNS failure. Host wake information near call 9 does not prove every earlier failure's cause. Unknown usage is never treated as zero.

## Failed rounds remain visible

| Round | Independent harness traces | Measured tokens | Complete usage? |
|---|---:|---:|---|
| [V8](../live-origin-v8-20260914/README.md) | 0/2 | 2,360,179 | Yes |
| [V9](../live-origin-v9-20260914/README.md) | 0/2 | 3,669,428 | Yes |
| [V10](../live-origin-v10-20260914/README.md) | 0/2 | 3,967,285 | Yes |
| V11 | 1/2 | 3,648,014 | No |

These four rounds used at least **13,644,906 measured tokens**. Archive availability and actual retrieval choices changed across rounds; this is not a controlled prompt-only effect.

V11 was frozen at `6e88fb2c`; 494 regression tests passed before dispatch. A zero-model-call replay of unchanged V10 responses correctly retained the ECU gap and returned partial provenance. Software tests are not additional accuracy results.

V11 still re-decomposed unchanged seed material. It therefore does not satisfy the user's newer requirement to avoid repeated source review; that policy is being developed separately in V12.

[Source audit](AUDIT.json) · [Call/token audit](TOKEN_AUDIT.json) · [Mechanical results](RESULTS.json) · [Summary](SUMMARY.json) · [Stages](STAGE_TOKENS.json) · [Receipts](RECEIPTS.json) · Protocol (protocol retained in local frozen source checkout: live-origin-v11-20260914/PROTOCOL.md)
