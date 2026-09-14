# Trace a news item to its sources

`trace-news` collects source text, investigates the news item's upstream
references, and runs the double loop separately for each claim. The report keeps
two questions separate: **where did this claim come from, and what does the
available evidence establish?** An original press release can contain a false
claim; several copies of it do not provide independent confirmation.

When the input URL can be fetched, the harness follows its observed upstream
links before optional background research can consume the document allowance.

## Run it

Save a JSON input as `news.json`:

```json
{
  "news": [
    {
      "id": "news-1",
      "text": "Paste the news text here.",
      "url": "https://publisher.example/article",
      "claims": ["Replace this with one specific claim from the news."]
    }
  ],
  "config": {
    "depth": 1,
    "max_queries": 2,
    "max_claims": 3,
    "max_documents": 8,
    "max_searches": 3,
    "max_origin_depth": 2,
    "max_origin_calls": 10,
    "fetch_timeout": 15
  }
}
```

Replace the example text, claim and URL with the actual news item. Then run:

```bash
python -m newsverify trace-news news.json --output reports/news-trace.json
```

The default `local` tunnel runs the installed Codex CLI with its existing login
and uses `gpt-6-astra` unless `--model` or `FACTCIRCUIT_MODEL` selects another
locally available model.
Local orchestration does **not** mean offline model weights. The local default
is independent of the user's global model setting; `--model` and
`FACTCIRCUIT_MODEL` provide explicit project/model overrides. Reasoning effort
continues to use the configured global setting when omitted.

To select the API explicitly, set `OPENAI_API_KEY` in your environment and run:

```bash
python -m newsverify trace-news news.json --tunnel api --model YOUR_API_MODEL --output reports/news-api.json
```

Neither tunnel silently switches to the other after an error. API access and
model availability are separate from the Codex login. See
[model execution tunnels](MODEL_TUNNELS.md) for authentication and isolation.

## Input and limits

The top-level `news` list accepts 1–20 items, each with a unique nonempty `id`
and `text` of at most 50,000 characters. Optional fields are:

| Field | Meaning |
| --- | --- |
| `url` | Input article's HTTP(S) URL, without embedded credentials. |
| `claims` | Explicit claims to assess, each at most 10,000 characters. If omitted, the research stage extracts up to three; if extraction fails, the supplied text is retained as the fallback target. |
| `materials` | A supplied, finite snapshot pool. Its presence disables live source search and fetching for this item. |
| `source_version_id` | The input article's version ID within the eligible snapshot pool. Use this with historical materials to anchor its source chain. |
| `as_of` | An ISO timestamp with timezone. Requires supplied `materials`; current pages cannot be backdated. |

All `config` fields are optional. Unknown configuration and news-item fields are
rejected. Limits apply to each item except the global model-call cap:

| Setting | Default | Accepted range / effect |
| --- | ---: | --- |
| `research_mode` | `full` | `full` runs the complete news report. `claim` focuses source research on exactly one explicit claim per news item; source research, synthesis and the formal double loop remain active. |
| `depth` | 1 | 0–3; causal research depth. Zero disables causal expansion. |
| `max_queries` | 2 | 1–3; planned source-search angles. |
| `max_claims` | 3 | 1–5; explicit claims must fit; automatic extraction remains at most three. |
| `max_documents` | 8 | 1–20; live source-fetch attempt cap and per-claim document limit. Failed fetch attempts consume the live cap. |
| `max_searches` | 3 | 0–8; live search-query attempt cap, including causal searches. |
| `max_origin_depth` | 2 | 0–5; upstream-link discovery rounds, selecting at most two observed links per round. |
| `max_origin_calls` | 10 | 1–30; model-call cap for each claim's double loop, also subject to the remaining global budget. |
| `fetch_timeout` | 15 | 1–60 seconds per built-in source request, including its redirects. A search may make several such requests. |

The CLI's `--max-model-calls` defaults to **40 across the entire batch**, including
research, link selection, all claims and failed model invocations. Items and
claims run in input order. The cap limits attempted model calls; it does not
promise every item will finish or divide the allowance equally. Later claims
retain explicit budget errors when no calls remain. Use smaller batches or
`depth: 0` when prioritizing claim verification within a small allowance.
This disables causal expansion only; `max_origin_depth` independently controls
following the article's upstream references.

For a single explicit claim, set `"research_mode": "claim"` in `config` to omit
causal analysis, grounding, timelines, perspectives and the explanatory draft.
Each item must supply exactly one claim; a batch may still contain several
items. The exact supplied claim remains the research and verification target.
The report records the omitted stages as intentional skips and includes its
research scope. Empty presentation fields in this mode do not mean those
checks passed. Errors in retained stages still produce a partial or failed item.

`--timeout` defaults to 90 seconds per model call. Neither timeout nor call count
is a token, money or total-runtime ceiling. Research stages reuse evidence
context, and the installed CLI may have internal transport retries. The active
causal graph also has fixed limits of two children per node and eight total
nodes, including the input event.

## Historical evidence

For an earlier cutoff, supply the exact text version that was available then.
Each material has the following shape:

