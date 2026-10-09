# Head-to-head: synthetic-dev-pool-003

Model `claude-fable-5.1 (serving agent via mailbox)` via `mailbox` (reasoning effort `agent`); harness 0.3.2 (4d3224442db6); harness config overrides `{}`.

Comparison: same evidence: the direct arm receives every cutoff-eligible material.

| Measure | Direct | Harness |
|---|---:|---:|
| Accuracy | 8/8 (100%) | 8/8 (100%) |
| False-claim recall | 4/4 | 4/4 |
| Origin named correctly (traceable) | 5/5 | 5/5 |
| Lineage path certified by harness | n/a | 5 |
| Abstained | 3 | 3 |
| Invalid outputs | 0 | 0 |
| Total tokens | 0 | 0 |
| Model calls | 8 | 36 |
| Mean input tokens per call | 0 | 0 |
| Packet chars sent by harness code | 22,568 | 211,270 |

Token ratio harness/direct: n/a (registered limit 6.0x, provider-reported). Packet-only estimate (chars/4 + output): 9.36x. A mean input count far above the packet size is the provider's fixed per-call overhead, paid once per call.

**Harness wins by the registered rule: False** (accuracy higher: False, all valid: True, within tokens: False).

| Case | Truth | Direct | Harness |
|---|---|---|---|
| c1 | unresolved | unresolved ✓ | unresolved ✓ |
| c2 | contradicted | contradicted ✓ | contradicted ✓ |
| c3 | unresolved | unresolved ✓ | unresolved ✓ |
| c4 | supported | supported ✓ | supported ✓ |
| c5 | contradicted | contradicted ✓ | contradicted ✓ |
| c6 | contradicted | contradicted ✓ | contradicted ✓ |
| c7 | contradicted | contradicted ✓ | contradicted ✓ |
| c8 | unresolved | unresolved ✓ | unresolved ✓ |

- A registered win on a small frozen pool is exploratory, not a population accuracy claim.
- Both arms see supplied snapshots only; neither arm retrieved from the open web.
- Historical cutoff isolation applies to supplied evidence, not to the model's training.
- Invalid or failed outputs count as incorrect; nothing was retried.
