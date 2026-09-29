# Factual judgment: records, checks and reproducible calculations

The requested outcome is a verdict about the exact real-world claim at its cutoff, grounded in inspectable evidence. Source location and textual support remain separate outputs. This mode has no forecast or withdrawal-risk score.

## Contract

The factual verifier receives eligible immutable source texts, the fixed target, lineage edges and its own open check tasks. Earlier model verdicts and free-form research conclusions are excluded from this packet. It uses the user's existing local/API model transport and call budget.

It must explicitly assess six dimensions: target scope, primary record, method, calculation, source dependence and counterevidence. Target scope and primary record are always required; the other checks must explain applicability. A definite world verdict requires grounded scope/record checks and its own exact evidence. A required failed or missing check retains an executable world-verification task. Missing records alone never establish falsity. For a reporting claim, the original document can be the relevant primary record; for an empirical claim, a quotation of an author's assertion does not authenticate the measurement.

Supported numeric operations are sum, difference, mean, ratio, percentage and percent change. Operands must occur in their cited exact source passages, the claimed number must occur in an exact target passage, units must be compatible, and rounding is explicit (0–6 decimal places). Python Decimal recomputes the result. Mismatches or undefined calculations block a positive world verdict and create a reanalysis task. They do not automatically prove an experiment false or a dataset fabricated.

Check gaps have stable IDs and preserve registered lifecycle identities. A verified check closes an existing check gap only with quoted evidence. An existing arithmetic discrepancy can also close when quoted records and the later factual assessment settle it as a contradiction; undefined arithmetic remains open. New materials continue through the existing save/decompose/verify loop. The snapshot command selects from the supplied pool; unavailable external records remain explicit tasks. This change does not implement an unrestricted web or laboratory-data acquisition service.

## Entry points

```sh
python -m factcircuit judge-facts examples/factual_judgment.json --model gpt-6-astra --output reports/factual-judgment.json
python -m factcircuit trace-news examples/news_tracing.json --factual-judgment --output reports/news-facts.json
```

`judge-facts` is the new fact-first entry point. `trace-news --factual-judgment` uses the same verifier after news/source acquisition. Existing trace commands retain their previous contracts for reproducible comparisons. Local Codex is the default; API remains explicit, with no automatic fallback.

Reports preserve requested and gated world verdicts, all check findings, recomputed calculations, evidence anchors, follow-up tasks and model usage. An unresolved fact is not labelled false. Code tests use scripted inference and synthetic records; their counts are not real-news accuracy. The semantic model still judges whether a source record is relevant and credible: structural checks and arithmetic do not independently authenticate measurements. Real accuracy requires a separate independently adjudicated evaluation.
