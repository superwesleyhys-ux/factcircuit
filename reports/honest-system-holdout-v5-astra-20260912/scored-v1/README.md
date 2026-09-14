# Astra system holdout v5: original model vs FactCircuit source tracing

> **Interpretation correction — 2026-09-14:** This historical run used unequal,
> manually curated evidence packets. Its runner did not perform automated
> retrieval. The outcome-aware selection and summaries were not independently
> blind. The numerical outputs below are preserved, but any system-level or
> “blind” interpretation is superseded by this note. It does not establish a
> same-evidence policy advantage or deliberate fabrication detection. See the
> [v6 same-evidence protocol](../../../experiments/astra-same-evidence-v6-20260914/PROTOCOL.md).

This is a preregistered system comparison on eight previously unused papers. The direct
arm received the anonymized original-article record. The harness arm received the same
record plus source traces that were already public by the end of 2024. Later publisher
outcomes were sealed and revealed only after inference.

Both arms used the local Codex-login route with `gpt-6-astra` at low reasoning, one call per case.

| Registered measure | Direct model | FactCircuit harness |
|---|---:|---:|
| Balanced accuracy | 50.0% | 100.0% |
| Overall accuracy | 4/8 (50.0%) | 8/8 (100.0%) |
| Later-positive recall | 0/4 (0.0%) | 4/4 (100.0%) |
| Control specificity | 4/4 (100.0%) | 4/4 (100.0%) |
| Valid completed outputs | 8/8 | 8/8 |
| Input + output tokens | 75,198 | 78,836 |
| Harness / direct tokens | 1.00x | 1.05x |

**Winner by the preregistered primary metric: harness.**

**All registered success criteria met: yes.**

## Per-case blind results

| Case | Later outcome | Direct | Harness |
|---|---|---|---|
| v501 | elevated | ordinary (wrong) | elevated (correct) |
| v502 | ordinary | ordinary (correct) | ordinary (correct) |
| v503 | elevated | ordinary (wrong) | elevated (correct) |
| v504 | ordinary | ordinary (correct) | ordinary (correct) |
| v505 | elevated | ordinary (wrong) | elevated (correct) |
| v506 | ordinary | ordinary (correct) | ordinary (correct) |
| v507 | elevated | ordinary (wrong) | elevated (correct) |
| v508 | ordinary | ordinary (correct) | ordinary (correct) |

## What the labels mean

`elevated` means a publisher first issued the registered retraction outcome in 2025–2026.
`ordinary` means the source audit located no retraction concerning the control through
2026-09-12. The latter is a right-censored control label, not a claim that the work is
authentic in every respect.

## Guardrails and limitations

The corpus, both arm packets, prompts, order, model, scorer, sealed-gold hash, and pass
conditions were committed before the first call. The isolated runner sent only the
registered anonymized packet for each arm. There were no retries or post-result changes.

This eight-case result is exploratory and is not statistically conclusive. Model
pretraining may contain later facts even though names, titles, DOI values, institutions,
journals, URLs, and outcome reports were removed from the packet.

## Reproduce

See `experiments/honest-system-holdout-v5-astra-20260912/PROTOCOL.md`, `REGISTRATION.json`,
`corpus.json`, `SOURCE_AUDIT.json`, and the run receipts in this directory. The revealed
gold file contains the post-cutoff publisher outcomes and matches the hash committed
before inference.
