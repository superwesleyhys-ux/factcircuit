# Head-to-head fable-001: Claude Fable 5.1 as the model, served through a mailbox

Direct 8/8, harness 8/8; origins named 5/5 against 5/5; lineage certified by
the harness on 5 of 5; harness 36 model calls against 8; harness packets
211,270 characters against 22,568. A tie on the same-evidence pool, like
Astra's dev-003. See [SUMMARY.md](SUMMARY.md).

## How the model was reached

There is no Anthropic API credential in the environment this ran in, so the
model was served through [`experiments/head_to_head_mailbox.py`](../../experiments/head_to_head_mailbox.py):
the harness wrote every model call to a mailbox directory and a Claude Code
subagent (model `fable`, Claude Fable 5.1) answered each one, one at a time,
in the order the harness issued them. One subagent served the direct arm (8
requests, 2.5 minutes) and a second, fresh one served the harness arm (36
requests, 18 minutes). Neither saw the other's mailbox.

Blinding, as instructed and as the transport enforces by construction:

- the agent was told to read only files inside its mailbox, never the case
  file, the repository or the filesystem, and to use no network;
- the sealed gold file lived outside the sandbox checkout that the harness ran
  from (`register` refuses a gold file inside the checkout);
- case and target ids are opaque (`c1`..`c8`); `CASE_KEY.json` maps them to
  the descriptive names of `synthetic-dev-pool-002` and was written outside
  the sandbox before the run.

## What this run is not

- Not an API run. The subagent is a Claude Code agent with a system prompt and
  tools, told to behave as a closed-packet model; provider usage is not
  reported, so the registered token rule cannot be evaluated and the
  `Total tokens` rows read 0. `input_chars`/`output_chars` are measured.
- Not a reasoning-effort match with dev-003 (`low` on Astra; the subagent's
  effort is whatever the harness gave it).
- Not evidence of real-news accuracy: the pool is synthetic.

## Reproduce

```bash
python experiments/head_to_head.py register examples/head_to_head_dev_pool.json \
  --gold /outside/the/checkout/gold.json --output runs/fable-002 --max-token-ratio 6
python experiments/head_to_head_mailbox.py runs/fable-002 --cases examples/head_to_head_dev_pool.json \
  --arm direct --mailbox /tmp/mail --model-label "claude-fable-5.1 (serving agent via mailbox)"
# in parallel: an agent serves /tmp/mail/direct, then /tmp/mail/harness (see docs/HEAD_TO_HEAD.md)
python experiments/head_to_head.py score runs/fable-002 --cases examples/head_to_head_dev_pool.json \
  --gold /outside/the/checkout/gold.json
```

The gold for this pool is now published as
`examples/head_to_head_dev_pool_gold.example.json`; a run on this pool after
this date is a mechanics check, not a blind one.
