"""Offline V12 archive regression checks; optionally validate local real captures.

python experiments/live-origin-v12-20260914/check_archive.py \
  --diagnostic-dir /absolute/path/to/archive-rootcause
No model calls or live network requests are made by these checks.
"""
import argparse
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from archive_collector import (ArchiveCollector, ArchivedDocument, CACHE_SCHEMA,
                               EXTRACTED_CHARS_LIMIT, RAW_BYTES_LIMIT, validate_replay)
from newsverify.news_sources import _SourceError

CUTOFF = "2024-12-31T23:59:59Z"
URL = "https://example.org/paper"
OLD = "20231201120000"
NEW = "20241201120000"


def replay(stamp=NEW, original=URL):
    return f"https://web.archive.org/web/{stamp}id_/{original}"


def headers(stamp=NEW, original=URL):
    date = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return {"content-type": "text/html; charset=utf-8",
            "memento-datetime": format_datetime(date, usegmt=True),
            "link": f'<{original}>; rel="original"'}


def index(rows):
    return json.dumps([["timestamp", "original", "statuscode"], *rows]).encode()


class FakeCurl:
    body = b""
    reply_headers = b"HTTP/2 200\r\ncontent-type: text/html\r\ncontent-encoding: gzip\r\n\r\n"
    def __init__(self, command, **kwargs):
        Path(command[command.index("--dump-header") + 1]).write_bytes(self.reply_headers)
        self.stdout = io.BytesIO(self.body)
        self.returncode = 0
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.stdout.close()
    def wait(self, **kwargs):
        return self.returncode
    def kill(self):
        self.returncode = -9


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collector = self.make_collector()
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def make_collector(self, **kwargs):
        return ArchiveCollector(cutoff=CUTOFF, cache_dir=self.root, timeout=30, **kwargs)

    def stub(self, rows, responses, collector=None):
        target = collector or self.collector
        def http(url, deadline, **kwargs):
            self.calls.append(url)
            if "/cdx/search/cdx?" in url:
                return 200, {"content-type": "application/json"}, index(rows)
            if url not in responses:
                raise AssertionError("Unexpected network request: " + url)
            response = responses[url]
            if isinstance(response, Exception):
                raise response
            return response
        target._http = http

    def prime(self):
        self.stub([[NEW, URL, "200"]], {replay(): (200, headers(), b"<h1>Study</h1><p>Complete evidence.</p>")})
        self.assertIsNotNone(self.collector.fetch(URL))
        return next(self.root.glob("*.json")), next(self.root.glob("*.body"))

    def assert_cache_rejected(self, expected):
        other = self.make_collector()
        other._http = lambda *args, **kwargs: self.fail("Corrupt cache must not trigger a network fallback")
        self.assertIsNone(other.fetch(URL))
        self.assertEqual(other.errors[-1]["code"], expected)

    def test_uniform_defaults(self):
        self.assertEqual(self.collector.max_bytes, RAW_BYTES_LIMIT)
        self.assertEqual(self.collector.max_chars, EXTRACTED_CHARS_LIMIT)
        self.assertEqual(self.collector.max_searches, 0)

    def test_complete_text_above_old_limit(self):
        body = b"<h1>Study</h1><p>" + b"Original observations. " * 5000 + b"</p><h2>Corrections</h2><p>Unique final qualifier.</p>"
        self.stub([[NEW, URL, "200"]], {replay(): (200, headers(), body)})
        doc = self.collector.fetch(URL)
        self.assertIsInstance(doc, ArchivedDocument)
        self.assertGreater(len(doc.content), 50_000)
        self.assertIn("Unique final qualifier.", doc.content)
        self.assertEqual(doc.capture_at, "2024-12-01T12:00:00+00:00")

    def test_latest_200_preferred_over_newer_redirect(self):
        self.stub([["20241230120000", URL, "301"], [OLD, URL, "200"], [NEW, URL, "200"]],
                  {replay(): (200, headers(), b"<p>Complete.</p>")})
        self.assertIsNotNone(self.collector.fetch(URL))
        self.assertEqual(self.calls[-1], replay())

    def test_bad_capture_falls_back_with_failure_record(self):
        self.stub([[OLD, URL, "200"], [NEW, URL, "200"]],
                  {replay(): (200, headers(OLD), b"<p>Wrong date.</p>"),
                   replay(OLD): (200, headers(OLD), b"<p>Older primary record.</p>")})
        doc = self.collector.fetch(URL)
        self.assertEqual(doc.capture_at, "2023-12-01T12:00:00+00:00")
        self.assertTrue(any(x.get("code") == "unverified_archive_timestamp" for x in self.collector.requests))

    def test_extraction_failure_falls_back_before_cache_commit(self):
        cases = [
            (headers(), b"<html><script>hidden only</script></html>", "empty_document"),
            ({**headers(), "content-type": "text/html; charset=missing-charset"}, b"<p>Text.</p>", "unsupported_charset"),
            (headers(), b"<p>" + b"a" * 101 + b"</p>", "extracted_text_too_large"),
        ]
        for bad_headers, body, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                collector = ArchiveCollector(cutoff=CUTOFF, cache_dir=directory, timeout=30, max_chars=100)
                self.stub([[OLD, URL, "200"], [NEW, URL, "200"]],
                          {replay(): (200, bad_headers, body),
                           replay(OLD): (200, headers(OLD), b"<p>Older complete primary record.</p>")}, collector)
                doc = collector.fetch(URL)
                self.assertIsNotNone(doc, collector.errors)
                self.assertEqual(doc.archive_url, replay(OLD))
                self.assertTrue(any(x.get("code") == code for x in collector.requests))
                saved = json.loads(next(Path(directory).glob("*.json")).read_text())
                self.assertEqual(saved["archive_url"], replay(OLD))

    def test_fallback_attempts_are_bounded(self):
        stamps = [f"202412{day:02d}120000" for day in range(1, 9)]
        self.stub([[s, URL, "200"] for s in stamps],
                  {replay(s): _SourceError("archive_http_failed") for s in stamps})
        self.assertIsNone(self.collector.fetch(URL))
        failures = [x for x in self.collector.requests if x["operation"] == "archive_candidate_error"]
        self.assertEqual(len(failures), 5)
        self.assertEqual(self.collector.errors[-1]["code"], "archive_candidates_exhausted")

    def test_cutoff_blocks_live_and_future_redirects(self):
        for destination in ["https://example.org/current", replay("20250101120000")]:
            with self.subTest(destination=destination):
                self.calls.clear()
                collector = self.make_collector()
                self.stub([[NEW, URL, "301"]], {replay(): (302, {"location": destination}, b"")}, collector)
                self.assertIsNone(collector.fetch(URL))
                self.assertNotIn(destination, self.calls)
                self.assertTrue(any(x.get("code") in {"non_archive_redirect", "post_cutoff_archive_redirect"}
                                    for x in collector.requests))

    def test_original_identity_must_match_replay(self):
        self.stub([[NEW, URL, "200"]], {replay(): (200, headers(original="https://unrelated.example/paper"), b"Text")})
        self.assertIsNone(self.collector.fetch(URL))
        self.assertTrue(any(x.get("code") == "archive_original_identity_mismatch" for x in self.collector.requests))

    def test_decoded_byte_guard_logs_before_rejecting(self):
        self.collector.max_bytes = 50
        with patch("archive_collector.subprocess.Popen", FakeCurl), patch.object(FakeCurl, "body", b"a" * 1000):
            with self.assertRaisesRegex(_SourceError, "response_too_large"):
                self.collector._http(replay(), time.monotonic() + 30)
        record = self.collector.requests[-1]
        self.assertEqual(record["decoded_bytes"], 51)
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["error"], "response_too_large")
        self.assertFalse(record["body_complete"])

    def test_cache_digest_tampering_rejected(self):
        _, body = self.prime()
        body.write_bytes(b"Altered evidence")
        self.assert_cache_rejected("archive_cache_digest_mismatch")

    def test_cache_requested_identity_tampering_rejected(self):
        path, _ = self.prime()
        meta = json.loads(path.read_text()); meta["requested_url"] = "https://elsewhere.example/"
        path.write_text(json.dumps(meta))
        self.assert_cache_rejected("archive_cache_identity_mismatch")

    def test_cache_date_tampering_rejected(self):
        path, _ = self.prime()
        meta = json.loads(path.read_text()); meta["headers"]["memento-datetime"] = headers(OLD)["memento-datetime"]
        path.write_text(json.dumps(meta))
        self.assert_cache_rejected("archive_cache_timestamp_mismatch")

    def test_cache_future_hop_rejected(self):
        path, _ = self.prime()
        meta = json.loads(path.read_text()); meta["replay_chain"].insert(0, replay("20250101120000"))
        path.write_text(json.dumps(meta))
        self.assert_cache_rejected("post_cutoff_archive_redirect")

    def test_v7_cache_revalidated_without_rewriting(self):
        path, _ = self.prime()
        meta = json.loads(path.read_text())
        for name in ["schema", "cutoff", "decoded_bytes", "replay_chain", "selected_capture"]:
            meta.pop(name)
        path.write_text(json.dumps(meta)); old_bytes = path.read_bytes()
        other = self.make_collector()
        other._http = lambda *args, **kwargs: self.fail("Valid V7 cache should not fetch")
        self.assertIsNotNone(other.fetch(URL))
        self.assertEqual(path.read_bytes(), old_bytes)
        self.assertEqual(other.archive_records[URL]["replay_chain_scope"], "final_only_legacy_v7")
        self.assertEqual(other.requests[-1]["source_cache_schema"], 1)

    def test_incomplete_cache_rejected(self):
        path, _ = self.prime(); path.unlink()
        self.assert_cache_rejected("archive_cache_incomplete")

    def test_no_capture_does_not_fetch_current_page(self):
        self.stub([], {})
        self.assertIsNone(self.collector.fetch(URL))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.collector.errors[-1]["code"], "no_pre_cutoff_capture")

    def test_pdf_stays_explicitly_unsupported(self):
        self.stub([[NEW, URL, "200"]], {replay(): (200, {**headers(), "content-type": "application/pdf"}, b"%PDF-1.4\n...")})
        self.assertIsNone(self.collector.fetch(URL))
        self.assertTrue(any(x.get("code") == "unsupported_pdf" for x in self.collector.requests))

    def test_post_cutoff_replay_validation(self):
        with self.assertRaisesRegex(_SourceError, "post_cutoff_archive_redirect"):
            validate_replay(replay("20250101120000"), self.collector.cutoff)


