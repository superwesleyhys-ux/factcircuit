# Head-to-head: synthetic-dev-pool-002

Model `gpt-6-astra` via `local` (reasoning effort `low`); harness 0.3.2 (bd81d18571b5); harness config overrides `{}`.

| Measure | Direct | Harness |
|---|---:|---:|
| Accuracy | 8/8 (100%) | 8/8 (100%) |
| False-claim recall | 4/4 | 4/4 |
| Origin named correctly (traceable) | 5/5 | 5/5 |
| Lineage path certified by harness | n/a | 4 |
| Abstained | 3 | 3 |
| Invalid outputs | 0 | 0 |
| Total tokens | 90,602 | 462,747 |
| Model calls | 8 | 38 |
| Mean input tokens per call | 11,206 | 11,939 |
| Packet chars sent by harness code | 22,361 | 199,206 |

Token ratio harness/direct: 5.11x (registered limit 6.0x, provider-reported). Packet-only estimate (chars/4 + output): 9.00x. A mean input count far above the packet size is the provider's fixed per-call overhead, paid once per call.

**Harness wins by the registered rule: False** (accuracy higher: False, all valid: True, within tokens: True).

| Case | Truth | Direct | Harness |
|---|---|---|---|
| copy-is-not-a-second-source | unresolved | unresolved ✓ | unresolved ✓ |
| record-revised-before-cutoff | contradicted | contradicted ✓ | contradicted ✓ |
| decoy-record-same-number | unresolved | unresolved ✓ | unresolved ✓ |
| two-hop-supported | supported | supported ✓ | supported ✓ |
| two-hop-contradicted-by-record | contradicted | contradicted ✓ | contradicted ✓ |
| qualifier-site-mismatch | contradicted | contradicted ✓ | contradicted ✓ |
| notice-corrected-before-cutoff | contradicted | contradicted ✓ | contradicted ✓ |
| notice-only-no-record | unresolved | unresolved ✓ | unresolved ✓ |

- A registered win on a small frozen pool is exploratory, not a population accuracy claim.
- Both arms see supplied snapshots only; neither arm retrieved from the open web.
- Historical cutoff isolation applies to supplied evidence, not to the model's training.
- Invalid or failed outputs count as incorrect; nothing was retried.
