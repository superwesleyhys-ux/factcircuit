# Trace original words and phrases individually

`trace-phrases` is a separate path that does not call the research-summary or synthesis stages. It selects literal occurrences from a fetched page, keeps their positions and surrounding words, and checks each occurrence independently. The complete stored input and fetched documents remain available on every model call. Earlier item judgments and model explanations never become later evidence.

Create an input JSON file:

```json
{
  "url": "https://www.axios.com/2022/10/11/nasa-dart-asteroid-deflection",
  "selectors": ["32 minutes", "73 seconds"],
  "limits": {
    "max_calls": 3,
    "max_documents": 4,
    "max_searches": 1,
    "max_chars": 200000,
    "context_chars": 160
  }
}
```

Run with your local Codex login (Astra is the default; model inference is remote):

```sh
factcircuit trace-phrases input.json --output private-data/phrase-run-01.json
```

The output must be a new file. It contains full third-party texts and raw model input/output; keep it local. For a different local model, pass `--model`. `--arm direct` uses ordinary verification guidance in the same runner. That is a controlled comparison of guidance, not a comparison to a bare model with no shared extraction or validation.

Each literal selector selects **all** exact occurrences, including overlaps, in source order. One Chinese character or one word is also valid. To select just one occurrence, use `{"start": 120, "end": 122}` with verified offsets from the stored text. Offsets are zero-based Unicode codepoints, end exclusive; they are not UTF-8 bytes, UTF-16 positions, or browser screen coordinates. Text and Unicode normalization are never silently changed after extraction. Missing phrases, duplicate selections, and more than 100 occurrences fail explicitly; they are not dropped. A substring match inside another word remains a literal match, not a linguistic or factual equivalence.

For each item, inspect:

- `item.text`, `start`, `end`, `source_sha256`, `before`, `after`, and `context`. Clipping flags show whether more text exists outside that window. Full documents retain distant qualifiers, headings, table units and notes.
- `documents`: complete captured text, URL, capture availability, raw-body hash and extraction description. HTML entities are decoded; structural newlines and table-cell tabs are inserted. Original text-node whitespace is preserved. Offsets describe this representation, not original HTML bytes or a visual rendering.
- `actions`, `source_requests`, and `source_errors`: what was actually opened/searched, blocked, or unavailable. Search snippets are not used as evidence. Opening a document follows an observed link; a bounded public search may discover further documents.
- `judgment`: the model's interpretation of that occurrence in context. A standalone word may have no truth value. Source attribution, empirical claims and lexical presence are distinct. `unresolved` is a valid outcome.
- `citations`: exact quoted occurrence positions computed by the program, with original neighboring text. This verifies textual integrity, not semantic correctness. `origin_chain` is a model-proposed chain constrained to read sources and observed links/verified retrieval aliases, not proof of originality.

The live collector uses only public HTTP(S) responses without subscriber credentials or a paywall bypass. Recognized login, subscription and bot-challenge pages are excluded. The detector is conservative and cannot certify completeness for every site. JavaScript-only content, images, PDFs, hidden CSS content and visual layout need separate inspection; the text parser is not a browser.

An optional `as_of` must contain a timezone. The default collector captures current pages, so their current capture time cannot establish pre-cutoff availability, even if the publication date is old. For historical use, Python callers can inject a collector with verified archive captures. The registered [DART diagnostic](../experiments/exact-phrase-v14-20260915/PROTOCOL.md) does that with unmodified raw archive bytes. Its two-document replay does not test unrestricted live search.

For comparison, freeze the same original bytes, selections, model, reasoning effort, actions, limits and schema before either arm runs. Use fresh per-item collectors, record actual usage, and retain invalid, failed and unresolved results. Never tune the policy against the same result until it wins. Same wording, same owner, copied reporting or an original issuer's self-report do not by themselves independently authenticate an event. Semantic reliability still needs evidence review and broader held-out evaluation.
