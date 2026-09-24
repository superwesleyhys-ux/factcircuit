---
name: continuous-growth
description: Use when the user asks to iteratively improve reusable harnesses or skills, continue a long-running improvement goal, or transfer an evidence-gated improvement workflow across projects.
---

# Continuous Growth

Improve a durable goal, not the number of loops. The model, evaluation target,
source-evidence rules, credentials and permission boundaries remain unchanged.

## One owner, small working set

Use the project's existing orchestrator. Prefer the already installed OMX
`ultragoal` for durable coding goals and `autoresearch` for registered research.
OMC/Claude or one Ralph implementation can substitute when actually available.
Never nest OMX, OMC, multiple Ralph loops and LoopX as competing writers. A
LoopX-owned project keeps LoopX as its owner; GrowthKit is only an artifact gate.
Check the real CLI/runtime before reporting a harness as connected. A file or
config entry proves neither native-host discovery nor successful inference.

Load at most three relevant worker skills for a step. Use JevHarness for
structured routing, judgment and reusable task-specific decision graphs when
its runtime is genuinely available. It is not the primary authoring model.
For engineering defects route to systematic-debugging and TDD; for experiments
to autoresearch; for acceptance to verification-before-completion. Missing
capabilities become explicit blockers, not silently invented successful calls.

## Execute a bounded slice

Read `growthkit/README.md` and `growth.json`. Verify the frozen case set, evaluator
assets, baseline artifact, trusted command allowlist and remaining call budget.
Use the existing model tunnel; do not switch to paid API fallback, alter keys,
relax sandboxing or buy quota as part of this skill.

Run `python growthkit/engine.py status --project .` first. Then, when permitted,
run `python growthkit/engine.py run --project . --cycles 3`. The engine proposes
one JSON harness artifact per cycle, compares incumbent and candidate with the
same evaluation case IDs and limits, and retains only measured improvements.
The engine does not install, execute or deploy candidate code automatically.
The trusted task adapter owns all legal actions and scoring.

A rejected artifact does not replace the incumbent. A duplicate artifact is
not reevaluated. Use the retained evidence and last three decision receipts to
form a materially different hypothesis, rather than rewriting the same prompt.
At stagnation, reconsider the failure mechanism or capability gap. Search
primary upstream documentation only for a concrete missing capability; do not
auto-install arbitrary new packages or load a catalog of unused skills.

## Gates and continuation

Lifetime call reservations never reset on ordinary restart. They count adapter
process calls, not dollars or all internal model requests. Preserve the trusted
adapter's existing model/token/time caps. A reported usage limit is not a
sandbox or provider-side spending limit.

On auth, quota, permission, protocol or timeout failure, stop that lane. After
independently resolving and recording the blocker, use
`python growthkit/engine.py resume --project . --reason "specific verified repair"`.
Resume does not refund calls, change assets, clear an in-flight transaction,
or enlarge budgets. Never use a cosmetic reason to bypass a real gate.

Create `.growth/STOP` to stop between commands. Never delete an active lock.
An interrupted transaction requires review of the saved phase and possible
side effects; it is not replayed automatically. Contract, scorer, controller or
case-set changes require a newly reviewed goal/worktree with a fresh baseline,
not editing the existing digest to pretend the old experiment is unchanged.

## FactCircuit-specific constraints

Keep original claim spans, qualifiers, provenance and cutoff eligibility. Reuse
existing verified evidence; expand only a documented gap. Do not reopen the
registered historical experiment as an unlimited training set. Development and
selection results are not unseen accuracy estimates. Do not change its fixed
round count, gold, scorer or source scope to achieve a desired result.

## Report the actual layer

Distinguish source present, skill file installed, runtime discovered, real
inference completed, development improvement measured, independent evaluation
passed, and production deployment. Synthetic subprocess tests validate this
controller only. Never turn a self-assigned score, archived response or mock
adapter into a claim of live model or application improvement.