```json
{
  "version_id": "article-v1",
  "url": "https://publisher.example/article",
  "content": "Exact retained source text, including relevant attribution.",
  "retrieved_at": "2026-09-08T12:00:00Z",
  "published_at": "2023-06-01T12:00:00Z",
  "available_at": "2023-06-02T00:00:00Z",
  "availability_basis": "Replace with evidence that this exact version was available by this time.",
  "issuer": "Publisher"
}
```

Place materials under the news item, set `source_version_id: "article-v1"` and
an `as_of` such as `"2023-12-31T23:59:59Z"`. The timestamps above illustrate the
format, not a verified historical source. `published_at` is optional; dated
publication metadata alone does not establish availability of the captured
version. An explicit `available_at` and nonempty `availability_basis` are needed
for admission. Unknown or later versions are excluded before either the research
stages or formal trace sees their text. Future or unavailable sources appear in
`execution.pool_exclusions`.

Up to 100 supplied versions are accepted per item, with unique version IDs.
Fetching a page today records today's availability even if its page metadata
names an older publication date. Snapshot mode disables source-network activity;
the selected model tunnel may still use a hosted model.

## Read the result

- `claims[].fact_status` records the evidence verdict. `unresolved` means the
  supplied evidence did not settle the claim; it is not a false verdict.
- `claims[].provenance_status` and `origin_summary` describe the source chain.
  A located origin needs an accepted direct path from the input source, with
  exact quotation checks and destination URLs observed in source text or links
  (including captured redirect aliases). Unrelated earlier material cannot substitute for that
  path. This still relies on model interpretation of source meaning; it is not
  independent authentication or proof of the absolute earliest publication.
  `claims[].located_sources` lists the accepted origins. If a model proposes a
  path without an observed destination link, `origin_link_gap` explains why the
  displayed result stays unresolved; the underlying model trace remains available
  for diagnosis.
  `origin_summary.upstream_candidates` preserves the actual upstream links chosen
  for retrieval, including papers that could not be collected. A candidate URL
  is not a verified original source.
- `analysis.report` preserves the imported research phases: source proposals,
  causal tree, timeline, perspectives, draft response and synthesis. These are
  explicitly model-generated, unverified analysis. Their original/grounded/
  verified flags cannot override the separate claim results.
  Bounded findings, questions and candidate version IDs can guide the formal
  loop as untrusted advice; they cannot supply quotations or resolve evidence
  gaps. In `claim` mode the report records intentionally omitted presentation
  phases under `phase_status` and `limits.research_scope`.
- `errors`, claim errors and phase records preserve partial failures. Execution
  records retain requests, model calls, available usage and admitted source text.
  Unknown token usage stays unknown. Reports include prompts and source content;
  inspect them before sharing.

Exit status is 0 when every item completes, including legitimate unresolved
verdicts; 1 for partial or failed items; 2 for invalid input/configuration. A
completed run does not mean all claims were proved true or all origins located.

## Collection limits and imported code

Live collection uses public Bing RSS search and direct HTTP(S) requests. It sends
the search query to Bing and fetches selected pages without browser cookies or
login sessions. The built-in collector rejects local/private network addresses,
checks redirects, and bounds request size and time. It reads HTML or plain text;
it does not render JavaScript, bypass paywalls, parse PDFs, or examine images and
figures. Some original records therefore remain unavailable. A later follow-up
may require a manually supplied text snapshot.
The default page limits are 1 MB of response bytes and 50,000 extracted text
characters. Oversized sources are reported as failures, rather than silently
used as complete records.
HTML table cells and empty cells are kept in explicit separated rows. This
preserves ordinary numeric values; it does not reconstruct merged-cell or
nested-table geometry.

Search and origin discovery inspect a finite pool, not the whole web. Research
now receives each eligible supplied text version in full, including versions
with the same URL, with exact offsets, text length, historical availability
time and its basis. A later retrieval date does not erase evidence of an earlier
archive. The total research source-text capacity is 240,000 characters; exceeding
it fails the research stage before inference instead of silently dropping text.
This source-text limit does not guarantee that the complete prompt fits every
model's context. Transport failures remain recorded. The separate claim trace
uses the retained eligible material. Full coverage refers to the supplied text,
not unseen images, missing attachments or the completeness of an original paper.
A fetched page can
still be incomplete, misleading or wrong, and matching an exact quote does not
establish that it entails the claim.

The complete uploaded project is preserved unchanged under
[`integrations/news-tracing-master`](../integrations/news-tracing-master), with
an external [file/hash manifest](../integrations/NEWS_TRACING_IMPORT.json).
It is reference material, not the active entry point. Its original API client,
Rich UI and requirements are not required to run this integration.

The active implementation is [`newsverify/news_tracing`](../newsverify/news_tracing)
plus the [harness client](../newsverify/news_client.py),
[collector](../newsverify/news_sources.py) and
[orchestrator](../newsverify/news_tracing_runner.py). It adapts the uploaded phases
to the existing transports, bounded execution and per-claim provenance engine.
The uploaded archive contains no license file; preservation does not assert a
third-party license.