def check_real_capture(root):
    # Diagnostic fixtures are supplied locally and never injected into a model
    # prompt. This tests the actual existing parser on bytes fetched separately.
    meta = json.loads((root / "nature-replay-latest.meta.json").read_text())
    body = (root / "nature-replay-latest.body").read_bytes()
    assert meta["returncode"] == 0 and hashlib.sha256(body).hexdigest() == meta["sha256"]
    lines = (root / "nature-replay-latest.headers").read_text().splitlines()
    source_headers = {k.strip().lower(): v.strip() for line in lines if ":" in line
                      for k, v in [line.split(":", 1)]}
    original = meta["url"].split("id_/", 1)[1]
    stamp = meta["url"].split("/web/", 1)[1][:14]
    with tempfile.TemporaryDirectory() as directory:
        collector = ArchiveCollector(cutoff=CUTOFF, cache_dir=directory, timeout=30)
        def fixture_http(url, deadline, **kwargs):
            if "/cdx/search/cdx?" in url:
                return 200, {}, index([[stamp, original, "200"]])
            assert url == meta["url"], "Unexpected network request"
            return 200, source_headers, body
        collector._http = fixture_http
        doc = collector.fetch(original)
        assert doc is not None, collector.errors
        assert len(body) > 1_000_000 and len(doc.content) > 50_000
        for section in ["Conclusions", "Methods", "Data availability", "References", "Change history", "Supplementary Information"]:
            assert section in doc.content, section
        assert doc.content == (root / "nature-replay-latest.txt").read_text()
        assert doc.capture_at == "2024-12-31T23:05:38+00:00"
        print(json.dumps({"real_capture": "passed", "raw_bytes": len(body),
                          "full_text_chars": len(doc.content), "sha256": meta["sha256"],
                          "capture_at": doc.capture_at, "text_preserved_exactly": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostic-dir", type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArchiveChecks))
    if not result.wasSuccessful():
        raise SystemExit(1)
    if args.diagnostic_dir:
        check_real_capture(args.diagnostic_dir)
