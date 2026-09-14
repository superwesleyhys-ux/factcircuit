# Astra v6: new papers, identical evidence, two decision policies

Frozen before inference on 2026-09-14. This is an **exploratory retrospective,
same-evidence decision-stage comparison**, not an automated retrieval test or
an independently blinded holdout. The candidate uses a new chronological
artifact-and-rebuttal policy; the direct baseline gets the same task definitions
and evidence with no prescribed analysis sequence. Neither runs the production
multi-stage harness. No training or tuning on these outputs is planned.

## Data and temporal boundary

Eight biological research papers published before 2024 were purposively selected:
four with 2025–2026 publisher retractions for data reliability and four with
pre-cutoff image/data corrections and no retraction located through 2026-09-14.
All eight original DOIs were checked against prior tracked experiment/report
text. This does not establish independence between all authors or source families.
One later-retracted case has no pre-cutoff concern located in the checked records.
It remains in the denominator even if neither model could anticipate its outcome.

Both arms receive exactly the **same three passages** per case, drawn only from
records dated on or before **2024-12-31**. These are assistant-curated paraphrases
of abstracts, dated public comments and publisher correction notices. Titles,
authors, URLs and DOIs are omitted from model inputs. Case IDs and order are
shuffled with fixed seeds, without encoding the outcome category.

The source editor knew the later outcomes when selecting and summarizing cases.
Sealing a label file prevents runtime access; it does **not** undo selection bias,
editorial information leakage, or model training-data contamination. Current
source pages were retrieved in 2026; pre-cutoff content is reconstructed from
publication dates and visible comment edit histories, not verified historical
snapshots. The original abstracts are author claims, not laboratory validation.
The model sees no images, raw experiments, or post-cutoff retraction text.

`SOURCE_AUDIT.json` maps passages to public sources and local capture hashes.
Full third-party captures stay local. The later outcome key stays outside the
repository during inference; its hash is committed in `REGISTRATION.json`.
After all calls finish, the unchanged key is published with results.

## Fixed execution

- Requested model: `gpt-6-astra`, low reasoning, local Codex-login route.
- “Local” means the CLI/login route; inference uses a remote service.
- Sixteen calls: one per case per arm, alternating the first arm within each pair.
- Timeout: 240 seconds per call. No retries, fallback, or repeated-run selection.
- Fresh ephemeral isolated working directory per call; web, shell, apps, plugins,
  multi-agent tools and installed skill catalogs disabled in the existing transport.
- Only the designated policy, evidence and schema are passed to each call.
- Same output schema and 180-word rationale limit in both arms.
- Hash corpus, sources, policies, runner, scorer, safeguards and transport before
  calls. Push registration before inference. Log each attempted step durably.
- Usage comes from CLI-reported input/output tokens; include failed calls when
  usage is available and report unknown usage explicitly. The CLI does not
  independently attest a server-side checkpoint or record full raw provider traces.

## Registered scoring

**Primary: later-outcome balanced accuracy** of the `risk` forecast. The positive
target is a located 2025–2026 data-reliability retraction; the negative target is
no located retraction through the audit date. Elevated predicts positive;
ordinary predicts negative. `insufficient_evidence`, failed and invalid responses
count as incorrect for this predictive metric, with separate abstention/failure
counts. Sensitivity and specificity always use all four cases in each group.
An abstention on a genuinely underdetermined case is not a false factual claim.

Secondary measures:

- Agreement with assistant-labelled **cutoff record states**: unresolved concern,
  addressed concern, or no specific concern. This measures interpretation of
  supplied summaries, not independently established scientific truth.
- Unsupported assertions of established deliberate fabrication. None of these
  packets establishes intent; allegations and corrections are not proof of fraud.
- Valid outputs, reference-ID validity, forecast coverage, paired discordances,
  exact two-sided McNemar p-value, and input/output token totals.

The observed candidate score must be strictly higher, all 16 outputs valid, and
all integrity checks pass to label it an **observed win on these eight cases**.
A tie or loss is retained. Token use is recorded, not a selection objective.
No result from this small selected sample warrants a broad superiority claim.

## Limits and prior-result interpretation

Right-censored controls are not verified-authentic papers; follow-up periods
differ. All controls here come from one publisher family and all positive
outcomes from another, although publisher identities are hidden in packets.
Other stylistic/domain clues and the purposive 4/4 prevalence can bias results.
The source editor is also the policy author and cutoff-label annotator.
The model may recognize anonymized descriptions or remember later events.
This experiment does not measure independent source finding, download success,
image analysis, full-text verification, or general fake-news detection.

The v5 runner supplied manually curated extra traces only to its candidate arm.
Its 8/8 vs 4/8 output counts remain recorded, but those counts do not demonstrate
automated retrieval or same-evidence policy improvement. This v6 protocol fixes
the evidence disparity for the new comparison; it does not retroactively
validate v5's system-level interpretation.

## Reproduce

From the repository root (the second command makes real model calls):

```sh
python -B experiments/astra-same-evidence-v6-20260914/run_test.py --preflight-only
python -B experiments/astra-same-evidence-v6-20260914/run_test.py --output /path/to/new-run
python -B experiments/astra-same-evidence-v6-20260914/summarize_test.py --run /path/to/new-run --gold /path/to/revealed-gold.json --output /path/to/new-score
```

Existing output directories are refused. Additional replications must be labelled
new experiments rather than substitutes for the registered run.
