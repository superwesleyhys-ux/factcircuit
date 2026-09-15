"""Public source capture for literal tracing; no prose normalization or summary."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import hashlib
import re
from urllib.parse import urljoin

from .news_sources import NewsSourceCollector, SourceDocument, canonical_url, _headers, _PAGE_TYPES


class LiteralHTML(HTMLParser):
    """Keep visible text-node contents in order, with explicit structural separators.

    Offsets refer to this extracted representation, NOT to HTML bytes or a browser
    rendering. Scripts/styles and head metadata are excluded. No claim of visual
    completeness is made (CSS, JS, images and PDFs require separate inspection).
    """
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url, self.parts, self.links, self.titles = url, [], [], []
        self.hidden, self.in_title, self.anchor = [], False, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "template", "svg", "head"}:
            self.hidden.append(tag)
        if tag == "title":
            self.in_title = True
        if self.hidden:
            return
        if tag in {"p", "div", "section", "article", "br", "tr", "h1", "h2", "h3", "li", "caption"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append("\t")
        if tag == "a" and attrs.get("href"):
            try:
                self.anchor = {"url": canonical_url(urljoin(self.url, attrs["href"])), "text": ""}
                self.links.append(self.anchor)
            except ValueError:
                self.anchor = None

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "a":
            self.anchor = None
        if tag in {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.titles.append(data)
        if self.hidden:
            return
        self.parts.append(data)
        if self.anchor is not None:
            self.anchor["text"] += data


@dataclass(frozen=True)
class LiteralDocument(SourceDocument):
    raw_body_sha256: str = ""
    raw_bytes: int = 0
    extraction: str = "literal text nodes; HTML entities decoded; block newlines and cell tabs inserted; no whitespace folding"
    access_status: str = "public_response_unverified_completeness"
    capture_receipt: dict = field(default_factory=dict)


def decode_public_document(url, headers, body, *, retrieved_at=None):
    """Decode obtained public bytes; reject recognizable access/challenge pages."""
    headers = _headers(headers)
    kind = headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in _PAGE_TYPES:
        raise ValueError("unsupported_content_type")
    encoding = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", headers.get("content-type", ""), re.I)
    raw = body.decode(encoding.group(1) if encoding else "utf-8", errors="strict")
    # Refuse known incomplete pages; this is a conservative heuristic, not a
    # paywall detector or proof of completeness for every possible website.
    if re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', raw, re.I):
        raise ValueError("access_restricted")
    parser = LiteralHTML(url)
    if kind in {"text/html", "application/xhtml+xml"}:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    else:
        text = raw
    title = "".join(parser.titles)
    if re.search(r"^(?:\s*)(?:just a moment|access denied|verify you are human|checking your browser)", title, re.I):
        raise ValueError("access_challenge")
    if re.search(r"subscribe to (?:continue|read)|sign in to (?:continue|read)|enable javascript and cookies to continue|verify you are human", text, re.I):
        raise ValueError("access_restricted_or_challenge")
    if not text.strip():
        raise ValueError("empty_document")
    return LiteralDocument(
        url=canonical_url(url), title=title or url, content=text,
        retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat(),
        links=parser.links, raw_body_sha256=hashlib.sha256(body).hexdigest(), raw_bytes=len(body),
        extraction=("plain text decoded without stripping or normalization" if kind == "text/plain" else LiteralDocument.extraction),
    )


class LiteralSourceCollector(NewsSourceCollector):
    """Same public-network guards and budgets, with literal text extraction.

    No cookies, credentials, subscription bypass, headless browser, or article
    reconstruction. Failed responses stay in the request/error ledger.
    """
    def fetch(self, url):
        try:
            url = canonical_url(url)
        except ValueError:
            self._error("fetch", "invalid_url")
            return None
        cached = self.documents.get(self._aliases.get(url, url))
        if cached:
            return cached
        if url in self._attempted:
            return None
        if len(self._attempted) >= self.max_documents:
            self._error("fetch", "document_limit", url)
            return None
        self._attempted.add(url)
        record = {"operation": "fetch", "url": url, "success": False}
        self.requests.append(record)
        try:
            final_url, raw_headers, body = self._fetch(url) if self._fetch else self._download(url, _PAGE_TYPES)
            final_url, headers = canonical_url(final_url), _headers(raw_headers)
            self._check_headers(headers, _PAGE_TYPES)
            if not isinstance(body, bytes) or len(body) > self.max_bytes:
                raise ValueError("invalid_or_oversized_body")
            doc = decode_public_document(final_url, headers, body)
            if len(doc.content) > self.max_chars:
                raise ValueError("extracted_text_too_large")
            self.documents.setdefault(final_url, doc)
            self._aliases[url] = final_url
            record.update(success=True, final_url=final_url, bytes=len(body), raw_body_sha256=doc.raw_body_sha256)
            return self.documents[final_url]
        except Exception as exc:
            # Never retain arbitrary transport exception text (could contain
            # credentials from a custom adapter).
            safe = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "fetch_failed"
            self._error("fetch", safe, url)
            return None
