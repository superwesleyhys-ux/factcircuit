"""Exact selections in stored source text, without semantic rewriting.

Offsets are zero-based Python Unicode codepoint offsets with an exclusive end.
They refer to the supplied ``str``, not HTML bytes, UTF-16 units, normalized text,
or rendered grapheme clusters. Every returned string is a slice of that source.
Finding an occurrence establishes only textual presence, never factual truth.
"""

from __future__ import annotations

import hashlib


MAX_ITEMS = 100


def text_sha256(text: str) -> str:
    """Hash exact UTF-8 source text, including its whitespace and normalization."""
    if not isinstance(text, str):
        raise ValueError("source content must be text")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("source text must contain valid Unicode scalar values") from exc
    return hashlib.sha256(encoded).hexdigest()


def _indices(content: str, start: int, end: int) -> None:
    if type(start) is not int or type(end) is not int:
        raise ValueError("span offsets must be integer Unicode codepoint offsets")
    if not 0 <= start < end <= len(content):
        raise ValueError("span offsets must select a nonempty substring inside source text")


def validate_span(content: str, start: int, end: int, quote: str) -> None:
    """Raise ``ValueError`` unless the exact nonempty quote occupies these offsets."""
    text_sha256(content)
    _indices(content, start, end)
    if not isinstance(quote, str) or content[start:end] != quote:
        raise ValueError("span quote does not exactly match source text at its offsets")


def _context_size(context_chars: int) -> None:
    if type(context_chars) is not int or context_chars < 0:
        raise ValueError("context_chars must be a nonnegative integer")


def _positions(content: str, phrase: str) -> list[tuple[int, int]]:
    if not isinstance(phrase, str) or not phrase:
        raise ValueError("phrase must be nonempty exact text")
    positions: list[tuple[int, int]] = []
    position = content.find(phrase)
    while position != -1:
        positions.append((position, position + len(phrase)))
        if len(positions) > MAX_ITEMS:
            raise ValueError(f"selection exceeds the {MAX_ITEMS} item limit; use explicit offsets")
        # Move one codepoint, retaining overlaps such as both 'ana' in 'banana'.
        position = content.find(phrase, position + 1)
    return positions


def _slice(content: str, start: int, end: int) -> dict:
    return {"start": start, "end": end, "text": content[start:end]}


def _item(content: str, start: int, end: int, digest: str, context_chars: int,
          *, source_id: str | None = None) -> dict:
    # Keep the containing line(s) plus neighboring codepoints. This can retain
    # a table row's units but does not establish that remote headers, negation,
    # attribution, or other relevant context is absent. The caller keeps the
    # full original document available to verification.
    line_start = content.rfind("\n", 0, start) + 1
    # If the selected substring ends in a newline, that newline already closes
    # its final containing line; do not pull in a whole unrelated next line.
    next_newline = content.find("\n", end - 1)
    line_end = len(content) if next_newline == -1 else next_newline + 1
    left = min(line_start, max(0, start - context_chars))
    right = max(line_end, min(len(content), end + context_chars))
    identity = f"{digest}:{start}:{end}"
    prefix = "item"
    if source_id is not None:
        identity = f"{len(source_id)}:{source_id}:{identity}"
        prefix = "occurrence"
    item = {
        "id": f"{prefix}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
        "start": start,
        "end": end,
        "text": content[start:end],
        "before": _slice(content, left, start),
        "after": _slice(content, end, right),
        "context": _slice(content, left, right),
        "source_sha256": digest,
        "clipped_left": left > 0,
        "clipped_right": right < len(content),
    }
    if source_id is not None:
        item["source_id"] = source_id
    return item


def select_items(text: str, selectors: list, *, context_chars: int = 160) -> list[dict]:
    """Select exact phrases (all occurrences) or ``{start, end}`` span objects.

    Distinct overlapping spans are allowed. Duplicate spans, missing phrases,
    malformed selectors, and selections expanding beyond 100 items are errors;
    no selection is silently dropped or truncated. Results are ordered by start
    then end, independently of selector ordering. Whitespace-only phrases are
    valid exact selections and are never stripped.

    ``before`` and ``after`` join the selected text to reproduce ``context``.
    Context includes the containing line(s) and up to ``context_chars`` beyond
    each selection boundary, whichever reaches farther. The clipping flags mean
    text exists outside that window; they do not assess semantic completeness.
    """
    digest = text_sha256(text)
    _context_size(context_chars)
    if not isinstance(selectors, list) or not selectors:
        raise ValueError("selectors must be a nonempty list")
    if len(selectors) > MAX_ITEMS:
        raise ValueError(f"selection exceeds the {MAX_ITEMS} item limit")
    positions: set[tuple[int, int]] = set()
    for selector in selectors:
        if isinstance(selector, str):
            selected = _positions(text, selector)
            if not selected:
                raise ValueError("selected phrase does not occur exactly in source text")
        elif isinstance(selector, dict) and set(selector) == {"start", "end"}:
            start, end = selector["start"], selector["end"]
            _indices(text, start, end)
            selected = [(start, end)]
        else:
            raise ValueError("each selector must be exact text or an object with only start and end")
        for position in selected:
            if position in positions:
                raise ValueError("duplicate span selected; each exact source item must appear once")
            positions.add(position)
            if len(positions) > MAX_ITEMS:
                raise ValueError(f"selection exceeds the {MAX_ITEMS} item limit; use explicit offsets")
    return [_item(text, start, end, digest, context_chars)
            for start, end in sorted(positions)]


def phrase_occurrences(source_id: str, content: str, phrase: str,
                       context_chars: int = 160) -> list[dict]:
    """Index every exact occurrence, including overlaps; missing text returns [].

    Occurrence IDs bind the source identifier, entire content hash, and offsets.
    More than 100 matches fails explicitly instead of returning a partial index.
    An occurrence in a repeated or copied source is not independent confirmation.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be nonempty text")
    # Validate Unicode for source IDs too, without transforming the identifier.
    text_sha256(source_id)
    digest = text_sha256(content)
    _context_size(context_chars)
    return [_item(content, start, end, digest, context_chars, source_id=source_id)
            for start, end in _positions(content, phrase)]
