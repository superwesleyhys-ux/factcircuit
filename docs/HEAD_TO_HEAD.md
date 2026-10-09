# Head-to-head protocol: direct model versus the harness

`experiments/head_to_head.py` runs one model, through one tunnel, in two arms
over one frozen case pool, and scores both against a gold file that is sealed
by hash before inference. It exists so the question "does the harness beat the
bare model?" is answered by a registered run rather than by a selected one.

## Arms

- **Direct**: one call per case. The packet holds every material that is
  eligible at the target's `as_of` cutoff, verbatim, plus the target. The model
  returns a four-state verdict, quoted basis spans (validated against the
  packet), and the version IDs it considers the original record.
- **Harness**: `run_double_loop_trace` over the same materials with the same
  tunnel and model. The verdict is `fact_status`; the original record is the
  located origin set when `provenance_status` is `original_material_located`.

Materials that are ineligible at the cutoff are excluded from both arms. Both
arms are supplied-snapshot only; neither retrieves from the open web.

## Procedure

```bash
# 1. Freeze the pool and seal the gold file (kept outside the repository).
python experiments/head_to_head.py register CASES.json --gold ~/sealed/gold.json --output runs/h2h-001

# 2. Commit and push runs/h2h-001/REGISTRATION.json before inference.

# 3. Run both arms with one tunnel and model.
python experiments/head_to_head.py run runs/h2h-001 --cases CASES.json --tunnel local            # Codex login
python experiments/head_to_head.py run runs/h2h-001 --cases CASES.json --tunnel anthropic --model MODEL

# 4. Open the sealed gold file and score.
python experiments/head_to_head.py score runs/h2h-001 --cases CASES.json --gold ~/sealed/gold.json
```

`register` records the SHA-256 of the case file and of the gold file, the
harness version and commit, the case order, an alternating arm order, the
model-call cap, and the token ratio that a win must respect. `run` refuses a
case file whose hash changed. `score` refuses a gold file whose hash differs
from the registration, and refuses arms that were run with different settings.

## Registered success rule

The harness wins a run only if all three hold:

1. its accuracy is strictly higher than the direct arm's;
2. every harness output is valid (a failed or malformed output counts as wrong
   and is never retried);
3. its input-plus-output token total is at most the registered ratio of the
   direct arm's (default 2×).

`SUMMARY.md` reports accuracy, false-claim recall, origin accuracy on traceable
cases, abstentions, invalid outputs, tokens, the per-case pairing, and the rule
verdict. Ties are ties.

## Building a case pool that can show a difference

The harness does nothing the bare model cannot do on a one-document packet.
Where it can differ is bookkeeping the model has to do implicitly: following a
notice to its producing record, keeping a post-cutoff revision out, treating
copies of one wire story as one source, and reopening an earlier analysis when
a later document contradicts it. A pool that contains only self-contained
packets will tie. A pool where the answer depends on which material is the
original, or on which version was available at the cutoff, is where a
difference can be measured. Label those features in the gold file
(`original_version_ids`) so the origin metric reports them.

Keep event families disjoint from any earlier published run, write the gold
file before inference, and never edit the case file after registration.

## Files

- `examples/head_to_head_cases.json`: three synthetic cases (supported,
  contradicted by the record, record unavailable at the cutoff).
- `examples/head_to_head_gold.example.json`: matching labels. Copy it outside
  the repository before registering; `register` refuses a gold file inside the
  checkout.

Synthetic pools check the mechanics. They are not real-news accuracy evidence.
