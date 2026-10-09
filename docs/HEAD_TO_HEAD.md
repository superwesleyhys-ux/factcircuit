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
  set of origins the harness named. Whether it also certified an evidenced
  lineage path (`provenance_status` of `original_material_located`) is
  reported separately, since the direct arm never has to prove a path.

Gold `original_version_ids` may list alternatives (`[["record-v2"],
["record-v1", "record-v2"]]`) where a record and its own same-URL revision
are both acceptable answers; `null` marks an untraceable case.

Materials that are ineligible at the cutoff are excluded from both arms. Both
arms are supplied-snapshot only; neither retrieves from the open web.

`register --direct-scope` picks which question a run answers. `pool` (the
default) hands the direct arm every eligible material: a same-evidence test
of reasoning and bookkeeping, on which a capable model is not expected to
lose. `initial` hands the direct arm only the case's `initial_version_ids`,
what a user would paste, so the difference measured is retrieval from the
pool; it is a test of the whole system, not of the prompt, and its summary
says so. Do not present an `initial` run as a same-evidence result.

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
model-call cap, the token ratio that a win must respect, and any
`--harness-config KEY=VALUE` overrides (TraceConfig fields applied to every
case in the harness arm, so one case file can be registered twice to A/B a
setting such as `defer_verification_until_provenance_complete=true`). `run`
refuses a case file whose hash changed and writes `details/<case>-<arm>.json`
with the full harness report, or the direct packet and raw response, for
failure analysis. `score` refuses a gold file whose hash differs from the
registration, and refuses arms that were run with different settings.

## Serving the model from an agent (mailbox transport)

A model with no API tunnel here, a Claude session acting as a subagent, a
person, or any agent with file access, can still be the model inside both
arms: `experiments/head_to_head_mailbox.py` writes every call to a mailbox
directory as `NNNN.request.json` (`stage`, `instructions`, `evidence`,
`schema`) and waits for `NNNN.response.json`. Run one driver per arm with
its own mailbox; the serving agent loops over unanswered requests, answers
from `evidence` only, and stops when `DONE` appears. Usage is not reported, so
the token rule cannot be evaluated; `input_chars` and `output_chars` are.
Blinding is the operator's job: the agent must be permitted to read only its
mailbox, the gold file must sit outside anything it can reach, and case and
target ids must be opaque, because the harness packets carry `target.id`.

## Reading the token numbers

The rule compares provider-reported input plus output tokens, because that is
what the model actually processed. Through the local Codex route every call
carries the CLI's own fixed prompt (about 11k input tokens per call in
dev-001, with packets of a few hundred characters), so a multi-call harness
pays that overhead once per call and a 2× ratio is unreachable for any case
that fetches even one document. `SUMMARY.md` therefore also shows model calls,
mean input tokens per call, the characters the harness code itself sent, and a
packet-only estimate (chars/4 plus output tokens). Register a ratio that
matches the tunnel you are measuring; the API tunnels carry no such overhead.

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
- `examples/head_to_head_dev_pool.json` (`synthetic-dev-pool-003`, opaque
  ids): eight synthetic cases built around the surfaces above: a verbatim copy at a second URL, a same-URL revision
  before the cutoff, a decoy record with the same number, two-hop lineage
  with and without a contradicting original, a site qualifier mismatch, a
  corrected notice, and a notice with no record.
- `examples/*_gold.example.json`: matching labels. Copy one outside the
  repository before registering; `register` refuses a gold file inside the
  checkout.

## What the runs have shown

- dev-002a versus dev-002b (same pool, same model, verification deferred in
  b): early verification on an incomplete chain produced an escalating
  standard. On `two-hop-supported` the verifier asked for the summary, then
  the record, then, with the original record in hand stating the result,
  for "underlying readings, method or reliability evidence", and left the
  claim unresolved. With verification deferred until the chain was complete
  the same model answered `supported` on first sight. The double loop now
  defers by default and the verifier prompt states the sufficiency standard
  and forbids restating a gap with a stricter requirement; the news path
  keeps per-round verification until it is measured there.
- dev-003 (deferral default, prompt amendment): 8/8 against 8/8, origins
  5/5 against 5/5, 38 calls, 5.11×. With `input_chars` repaired the summary
  shows the harness's own packets at about 9× the direct packet characters:
  the harness's cost beyond the provider's fixed per-call overhead is the
  context it carries (prior analyses, verification history, prior-material
  metadata), not the documents.
- fable-001 (Claude Fable 5.1 served through the mailbox transport, opaque
  ids): direct 8/8, harness 8/8, origins 5/5 against 5/5, lineage certified
  5/5, 36 calls, packets 9.4× the direct packet characters. The same tie as
  Astra, from a different model family, with the id leak removed.
- Caveat on dev-002 and dev-003: the pool's case ids were descriptive
  (`two-hop-contradicted-by-record`) and the harness packets carry
  `target.id`, so the harness arm could read the label in the id; one
  version id (`copy-of-notice-a`) named its own role to both arms. The runs
  stand as published with this caveat. `synthetic-dev-pool-003` is the same
  eight cases with opaque ids (`c1`..`c8`, `bulletin-mirror`); its key is in
  `reports/head-to-head-fable-001/CASE_KEY.json`.
- On a pool where every eligible material is handed to the direct arm, the
  bare model has not lost a case in four runs across two model families. The harness's remaining
  advantage to measure is on pools the direct arm cannot be handed whole:
  live retrieval from a page, decoys at scale, and cost.

## Published runs

- [dev-001](../reports/head-to-head-dev-001/SUMMARY.md): `gpt-6-astra`, local
  route, low effort, the three-case example pool. 3/3 versus 3/3, origins 2/2
  versus 2/2; harness 146,463 tokens versus 33,786 (4.34×, about 11k of which
  is fixed Codex overhead on each of the 5 versus 1 calls). The registered
  rule was not met. Synthetic; checks mechanics, not accuracy.
- [dev-002a](../reports/head-to-head-dev-002a/SUMMARY.md) and
  [dev-002b](../reports/head-to-head-dev-002b/SUMMARY.md): the eight-case dev
  pool, `gpt-6-astra`, local route, low effort, registered ratio 6×. Direct
  8/8 in both. Harness 7/8 in 47 calls (6.47×) without deferral; 8/8 in 38
  calls (5.13×) with it. Origins named correctly 4/5 for every arm; the
  miss was a same-URL revision scored against a single acceptable set, which
  the gold format now allows to be listed as alternatives. Synthetic.
- [dev-003](../reports/head-to-head-dev-003/SUMMARY.md): same pool and model
  with deferral as the default and the amended verifier prompt. 8/8 against
  8/8, origins 5/5 against 5/5, lineage certified on 4 of 5, 38 calls,
  5.11×; harness packets 199,206 characters against 22,361. Synthetic; id
  leak caveat above.
- [fable-001](../reports/head-to-head-fable-001/README.md): Claude Fable 5.1
  served through the mailbox transport on the opaque-id pool. 8/8 against
  8/8, origins 5/5 against 5/5, lineage certified 5/5, 36 calls; tokens not
  measurable through that transport. Synthetic.

Synthetic pools check the mechanics. They are not real-news accuracy evidence.
