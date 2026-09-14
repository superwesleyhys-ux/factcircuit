# Astra v6: new dataset with identical evidence

**Observed primary outcome: tie.** This is an exploratory decision-stage comparison on eight new papers, not a test of automated source finding.

Both arms requested gpt-6-astra at low reasoning through local Codex login (remote inference). Each received the exact same three dated text summaries. The candidate adds the v6 artifact-and-rebuttal policy. One call per case/arm; no retries or fallback.

| Measure | Direct Astra | Astra + v6 policy |
|---|---:|---:|
| Later-outcome forecast matches | 7/8 | 7/8 |
| Balanced accuracy (abstentions count as misses) | 87.5% | 87.5% |
| Later-retracted cases flagged elevated | 3/4 | 3/4 |
| Controls predicted ordinary | 4/4 | 4/4 |
| Forecast abstentions | 1 | 1 |
| Cutoff record-state annotation matches | 8/8 | 8/8 |
| Unsupported established-fabrication assertions | 0 | 0 |
| Valid outputs | 8/8 | 8/8 |
| Input + output tokens | 76,960 | 78,750 |

Paired discordances: candidate-only correct 0; direct-only correct 0. Exact two-sided McNemar p = 1.0000. Token ratio: 1.023x.

## Per-case outcomes

| Case | Later outcome target | Direct forecast | Candidate forecast | Direct cutoff state | Candidate cutoff state |
|---|---|---|---|---|---|
| q8701 | ordinary | ordinary | ordinary | addressed_concern | addressed_concern |
| q7071 | elevated | insufficient_evidence | insufficient_evidence | no_specific_concern | no_specific_concern |
| q2371 | elevated | elevated | elevated | unresolved_concern | unresolved_concern |
| q6297 | ordinary | ordinary | ordinary | addressed_concern | addressed_concern |
| q9641 | elevated | elevated | elevated | unresolved_concern | unresolved_concern |
| q6682 | elevated | elevated | elevated | unresolved_concern | unresolved_concern |
| q9417 | ordinary | ordinary | ordinary | addressed_concern | addressed_concern |
| q3724 | ordinary | ordinary | ordinary | addressed_concern | addressed_concern |

## Interpretation and limits

Risk forecasts and fact determinations are separate. The hard case with no supplied pre-cutoff concern can be a predictive miss even when abstaining is evidence-responsible. A publisher retraction for unreliable data does not by itself establish deliberate fabrication. No located retraction in a control is not proof that its experiments were authentic.

The same outcome-aware assistant selected these cases, paraphrased sources, wrote policies and annotated cutoff states. The key was sealed before calls, but this is not independent blind evaluation. The sample is small, purposely balanced and restricted to scientific articles; original publisher families differ between the two outcome classes. Source dates are reconstructed from current records, not independent historical snapshots. Model training knowledge may still contain later events despite masking.

The models read summaries only. These calls do not demonstrate automated source tracing, image manipulation detection, original-data validation, or general fake-news accuracy. Cutoff-state agreement is agreement with declared assistant annotations, not an independently measured truth score.

The earlier v5 8/8 versus 4/8 counts came from unequal manually curated evidence packets. Those recorded counts remain unchanged, but interpreting them as proof of automated retrieval or a superior same-evidence policy was unsupported.

## Audit artifacts

- [Frozen protocol](../../../experiments/astra-same-evidence-v6-20260914/PROTOCOL.md)
- [Source mapping and publication dates](../../../experiments/astra-same-evidence-v6-20260914/SOURCE_AUDIT.json)
- [Complete parsed predictions and call receipts](../run-001/predictions.json)
- [Run manifest](../run-001/manifest.json)
- [Summary](SUMMARY.json), [integrity audit](AUDIT.json), [revealed outcome key](REVEALED_GOLD.json)

Registration SHA-256: `fa26fec50fb7da8eb82ae4788af24415d9d2a3d79ee8c36d647a51594075c00c`. Outcome-key SHA-256: `c393d7346e26f98a82b32c17304279b5cb5cee2a4d0db32b0958c7e9b185059c`.
Pre-call commit: `904d65eb34acb14265b656ec72d58949210e3af8`. Integrity checks passed: **True**.

Receipts contain parsed model outputs and CLI-reported usage, not full raw provider traces or a server-side checkpoint attestation.
