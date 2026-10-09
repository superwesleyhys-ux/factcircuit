# Head-to-head: synthetic-dev-pool-002

Model `gpt-6-astra` via `local` (reasoning effort `low`); harness 0.3.2 (4c39b9e05959); harness config overrides `{}`.

| Measure | Direct | Harness |
|---|---:|---:|
| Accuracy | 8/8 (100%) | 7/8 (88%) |
| False-claim recall | 4/4 | 4/4 |
| Origin correct (traceable) | 4/5 | 4/5 |
| Abstained | 3 | 4 |
| Invalid outputs | 0 | 0 |
| Total tokens | 90,626 | 586,788 |
| Model calls | 8 | 47 |
| Mean input tokens per call | 11,205 | 12,223 |
| Packet chars sent by harness code | 22,361 | 0 |

Token ratio harness/direct: 6.47x (registered limit 6.0x, provider-reported). Packet-only estimate (chars/4 + output): 1.87x. A mean input count far above the packet size is the provider's fixed per-call overhead, paid once per call.

**Harness wins by the registered rule: False** (accuracy higher: False, all valid: True, within tokens: False).

| Case | Truth | Direct | Harness |
|---|---|---|---|
| copy-is-not-a-second-source | unresolved | unresolved ✓ | unresolved ✓ |
| record-revised-before-cutoff | contradicted | contradicted ✓ | contradicted ✓ |
| decoy-record-same-number | unresolved | unresolved ✓ | unresolved ✓ |
| two-hop-supported | supported | supported ✓ | unresolved |
| two-hop-contradicted-by-record | contradicted | contradicted ✓ | contradicted ✓ |
| qualifier-site-mismatch | contradicted | contradicted ✓ | contradicted ✓ |
| notice-corrected-before-cutoff | contradicted | contradicted ✓ | contradicted ✓ |
| notice-only-no-record | unresolved | unresolved ✓ | unresolved ✓ |

- A registered win on a small frozen pool is exploratory, not a population accuracy claim.
- Both arms see supplied snapshots only; neither arm retrieved from the open web.
- Historical cutoff isolation applies to supplied evidence, not to the model's training.
- Invalid or failed outputs count as incorrect; nothing was retried.
