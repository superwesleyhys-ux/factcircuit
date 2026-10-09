# Head-to-head: synthetic-head-to-head-example

Model `None` via `local`; harness 0.3.2 (55c3fa7f587a).

| Measure | Direct | Harness |
|---|---:|---:|
| Accuracy | 3/3 (100%) | 3/3 (100%) |
| False-claim recall | 1/1 | 1/1 |
| Origin correct (traceable) | 2/2 | 2/2 |
| Abstained | 1 | 1 |
| Invalid outputs | 0 | 0 |
| Total tokens | 33,786 | 146,463 |

Token ratio harness/direct: 4.34x (registered limit 2.0x).

**Harness wins by the registered rule: False** (accuracy higher: False, all valid: True, within tokens: False).

| Case | Truth | Direct | Harness |
|---|---|---|---|
| supported-with-record | supported | supported ✓ | supported ✓ |
| contradicted-by-record | contradicted | contradicted ✓ | contradicted ✓ |
| record-unavailable-at-cutoff | unresolved | unresolved ✓ | unresolved ✓ |

- A registered win on a small frozen pool is exploratory, not a population accuracy claim.
- Both arms see supplied snapshots only; neither arm retrieved from the open web.
- Historical cutoff isolation applies to supplied evidence, not to the model's training.
- Invalid or failed outputs count as incorrect; nothing was retried.
