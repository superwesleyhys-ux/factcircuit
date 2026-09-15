"""Bounded historical full-text reader, with no present-day fallback.

V12 retains the uniform HTML/body and extraction limits, preserves the complete
extracted text, and tries a bounded set of older captures after failures. Only
web.archive.org receives network requests. Raw bytes and provenance stay local.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlencode, urlsplit, urljoin

from newsverify.news_sources import NewsSourceCollector, SourceDocument, canonical_url, _SourceError


RAW_BYTES_LIMIT = 5_000_000
EXTRACTED_CHARS_LIMIT = 1_000_000
INDEX_BYTES_LIMIT = 1_000_000
CACHE_SCHEMA = 2
_REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class ArchivedDocument(SourceDocument):
    capture_at: str = ""
    archive_url: str = ""

    @property
    def available_at(self):
        return self.capture_at

    @property
    def availability_basis(self):
        return "Archive Memento-Datetime validated at retrieval: " + self.archive_url


def _parse_replay(url, cutoff):
    p = urlsplit(url)
    match = re.fullmatch(r"/web/(\d{14})(?:id_)?/(https?://.+)", p.path + ("?" + p.query if p.query else ""))
    if p.scheme != "https" or p.netloc != "web.archive.org" or p.fragment or not match:
        raise _SourceError("non_archive_redirect")
    try:
        captured = datetime.strptime(match[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        raise _SourceError("invalid_archive_timestamp") from None
    if captured > cutoff:
        raise _SourceError("post_cutoff_archive_redirect")
    return captured, canonical_url(match[2])


def validate_replay(url, cutoff):
    return _parse_replay(url, cutoff)[0]


def _memento(headers):
    try:
        value = parsedate_to_datetime(headers.get("memento-datetime", ""))
        if value.tzinfo is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise _SourceError("unverified_archive_timestamp") from None


def _original(headers):
    for match in re.finditer(r'<([^>]+)>\s*;\s*rel=(?:"([^"]+)"|([^;,\s]+))', headers.get("link", ""), re.I):
        if "original" in (match[2] or match[3]).lower().split():
            return canonical_url(match[1])
    raise _SourceError("missing_original_identity")


class ArchiveCollector(NewsSourceCollector):
    def __init__(self, *, cutoff, cache_dir, max_capture_attempts=5, **kwargs):
        if kwargs.get("max_searches", 0) != 0:
            raise ValueError("This link-only reader has no historical full-text search service")
        if type(max_capture_attempts) is not int or not 1 <= max_capture_attempts <= 10:
            raise ValueError("max_capture_attempts must be between 1 and 10")
        self.cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
        if self.cutoff.tzinfo is None:
            raise ValueError("cutoff must have an explicit timezone")
        self.cutoff = self.cutoff.astimezone(timezone.utc)
        self.historical_cutoff = cutoff
        self.max_capture_attempts = max_capture_attempts
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.archive_records = {}
        kwargs.setdefault("max_searches", 0)
        kwargs.setdefault("max_bytes", RAW_BYTES_LIMIT)
        kwargs.setdefault("max_chars", EXTRACTED_CHARS_LIMIT)
        super().__init__(fetch=self._archive_fetch, **kwargs)

    def _http(self, url, deadline, *, max_bytes=None, stage="replay"):
        if urlsplit(url).netloc != "web.archive.org" or urlsplit(url).scheme != "https":
            raise _SourceError("non_archive_network_request")
        limit = self.max_bytes if max_bytes is None else max_bytes
        record = {"operation": "archive_http", "stage": stage, "url": url,
                  "decoded_bytes": 0, "byte_limit": limit, "success": False}
        self.requests.append(record)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            record["error"] = "timeout"
            raise _SourceError("timeout")
        # curl's --max-filesize does not reliably bound decompressed output.
        # Read only limit+1 decoded bytes, then stop the process on overflow.
        with tempfile.TemporaryDirectory(prefix="factcircuit-v9-archive-") as directory:
            header_path = Path(directory) / "headers"
            command = ["/usr/bin/curl", "--silent", "--show-error", "--compressed",
                       "--proto", "=https", "--connect-timeout", "10", "--max-time", str(remaining),
                       "--max-filesize", str(limit), "--dump-header", str(header_path), "--output", "-", url]
            chunks, size, too_large = [], 0, False
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  stdin=subprocess.DEVNULL) as process:
                try:
                    while True:
                        chunk = process.stdout.read1(min(65536, limit + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > limit:
                            too_large = True
                            process.kill()
                            break
                    process.wait(timeout=max(0.1, deadline - time.monotonic() + 1))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    record.update(decoded_bytes=size, error="archive_http_timeout")
                    raise _SourceError("archive_http_timeout") from None
                finally:
                    record["decoded_bytes"] = size
                returncode = process.returncode
            raw = header_path.read_text(errors="replace") if header_path.exists() else ""
            blocks = [b for b in re.split(r"\r?\n\r?\n", raw) if b.startswith("HTTP/")]
            lines = blocks[-1].splitlines() if blocks else []
            try:
                status = int(lines[0].split()[1])
            except (IndexError, ValueError):
                status = None
            headers = {k.strip().lower(): v.strip() for line in lines[1:] if ":" in line
                       for k, v in [line.split(":", 1)]}
            record.update(status=status, curl_returncode=returncode,
                          body_complete=returncode == 0 and not too_large)
            for key in ("content-type", "content-length", "content-encoding", "memento-datetime", "location"):
                if key in headers:
                    record[key.replace("-", "_")] = headers[key]
            if too_large or returncode == 63:
                record["error"] = "response_too_large"
                raise _SourceError("response_too_large")
            if returncode:
                record["error"] = "archive_http_timeout" if returncode == 28 else "archive_http_failed"
                raise _SourceError(record["error"])
            if status is None:
                record["error"] = "archive_http_invalid_headers"
                raise _SourceError(record["error"])
            record["success"] = True
            return status, headers, b"".join(chunks)

    def _validate_record(self, url, meta, body):
        try:
            if meta.get("schema") != CACHE_SCHEMA or meta.get("requested_url") != url or meta.get("cutoff") != self.historical_cutoff:
                raise _SourceError("archive_cache_identity_mismatch")
            if hashlib.sha256(body).hexdigest() != meta["sha256"] or len(body) != meta["decoded_bytes"]:
                raise _SourceError("archive_cache_digest_mismatch")
            if len(body) > self.max_bytes:
                raise _SourceError("response_too_large")
            captured, replay_original = _parse_replay(meta["archive_url"], self.cutoff)
            if captured.isoformat() != meta["capture_at"] or _memento(meta["headers"]) != captured:
                raise _SourceError("archive_cache_timestamp_mismatch")
            if canonical_url(meta["final_url"]) != replay_original or _original(meta["headers"]) != replay_original:
                raise _SourceError("archive_cache_identity_mismatch")
            chain = meta["replay_chain"]
            if not chain or chain[-1] != meta["archive_url"]:
                raise _SourceError("archive_cache_identity_mismatch")
            for replay in chain:
                validate_replay(replay, self.cutoff)
        except _SourceError:
            raise
        except (KeyError, ValueError, TypeError, AttributeError):
            raise _SourceError("invalid_archive_cache") from None

    def _normalize_cache_record(self, meta, body):
        # V7 kept the final verified Memento and original identity, but not the
        # redirect chain. Revalidate those records without rewriting the old
        # cache or pretending its missing intermediate hops were recorded.
        if isinstance(meta, dict) and "schema" not in meta:
            meta = dict(meta)
            meta.update(schema=CACHE_SCHEMA, cutoff=self.historical_cutoff,
                        decoded_bytes=len(body), replay_chain=[meta.get("archive_url")],
                        replay_chain_scope="final_only_legacy_v7", source_cache_schema=1)
        return meta

    def _capture(self, url, chosen, deadline, capture_count):
        original = canonical_url(chosen["original"])
        replay = "https://web.archive.org/web/" + chosen["timestamp"] + "id_/" + original
        chain = []
        for _ in range(8):
            _, replay_original = _parse_replay(replay, self.cutoff)
            if replay in chain:
                raise _SourceError("archive_redirect_loop")
            chain.append(replay)
            status, headers, body = self._http(replay, deadline)
            if status in _REDIRECTS:
                if not headers.get("location"):
                    raise _SourceError("missing_archive_redirect")
                replay = urljoin(replay, headers["location"])
                continue
            if status != 200:
                raise _SourceError("archive_capture_unavailable")
            captured = _memento(headers)
            if captured != validate_replay(replay, self.cutoff):
                raise _SourceError("unverified_archive_timestamp")
            original = _original(headers)
            if original != replay_original:
                raise _SourceError("archive_original_identity_mismatch")
            # curl already decoded transfer compression; retain the source
            # response diagnostics in requests, not misleading body headers.
            headers = dict(headers)
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _SourceError("timeout")
            # Validate with the same parser used by the final public fetch.
            # A successful HTTP capture can still be empty, malformed or too
            # large to extract; keep those failures inside the fallback loop.
            validator = NewsSourceCollector(max_documents=1, max_searches=0,
                timeout=remaining, max_bytes=self.max_bytes, max_chars=self.max_chars,
                fetch=lambda ignored: (original, headers, body))
            if validator.fetch(original) is None:
                raise _SourceError(validator.errors[-1]["code"] if validator.errors else "invalid_archive_capture")
            meta = {"schema": CACHE_SCHEMA, "cutoff": self.historical_cutoff,
                    "requested_url": url, "final_url": original, "archive_url": replay,
                    "capture_at": captured.isoformat(), "headers": headers,
                    "replay_chain": chain, "selected_capture": chosen,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(body).hexdigest(), "decoded_bytes": len(body),
                    "index_capture_count": capture_count}
            self._validate_record(url, meta, body)
            return meta, body
        raise _SourceError("archive_redirect_limit")

    def _archive_fetch(self, url):
        key = hashlib.sha256((self.historical_cutoff + "\n" + url).encode()).hexdigest()
        meta_path, body_path = self.cache_dir / (key + ".json"), self.cache_dir / (key + ".body")
        if meta_path.exists() or body_path.exists():
            if not meta_path.exists() or not body_path.exists():
                raise _SourceError("archive_cache_incomplete")
            if body_path.stat().st_size > self.max_bytes:
                self.requests.append({"operation": "archive_cache", "requested_url": url,
                                      "decoded_bytes": body_path.stat().st_size, "error": "response_too_large"})
                raise _SourceError("response_too_large")
            try:
                meta, body = json.loads(meta_path.read_text()), body_path.read_bytes()
            except (ValueError, OSError):
                raise _SourceError("invalid_archive_cache") from None
            meta = self._normalize_cache_record(meta, body)
            self._validate_record(url, meta, body)
            self.requests.append({"operation": "archive_cache", "requested_url": url,
                                  "archive_url": meta["archive_url"], "sha256": meta["sha256"],
                                  "decoded_bytes": len(body),
                                  "source_cache_schema": meta.get("source_cache_schema", CACHE_SCHEMA)})
        else:
            deadline = time.monotonic() + self.timeout
            query = urlencode({"url": url.split("://", 1)[1], "output": "json",
                               "to": self.cutoff.strftime("%Y%m%d%H%M%S"), "limit": "1000"})
            status, _, body = self._http("https://web.archive.org/cdx/search/cdx?" + query,
                                         deadline, max_bytes=INDEX_BYTES_LIMIT, stage="index")
            if status != 200:
                raise _SourceError("archive_index_unavailable")
            try:
                rows = json.loads(body)
                if not isinstance(rows, list) or (rows and not isinstance(rows[0], list)):
                    raise ValueError
                if len(rows) >= 1001:
                    raise _SourceError("archive_index_limit")
                captures = [dict(zip(rows[0], row)) for row in rows[1:]] if rows else []
                captures = [r for r in captures if r.get("statuscode") in {"200", "301", "302", "303", "307", "308"}
                            and re.fullmatch(r"\d{14}", r.get("timestamp", ""))
                            and r["timestamp"] <= self.cutoff.strftime("%Y%m%d%H%M%S")]
            except _SourceError:
                raise
            except (ValueError, TypeError, KeyError):
                raise _SourceError("invalid_archive_index") from None
            if not captures:
                raise _SourceError("no_pre_cutoff_capture")
            captures.sort(key=lambda r: (r["statuscode"] == "200", r["timestamp"]), reverse=True)
            candidates = captures[:self.max_capture_attempts]
            for attempt, chosen in enumerate(candidates, 1):
                self.requests.append({"operation": "archive_candidate", "requested_url": url,
                                      "attempt": attempt, "timestamp": chosen["timestamp"],
                                      "original": chosen.get("original"), "indexed_status": chosen["statuscode"]})
                try:
                    meta, body = self._capture(url, chosen, deadline, len(captures))
                except _SourceError as error:
                    self.requests.append({"operation": "archive_candidate_error", "requested_url": url,
                                          "attempt": attempt, "timestamp": chosen["timestamp"], "code": str(error)})
                    continue
                except (ValueError, TypeError, KeyError):
                    self.requests.append({"operation": "archive_candidate_error", "requested_url": url,
                                          "attempt": attempt, "timestamp": chosen["timestamp"], "code": "invalid_archive_capture"})
                    continue
                # These are new V12 cache entries; V7 captures/results are never changed.
                with tempfile.TemporaryDirectory(prefix=".v12-cache-", dir=self.cache_dir) as directory:
                    staged_body, staged_meta = Path(directory) / "body", Path(directory) / "meta"
                    staged_body.write_bytes(body)
                    staged_meta.write_text(json.dumps(meta, indent=2) + "\n")
                    staged_body.replace(body_path)
                    staged_meta.replace(meta_path)
                break
            else:
                raise _SourceError("archive_candidates_exhausted")
        self.archive_records[url] = meta
        return meta["final_url"], meta["headers"], body

    def fetch(self, url):
        document = super().fetch(url)
        if document is None or isinstance(document, ArchivedDocument):
            return document
        meta = self.archive_records[canonical_url(url)]
        document = ArchivedDocument(**document.__dict__, capture_at=meta["capture_at"], archive_url=meta["archive_url"])
        self.documents[document.url] = document
        return document

    def search(self, query, limit=3):
        self.requests.append({"operation": "search_skipped", "reason": "historical_link_only_pilot"})
        return []
