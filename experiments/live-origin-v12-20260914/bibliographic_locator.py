"""Source-bound modern bibliography discovery; never historical evidence.

Only Crossref's bibliographic field projection is requested. Raw responses and
exact requests stay in receipt_dir; model-facing candidates contain identity
locators only. Resolve does not fetch papers or modify a source's hyperlinks.
The caller supplies trusted, already admitted archive documents to verify().
"""
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
from urllib.parse import unquote, urlencode, urlsplit
from uuid import uuid4


FIELDS = ("DOI", "title", "author", "container-title", "published", "resource", "URL", "created")
LOCATOR_NOTICE = "Modern bibliographic locator only; not historical evidence or an observed hyperlink."
_CONTEXT = re.compile(r"\b(study|studies|published|paper|research|report|article|journal)\b", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"?#]+", re.I)
_MONTHS = {name: n for n, names in enumerate(((), ("jan", "january"), ("feb", "february"),
    ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
    ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
    ("nov", "november"), ("dec", "december"))) for name in names}


class BibliographyError(ValueError):
    pass


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def _contains(text, phrase):
    return " " + _norm(phrase) + " " in " " + _norm(text) + " "


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (TypeError, AttributeError, ValueError):
        raise BibliographyError("invalid_aware_timestamp") from None


def _url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\\]", value):
        raise BibliographyError("invalid_locator_url")
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.fragment:
        raise BibliographyError("invalid_locator_url")
    try:
        port = p.port
    except ValueError:
        raise BibliographyError("invalid_locator_url") from None
    if port not in (None, 80, 443):
        raise BibliographyError("invalid_locator_url")
    try:
        address = ipaddress.ip_address(p.hostname)
    except ValueError:
        if "." not in p.hostname or p.hostname.endswith((".local", ".localhost", ".internal")):
            raise BibliographyError("invalid_locator_url") from None
    else:
        if not address.is_global:
            raise BibliographyError("invalid_locator_url")
    return value


