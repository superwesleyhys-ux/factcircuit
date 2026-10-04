# Cycle 136 — outcome-blind Astra/Circuit judgment router

Goal: select a forecast per question using only evidence available at forecast time. The primary arm is raw Astra; Circuit is the alternative reasoning policy applied to the same base model. Mean paired Brier remains the evaluator. This cycle implements the deterministic selection boundary and tests its arithmetic. It does **not** elicit a new independent judgment or report a routing win.

For binary outcome `Y`, Astra forecast `a`, Circuit forecast `c`, and an independent subjective event estimate `q`, the expected Brier difference is

`E[(c-Y)^2-(a-Y)^2 | q] = (c-a)(c+a-2q)`.

When `c>a`, Circuit is preferable only if `q>(a+c)/2`. When `c<a`, Circuit is preferable only if `q<(a+c)/2`. At equality, neither has an expected Brier edge. The implemented router accepts a subjective interval `[q_low,q_high]` and switches to Circuit only if it wins **throughout** that interval. Missing or straddling judgments fall back to Astra. This is robust arithmetic conditional on the judge's interval, not proof that the interval is well calibrated.

The judgment must be made and sealed *before* viewing either arm's forecast, the outcome, or post-cutoff evidence. It must use the identical as-of source packet, cite the decisive observation and strongest counterevidence, and retain model/receipt/time/input hash. The caller, not `judgment_router.py`, has to enforce those provenance conditions. The judge may be a separately invoked Astra or authorized Jev, but no judge call was made this cycle and Jev availability is not evidence of judgment quality. An extra model call has a real cost; it should be evaluated against always-Astra and always-Circuit on fixed paired cases.

## Exposed four-case diagnostic

The following thresholds are calculated from cycle 135's already scored predictions. They are **not** a test of the router: every outcome is now known, so an elicited `q` on these cases would leak labels or be contaminated by model memory.

| Case | Astra `a` | Circuit `c` | Midpoint | Circuit requires | Historical winner |
|---|---:|---:|---:|---|---|
| cord-fresh-2 | .005 | .020 | .0125 | `q_low > 1.25%` | Astra |
| cord-fresh-1 | .030 | .040 | .0350 | `q_low > 3.50%` | Circuit |
| cord-fresh-5 | .020 | .020 | .0200 | No choice affects score | Tie |
| cord-fresh-6 | .040 | .060 | .0500 | `q_low > 5.00%` | Astra |

With no pre-outcome blind judgment, the actual router selects Astra on all four and obtains Astra's existing Brier `.47573125`; improvement is zero. Picking each arm using the *known outcomes* would reach `.47090625`, only `.004825` below Astra; this is an oracle ceiling, not achievable evidence. Because Circuit is higher on every non-tie case, the requested `Astra, Circuit, Astra` pattern is exactly an outcome classification problem. The tie is not a success: both arms assigned 2% to an event that occurred.

The one Circuit win has weak causal support: its rationale noted a six-animal sample and many significant results, whereas the later publisher notice cites image overlap/manipulation. Its one-point probability increase happened to reduce loss, but did not identify the later reason. On cord-fresh-6, Circuit correctly detected inconsistent train/test counts, yet a possible reporting correction is different from a qualifying retraction. This is why the judge must assess *event-path relevance*, not simply count anomalies or prefer more detailed prose.

## Validation and next admissible test

`newsverify/risk_benchmark/judgment_router.py` contains the decision rule; `tests/test_judgment_router.py` checks that point estimates minimize expected Brier loss and interval selections beat Astra at both ends across deterministic randomized cases, plus tie/fallback/invalid-input behavior. Full suite: **359 passed**; existing SQLite ResourceWarnings remain. No new Astra or Jev inference and no new outcomes were opened. The sealed cycle-130 forecasts still audit as 20 predictions with `brier=null`, awaiting outcomes after the 2026-10-04 target day. A judgment made today for those September 27 predictions would be late and must not be counted as issue-time routing.

For the next fresh prospective paired batch, freeze the judgment prompt, source packet, judge receipt and probability interval before either arm's predictions or labels are visible to the judge; then freeze all three outputs before resolution. Score per-case route choices and mean paired Brier against Astra and Circuit on the same fixed manifest, retaining failures and all calls. Group repeated questions by source/event to avoid treating correlated items as independent wins. Do not tune the rule using those held-out outcomes.

GitHub discovery this cycle: [RouteLLM](https://github.com/lm-sys/RouteLLM) implements and evaluates model routing with calibrated thresholds; its quality/cost figures concern its own benchmarks, not this forecast task. We reused the routing idea, not its pretrained router or performance claim.

Formal paper benchmark remains **0/200 admitted cases, 0/10 scored rounds**. No stable advantage over Astra is established.
