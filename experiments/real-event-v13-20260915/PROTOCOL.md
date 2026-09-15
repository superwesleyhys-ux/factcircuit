# Real-event diagnostic: Aspiration's 2021 revenue announcement

## Purpose and eligibility

Run one new, nonacademic real event through direct Astra and the current FactCircuit harness. The fixed claim is the reported FY2021 total revenue in Aspiration's February 17, 2022 company announcement. This is a real company press release, not a synthetic fixture or an independently reported investigation.

This is a **retrospective real-event diagnostic, not a clean first-revelation holdout**. A July 10, 2024 Bloomberg investigation had already raised questions about Aspiration's deals. Subsequent official admission and sentencing occurred in 2025–2026. It does not fully satisfy the earlier requirement that falsity first become public in 2025–2026. Selection was made with knowledge of the later outcome. That limitation cannot be cured by withholding the later documents from inference.

## Frozen inputs and execution

- One fixed case in `cases.json`; no replacement after model dispatch.
- The historical cutoff is December 31, 2024, 23:59:59 UTC. Only validated pre-cutoff Internet Archive captures enter either arm. Publication dates alone cannot establish availability. The chosen seed is a complete January 4, 2023 archived company release, with its original qualifications, tables, routine correction, navigation and links retained.
- The release's same-day correction changes the reference year in an outlook table. That routine correction is not evidence of the later revenue fraud.
- The collector rejects unavailable captures, post-cutoff redirects, unsupported content, and size overflow. There is no fallback to the current website and no manual evidence substitution. Seed discovery attempts and failures are in `PREFLIGHT.json`.
- Both arms use `gpt-6-astra`, low reasoning, through the local Codex-login route. Inference itself remains remote. Installed skill catalogs, shell, web search, plugins, apps and multi-agent tools are disabled in isolated model calls. The host fetches only the model's eligible observed links.
- Both start with identical seed text and the same link-only tools: at most 3 document attempts including the seed, 2 retrieval decisions, and 16 model calls per arm including final output. No bibliography or open-web search is available. This tests bounded source reading, not unrestricted investigative search.
- Direct Astra chooses sources and returns the common final schema. The harness additionally uses the production `research_mode="claim"` workflow and native incremental decomposition/selection/verification, with 8 native calls reserved. `claim` mode still executes the actual research workflow; it omits V12's wider timeline and causal analysis. Production code is unchanged.
- Equal ceilings are not equal actual compute. The direct driver has at most 3 reachable stages (2 retrieval decisions and a final answer); the harness can use up to 16 with its extra research and verification. This is a workflow comparison, not a matched-compute ablation.
- Direct runs first and harness second. Independent collectors start empty; sharing a byte-verified raw seed cache does not pre-populate documents or decisions. Archive availability may vary between independent later fetches.
- Per-stage exact stdin, schemas, raw CLI responses, failures and token usage are retained privately. First model failure stops that arm; no automatic model retry or rerun to obtain a better answer. Missing usage remains unknown.
- Code, configuration, seed and this protocol are hashed before model dispatch. Later-outcome adjudication is stored separately; only its commitment hash is registered. The runner does not load outcomes. The model's pretrained knowledge cannot be erased or independently proven absent.

## Predeclared evaluation

1. Report completed/failed model calls, final validation status, full input/output tokens and source-fetch errors for both arms.
2. Validate citations as exact bounded substrings of actually fetched historical documents, and origin chains against fetched observed links. Audit the native harness separately, including unresolved gaps and accepted source analyses. A passed output schema does not imply a verified chain.
3. Evaluate underlying-claim verdicts against each arm's own historical evidence. Keep company attribution separate from the truth of its reported revenue. An issuer's announcement alone does not independently establish authenticity.
4. Record the shared forecast: whether official correction, finding or admission in 2025–2026 will materially undermine the fixed public claim. Compare the categorical forecast with the separately retained later outcome only after predictions are completed and hashed. Audit whether any elevated-risk forecast has specific contemporaneous support. Unsupported guesses are not validated early detections.
5. Keep `fabrication_established` separate: this requires supplied contemporaneous proof of intentional fabrication. Abstention, suspicion and eventual hindsight agreement do not establish prior fraud detection.
6. The seed already is the issuer's distributed release. Correctly identifying it is not an upstream discovery. Reading a corporate homepage is not independent financial verification. The tools cannot promise access to all evidence public by the cutoff, including the 2024 investigation.
7. There is only one outcome-selected positive event and no controls. Report case-level results only: no overall accuracy, specificity, superiority, clean holdout, or forecasting-success-rate claim.

## Preservation

Results use a new directory and preserve failures. No test-targeted prompt tuning after dispatch. Public summaries, if subsequently published, must include the eligibility limitation and failures; full third-party articles and private model diagnostics remain local.