def _dates(text):
    """Explicit full dates only: ISO, month day year, or day month year."""
    found = set()
    for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            found.add(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    patterns = ((r"\b([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b", False),
                (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?[,]?\s+(\d{4})\b", True))
    for pattern, reverse in patterns:
        for a, b, y in re.findall(pattern, text, re.I):
            month, day = (b, a) if reverse else (a, b)
            if month.casefold() in _MONTHS:
                try:
                    found.add(date(int(y), _MONTHS[month.casefold()], int(day)))
                except ValueError:
                    pass
    return found


def _month_day_present(text, day):
    names = [re.escape(k) for k, v in _MONTHS.items() if v == day.month]
    month = "(?:" + "|".join(names) + ")\\.?"
    return bool(re.search(r"\b" + month + r"\s+0?" + str(day.day) + r"\b|\b0?" +
                          str(day.day) + r"\s+" + month + r"\b", text, re.I))


def _reference_identifiers(quote):
    """Exact identifiers in quoted source text, never newly guessed URLs."""
    dois, urls = set(), set()
    for raw in re.findall(r'https?://[^\s<>\[\]"\']+', quote):
        try:
            parsed = urlsplit(_url(raw))
        except BibliographyError:
            continue
        if parsed.hostname.casefold() in {"doi.org", "dx.doi.org", "www.doi.org"}:
            doi = unquote(parsed.path).lstrip("/")
            if _DOI.fullmatch(doi):
                dois.add(doi.casefold())
        elif parsed.path.strip("/"):
            urls.add(raw)
    for doi in re.findall(r'(?<!\w)(10\.\d{4,9}/[^\s<>\[\]"\']+)', quote, re.I):
        if _DOI.fullmatch(doi):
            dois.add(doi.casefold())
    return dois, urls


def _adjacent_quotes(content, first, second):
    # Repeated snippets could belong to separate bibliography entries. Require
    # unique literal anchors before using source adjacency instead of an ID.
    if content.count(first) != 1 or content.count(second) != 1:
        return False
    a, b = sorted(((content.index(q), content.index(q) + len(q)) for q in (first, second)))
    return a[1] <= b[0] and b[0] - a[1] <= 8 and not content[a[1]:b[0]].strip()


def _formal_citation_joins(content, clues):
    """Join a formal reference block to its explicitly dated citation notice.

    Ordinary bibliography blocks need no prose word such as 'study'. They must
    contain author, journal, and at least two supplied topic clues. A separate
    dated notice must share one exact identifier, or be immediately adjacent.
    Conflicting/multiple DOI identities never use the adjacency fallback.
    """
    joins = []
    blocks = [q for q in clues.quotes if _contains(q, clues.author) and _contains(q, clues.journal)
              and sum(_contains(q, k) for k in clues.keywords) >= 2]
    notices = [q for q in clues.quotes if _CONTEXT.search(q) and _dates(q)]
    for block in blocks:
        for notice in notices:
            if block == notice:
                continue
            block_dois, block_urls = _reference_identifiers(block)
            notice_dois, notice_urls = _reference_identifiers(notice)
            common = set()
            if len(block_dois) > 1 or len(notice_dois) > 1:
                continue
            if block_dois or notice_dois:
                if len(block_dois) == len(notice_dois) == 1 and block_dois == notice_dois:
                    common = {"doi:" + next(iter(block_dois))}
                elif block_dois and notice_dois:
                    continue
            elif block_urls and notice_urls:
                common = block_urls & notice_urls
                if not common:
                    continue
            adjacent = _adjacent_quotes(content, block, notice)
            if not common and not adjacent:
                continue
            joins.append({"block_quote": block, "notice_quote": notice,
                          "join_basis": "exact_shared_identifier" if common else "adjacent_exact_source_spans",
                          "identifiers": sorted(common),
                          "notice_dates": sorted(d.isoformat() for d in _dates(notice))})
    return joins


def _narrative_citation_context(quote, clues):
    # Journal names and locator URLs can themselves contain 'Journal', 'paper',
    # or 'research'. Those tokens do not establish narrative citation language.
    prose = re.sub(r'https?://[^\s<>\[\]"\']+', "", quote)
    prose = re.sub(r'(?<!\w)10\.\d{4,9}/[^\s<>\[\]"\']+', "", prose, flags=re.I)
    for identity in (clues.author, clues.journal):
        anchor = _exact_anchor(prose, identity)
        if anchor:
            prose = prose.replace(anchor, "")
    return bool(_CONTEXT.search(prose))


@dataclass(frozen=True)
class CitationClues:
    quotes: tuple[str, ...]
    author: str
    journal: str
    publication_date: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class LocatorCandidate:
    candidate_id: str
    title: str
    doi: str
    authors: tuple[str, ...]
    journal: str
    publication_date: str
    publisher_url: str
    notice: str = LOCATOR_NOTICE

    def to_packet(self):
        return asdict(self)


@dataclass(frozen=True)
class LookupResult:
    candidates: tuple[LocatorCandidate, ...]
    receipt_id: str
    status: str


@dataclass(frozen=True)
class CitationProof:
    source_url: str
    target_url: str
    source_text_sha256: str
    target_text_sha256: str
    source_quotes: tuple[str, ...]
    target_quotes: tuple[str, ...]
    identity: dict
    cutoff: str
    valid: bool
    checks: dict[str, bool]
    receipt_id: str
    edge_kind: str = "resolved_citation"
    notice: str = "Validated bibliography identity binding; not an observed hyperlink or a factual verdict."


def validate_clues(source, clues, cutoff):
    """Fail before requests if any query clue is ungrounded in this source."""
    if not isinstance(clues, CitationClues):
        raise BibliographyError("invalid_clues")
    content = getattr(source, "content", None)
    if not isinstance(content, str) or not content:
        raise BibliographyError("missing_source_text")
    _url(source.url)
    boundary = _instant(cutoff)
    if _instant(getattr(source, "available_at", None)) > boundary:
        raise BibliographyError("source_after_cutoff")
    if not isinstance(clues.quotes, (tuple, list)) or not 1 <= len(clues.quotes) <= 6:
        raise BibliographyError("invalid_source_quotes")
    if any(not isinstance(q, str) or not q.strip() or len(q) > 3000 or q not in content for q in clues.quotes):
        raise BibliographyError("quote_not_exact_source_span")
    if sum(map(len, clues.quotes)) > 6000:
        raise BibliographyError("source_quote_budget")
    for value in (clues.author, clues.journal):
        if not isinstance(value, str) or not 3 <= len(value) <= 160 or not _norm(value):
            raise BibliographyError("invalid_bibliographic_clue")
    if len(_norm(clues.author).split()) < 2:
        raise BibliographyError("author_needs_given_and_family_name")
    if not isinstance(clues.keywords, (tuple, list)) or not 2 <= len(clues.keywords) <= 6:
        raise BibliographyError("invalid_subject_keywords")
    if any(not isinstance(k, str) or not 3 <= len(k) <= 64 or not _norm(k) for k in clues.keywords):
        raise BibliographyError("invalid_subject_keywords")
    if len({_norm(k) for k in clues.keywords}) != len(clues.keywords):
        raise BibliographyError("duplicate_subject_keywords")
    joined = "\n".join(clues.quotes)
    if any(not _contains(joined, k) for k in clues.keywords):
        raise BibliographyError("keyword_not_in_source_quotes")
    narrative_quotes = [q for q in clues.quotes if _contains(q, clues.author) and _contains(q, clues.journal)
                        and _narrative_citation_context(q, clues)]
    formal_joins = _formal_citation_joins(content, clues)
    if not narrative_quotes and not formal_joins:
        raise BibliographyError("missing_author_journal_citation_context")
    topic_quotes = [q for q in clues.quotes if any(_contains(q, k) for k in clues.keywords)]
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", clues.publication_date):
            raise ValueError
        day = date.fromisoformat(clues.publication_date)
    except (TypeError, ValueError):
        raise BibliographyError("invalid_publication_date") from None
    if day > boundary.date():
        raise BibliographyError("publication_after_cutoff")
    formal_joins = [j for j in formal_joins if day.isoformat() in j["notice_dates"]]
    if not narrative_quotes and not formal_joins:
        raise BibliographyError("formal_citation_publication_date_not_bound")
    formal_dois = sorted({identifier.removeprefix("doi:") for join in formal_joins
                         for identifier in join["identifiers"] if identifier.startswith("doi:")})
    if len(formal_dois) > 1:
        raise BibliographyError("ambiguous_formal_citation_identifier")
    citation_quotes = list(dict.fromkeys(narrative_quotes + [q for j in formal_joins
                                      for q in (j["block_quote"], j["notice_quote"])]))
    quoted_date = day in _dates(joined)
    metadata_date = False
    if getattr(source, "published_at", None):
        try:
            metadata_date = _instant(source.published_at).date() == day
        except BibliographyError:
            # A date-only field is usable as a date clue, never as availability.
            metadata_date = source.published_at == day.isoformat()
    if not quoted_date and not (metadata_date and any(_month_day_present(q, day) for q in citation_quotes)):
        raise BibliographyError("publication_date_not_source_bound")
    # Explicit citation dates must agree. Otherwise a separately quoted full
    # date is a bounded discovery clue, not a claim of publication-time proof.
    named_date = any(re.search(r"\b(?:" + "|".join(_MONTHS) + r")\.?\s+\d{1,2}\b|\b\d{1,2}\s+(?:" +
                              "|".join(_MONTHS) + r")\b", q, re.I) or _dates(q) for q in citation_quotes)
    if named_date and not any(_month_day_present(q, day) or day in _dates(q) for q in citation_quotes):
        raise BibliographyError("citation_publication_day_not_bound")
    return {"source_url": source.url, "source_text_sha256": _hash(content),
            "clues_sha256": _hash(json.dumps(asdict(clues), sort_keys=True)),
            "source_quotes": list(clues.quotes), "citation_quotes": citation_quotes, "topic_quotes": topic_quotes,
            "citation_context_joins": formal_joins,
            "formal_citation_dois": formal_dois,
            "anchors": [{"start": content.index(q), "end": content.index(q) + len(q), "text": q} for q in clues.quotes],
            "quoted_full_date": quoted_date, "metadata_date": metadata_date}


def _archive_bound(document, cutoff):
    """Recheck adapter-provided archive identity; transport validates Memento."""
    try:
        capture = _instant(document.capture_at)
        if capture != _instant(document.available_at) or capture > _instant(cutoff):
            return False
        p = urlsplit(document.archive_url)
        m = re.fullmatch(r"/web/(\d{14})(?:id_)?/(https?://.+)", p.path + ("?" + p.query if p.query else ""))
        return (p.scheme == "https" and p.netloc == "web.archive.org" and m is not None
                and datetime.strptime(m[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) == capture
                and _url(m[2]).rstrip("/") == _url(document.url).rstrip("/"))
    except (AttributeError, BibliographyError, ValueError):
        return False


def _name_match(clue, given, family):
    parts = _norm(clue).split()
    surnames, given_parts = _norm(family).split(), _norm(given).split()
    return (bool(surnames) and len(parts) > len(surnames) and parts[-len(surnames):] == surnames
            and bool(given_parts) and (parts[0] == given_parts[0] or
                len(parts[0]) == 1 and parts[0] == given_parts[0][0] or
                len(given_parts[0]) == 1 and given_parts[0] == parts[0][0]))


def _exact_anchor(text, phrase):
    """Return literal matching text, preserving source bytes for quote checks."""
    words = re.findall(r"\w+", phrase)
    if not words:
        return None
    m = re.search(r"(?<!\w)" + r"[\W_]+".join(map(re.escape, words)) + r"(?!\w)", text, re.I)
    return m.group(0) if m else None


def _doi_anchor(text, doi):
    # A DOI prefix is not the article DOI: .supplement and /appendix matter.
    m = re.search(r"(?<![\w/])" + re.escape(doi) + r"(?![\w/.:;-])", text, re.I)
    return m.group(0) if m else None


class BibliographicLocator:
    def __init__(self, *, cutoff, receipt_dir, max_lookups=2, timeout=30, max_candidates=5,
                 max_bytes=300_000, request=None):
        _instant(cutoff)
        for name, value, upper in (("max_lookups", max_lookups, 10), ("max_candidates", max_candidates, 5),
                                   ("max_bytes", max_bytes, 1_000_000)):
            if type(value) is not int or not 1 <= value <= upper:
                raise BibliographyError("invalid_" + name)
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise BibliographyError("invalid_timeout")
        self.cutoff, self.receipt_dir = cutoff, Path(receipt_dir)
        self.max_lookups, self.timeout, self.max_candidates, self.max_bytes = max_lookups, timeout, max_candidates, max_bytes
        self._request = request or self._curl
        self.lookup_attempts = 0
        self._issued = {}

    def _curl(self, url):
        if urlsplit(url).scheme != "https" or urlsplit(url).netloc != "api.crossref.org":
            raise BibliographyError("non_crossref_network_request")
        with tempfile.TemporaryDirectory(prefix="factcircuit-bibliography-") as temp:
            root = Path(temp)
            run = subprocess.run(["/usr/bin/curl", "--silent", "--show-error", "--compressed", "--proto", "=https",
                "--connect-timeout", "10", "--max-time", str(self.timeout), "--max-filesize", str(self.max_bytes),
                "--dump-header", str(root / "headers"), "--output", str(root / "body"), url],
                capture_output=True, timeout=self.timeout + 1)
            if run.returncode:
                raise BibliographyError("crossref_timeout" if run.returncode == 28 else "crossref_http_failed")
            headers = (root / "headers").read_text(errors="replace")
            statuses = re.findall(r"(?m)^HTTP/[^\s]+\s+(\d{3})", headers)
            if not statuses or statuses[-1] != "200":
                raise BibliographyError("crossref_non_200")
            return (root / "body").read_bytes()

    def resolve(self, source, clues):
        binding = validate_clues(source, clues, self.cutoff)
        if self.lookup_attempts >= self.max_lookups:
            raise BibliographyError("bibliography_lookup_budget")
        day = date.fromisoformat(clues.publication_date)
        first = day.replace(day=1)
        last = min(day.replace(day=monthrange(day.year, day.month)[1]), _instant(self.cutoff).date())
        params = {"query.author": clues.author, "query.container-title": clues.journal,
                  "query.bibliographic": " ".join(clues.keywords),
                  "filter": f"from-pub-date:{first},until-pub-date:{last},until-created-date:{_instant(self.cutoff).date()},type:journal-article",
                  "rows": str(self.max_candidates), "select": ",".join(FIELDS)}
        url = "https://api.crossref.org/works?" + urlencode(params)
        self.lookup_attempts += 1
        receipt_id = uuid4().hex
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt = {"receipt_id": receipt_id, "url": url, "parameters": params, "binding": binding,
                   "cutoff": self.cutoff, "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": "started"}
        receipt_path = self.receipt_dir / (receipt_id + ".json")
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        try:
            raw = self._request(url)
            if not isinstance(raw, bytes) or len(raw) > self.max_bytes:
                raise BibliographyError("crossref_response_size")
            (self.receipt_dir / (receipt_id + ".response.json")).write_bytes(raw)
            receipt["response_sha256"] = hashlib.sha256(raw).hexdigest()
            data = json.loads(raw)
            message = data.get("message", {})
            items = message.get("items")
            total = message.get("total-results")
            if data.get("status") != "ok" or not isinstance(items, list) or len(items) > self.max_candidates:
                raise BibliographyError("invalid_crossref_response")
            if type(total) is not int or total < len(items):
                raise BibliographyError("invalid_crossref_result_count")
            candidates = []
            for item in items:
                if not isinstance(item, dict) or set(item) - set(FIELDS):
                    raise BibliographyError("unexpected_crossref_fields")
                candidate = self._candidate(item, clues, first, last, receipt_id, len(candidates),
                                            binding["formal_citation_dois"])
                if candidate is not None and candidate.doi not in {c.doi for c in candidates}:
                    candidates.append(candidate)
            status = "no_match" if not candidates else ("bounded_candidates" if total > len(items) else "candidates")
            receipt.update(status=status, candidates=[asdict(c) for c in candidates], total_results=total)
            for candidate in candidates:
                self._issued[candidate.candidate_id] = (candidate, binding, receipt_id)
            return LookupResult(tuple(candidates), receipt_id, status)
        except Exception as exc:
            receipt.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
            raise
        finally:
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    def _candidate(self, item, clues, first, last, receipt_id, number, formal_citation_dois=()):
        try:
            title = item["title"][0]
            doi = item["DOI"]
            journal = next(j for j in item["container-title"] if _norm(j) == _norm(clues.journal))
            authors = item["author"]
            if not isinstance(title, str) or not 1 <= len(title) <= 1000 or not isinstance(doi, str) or not _DOI.fullmatch(doi):
                return None
            if formal_citation_dois and doi.casefold() not in formal_citation_dois:
                return None
            if not isinstance(authors, list) or not 1 <= len(authors) <= 500:
                return None
            if not any(_name_match(clues.author, a.get("given", ""), a.get("family", "")) for a in authors):
                return None
            # This is only a discovery shortlist. News vocabulary may paraphrase
            # a paper title; full archived identity/topic gates still run later.
            if sum(_contains(title, k) for k in clues.keywords) < 1:
                return None
            parts = item["published"]["date-parts"][0]
            if len(parts) != 3:
                return None
            publication = date(*parts)
            if not first <= publication <= last:
                return None
            if _instant(item["created"]["date-time"]) > _instant(self.cutoff):
                return None
            publisher = _url(item["resource"]["primary"]["URL"])
            names = tuple(" ".join(str(a.get(k, "")).strip() for k in ("given", "family")).strip()
                          or str(a.get("name", "")) for a in authors)
            if any(not n or len(n) > 300 for n in names):
                return None
            return LocatorCandidate(receipt_id + ":" + str(number), title, doi.lower(), names, journal,
                                    publication.isoformat(), publisher)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, StopIteration):
            return None

    def verify(self, source, clues, candidate, archived_doc):
        issued = self._issued.get(getattr(candidate, "candidate_id", None))
        if issued is None or issued[0] != candidate:
            raise BibliographyError("candidate_not_issued_by_locator")
        binding = validate_clues(source, clues, self.cutoff)
        original = issued[1]
        if any(binding[k] != original[k] for k in ("source_url", "source_text_sha256", "clues_sha256")):
            raise BibliographyError("candidate_source_binding_changed")
        text = getattr(archived_doc, "content", "")
        if not isinstance(text, str):
            raise BibliographyError("invalid_target_text")
        target_url = getattr(archived_doc, "url", "")
        anchors = {"doi": _doi_anchor(text, candidate.doi), "title": _exact_anchor(text, candidate.title),
                   "author": _exact_anchor(text, clues.author), "journal": _exact_anchor(text, candidate.journal)}
        # A DOI mention in references alone is insufficient: require the page
        # title to identify this work, and all identity anchors in its front.
        front = text[:24000]
        checks = {"source_citation_context_bound": bool(binding["citation_quotes"]),
                  "source_version_unchanged": True,
                  "formal_citation_identifier_matches": not binding["formal_citation_dois"] or
                      candidate.doi in binding["formal_citation_dois"],
                  "archive_cutoff_and_identity": _archive_bound(archived_doc, self.cutoff),
                  "selected_publisher_url": target_url.rstrip("/") == candidate.publisher_url.rstrip("/"),
                  "page_title_matches": _contains(getattr(archived_doc, "title", ""), candidate.title),
                  "identity_in_front_matter": bool(_doi_anchor(front, candidate.doi)) and
                      all(_exact_anchor(front, phrase) for phrase in (candidate.title, clues.author, candidate.journal)),
                  "source_topics_in_primary": sum(_contains(text, k) for k in clues.keywords) >= 2,
                  "primary_body_present": bool(re.search(r"(?im)^\s*(results|methods|materials and methods|methodology|discussion)\s*$", text))}
        checks = {key: bool(value) for key, value in checks.items()}
        identity = {key: getattr(candidate, key) for key in ("doi", "title", "authors", "journal")}
        proof = CitationProof(source.url, target_url, _hash(source.content), _hash(text), tuple(clues.quotes),
                              tuple(dict.fromkeys(a for a in anchors.values() if a)), identity,
                              self.cutoff, all(checks.values()), checks, issued[2])
        (self.receipt_dir / (candidate.candidate_id.replace(":", "-") + ".proof.json")).write_text(json.dumps(asdict(proof), indent=2) + "\n")
        return proof

    verify_candidate = verify
