"""Source collection boundary tests: injected data and mocked sockets only."""
from datetime import datetime
import json
import socket
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, patch

from newsverify.news_sources import NewsSourceCollector, SourceDocument


URL = "https://news.example.org/story"
HTML = b'''<html><head><title>Example &amp; findings</title>
<meta property="article:published_time" content="2020-01-02T03:04:05Z">
<script>private script data</script></head><body><h1>Reported finding</h1>
<p>The report cites <a href="/paper#methods"><strong>the primary paper</strong></a>.</p>
<p>It includes seven observations.</p><style>hidden styles</style>
<a href="javascript:alert(1)">invalid link</a></body></html>'''


def reply(body=HTML, content_type="text/html; charset=utf-8", url=URL, **headers):
    return url, {"Content-Type": content_type, **headers}, body


class SourceDocumentTests(unittest.TestCase):
    def test_live_research_and_formal_material_share_capture_availability(self):
        from newsverify.news_client import TracingClient

        doc = SourceDocument(URL, 'Current version', 'Captured content.',
                             '2026-09-09T00:00:00Z', '2020-01-01T00:00:00Z')
        collector = NewsSourceCollector(fetch=Mock())
        collector.documents[URL] = doc
        row = TracingClient(Mock(), collector)._evidence()[0]
        material = doc.to_material('captured-v1')

        self.assertEqual(doc.available_at, doc.retrieved_at)
        self.assertEqual(row['available_at'], material.available_at)
        self.assertEqual(row['availability_basis'], material.availability_basis)
        self.assertEqual(doc.availability_basis, material.availability_basis)
        self.assertNotEqual(row['available_at'], doc.published_at)
        self.assertIn('does not establish earlier availability', row['availability_basis'])
        collector._fetch.assert_not_called()

    def test_html_table_keeps_headers_and_numeric_values_in_separate_cells(self):
        body = (b'Before.<table><caption>Measurements</caption><tr><th>Control</th><th>Treatment</th></tr>'
                b'<tr><td>10</td><td>20</td></tr><tr><td>3</td><td>40</td></tr>'
                b'</table>End.')
        doc = NewsSourceCollector(fetch=Mock(return_value=reply(body))).fetch(URL)
        self.assertEqual(doc.content, 'Before.\nMeasurements\n| Control | Treatment |\n| 10 | 20 |\n| 3 | 40 |\nEnd.')
        self.assertEqual(doc.to_material('table-v1').content, doc.content)

    def test_html_table_preserves_empty_edge_cells_and_empty_rows(self):
        body = (b'<table><tr><td></td><td>0</td><td> </td></tr>'
                b'<tr><td></td><td></td><td></td></tr><tr></tr>'
                b'<tr><td>5</td><td></td><td>7</td></tr></table>')
        doc = NewsSourceCollector(fetch=Mock(return_value=reply(body))).fetch(URL)
        self.assertEqual(doc.content, '|  | 0 |  |\n|  |  |  |\n||\n| 5 |  | 7 |')

    def test_html_table_normalizes_within_cells_without_splitting_rows_or_losing_links(self):
        body = (b'<table>\n<tr>\n<th><p>Sample A</p></th>\n<td><p>1.5</p>'
                b'<p>mg/L</p><a href="/record">record</a></td><td>3|4</td></tr>\n</table>')
        doc = NewsSourceCollector(fetch=Mock(return_value=reply(body))).fetch(URL)
        self.assertEqual(doc.content, '| Sample A | 1.5 mg/L record [https://news.example.org/record] | 3\\|4 |')
        self.assertEqual(doc.links, [{'url': 'https://news.example.org/record', 'text': 'record'}])

    def test_html_table_handles_optional_cell_and_row_end_tags(self):
        body = b'<table><tr><th>A<th>B<tr><td>10<td>20</table><p>After.</p>'
        doc = NewsSourceCollector(fetch=Mock(return_value=reply(body))).fetch(URL)
        self.assertEqual(doc.content, '| A | B |\n| 10 | 20 |\nAfter.')

    def test_html_preserves_primary_links_and_uses_capture_time_for_version_availability(self):
        transport = Mock(return_value=reply(**{"Set-Cookie": "secret cookie", "Authorization": "secret header"}))
        collector = NewsSourceCollector(fetch=transport)

        doc = collector.fetch("https://NEWS.example.org:443/story#headline")

        self.assertEqual(doc.title, "Example & findings")
        self.assertIn("the primary paper [https://news.example.org/paper]", doc.content)
        self.assertIn("seven observations", doc.content)
        self.assertNotIn("private script", doc.content)
        self.assertNotIn("hidden styles", doc.content)
        self.assertEqual(doc.links, [{"url": "https://news.example.org/paper", "text": "the primary paper"}])
        self.assertEqual(doc.published_at, "2020-01-02T03:04:05Z")
        material = doc.to_material("captured-v1")
        self.assertEqual(material.available_at, doc.retrieved_at)
        self.assertGreater(datetime.fromisoformat(material.available_at), datetime.fromisoformat(doc.published_at))
        self.assertIn("does not establish earlier availability", material.availability_basis)
        self.assertEqual(material.content, doc.content)
        self.assertIs(collector.fetch(URL), doc)
        self.assertEqual(list(collector.documents), [URL])
        transport.assert_called_once_with(URL)
        self.assertNotIn("secret", json.dumps([collector.requests, collector.errors, doc.__dict__]))

    def test_plain_text_keeps_content_and_urls_without_inventing_publication_date(self):
        body = "Caf\xe9 report. See https://records.example.org/original.\nSecond line.".encode("latin-1")
        collector = NewsSourceCollector(fetch=Mock(return_value=reply(body, "text/plain; charset=iso-8859-1")))
        doc = collector.fetch(URL)
        self.assertEqual(doc.content, body.decode("latin-1"))
        self.assertIsNone(doc.published_at)
        self.assertEqual(doc.links, [{"url": "https://records.example.org/original", "text": ""}])

    def test_date_only_metadata_has_no_invented_timezone(self):
        body = b'<head><meta property="article:published_time" content="2020-01-02"></head><body>Text.</body>'
        doc = NewsSourceCollector(fetch=Mock(return_value=reply(body))).fetch(URL)
        self.assertIsNone(doc.published_at)

    def test_unsupported_oversized_and_empty_sources_fail_explicitly(self):
        variants = [
            (reply(b"%PDF-1.7", "application/pdf"), {}, "unsupported_pdf"),
            (reply(b"%PDF-1.7", "text/plain"), {}, "unsupported_pdf"),
            (reply(b'{}', "application/json"), {}, "unsupported_content_type"),
            (reply(b"compressed", **{"Content-Encoding": "gzip"}), {}, "unsupported_content_encoding"),
            (reply(b"too long", "text/plain"), {"max_bytes": 3}, "response_too_large"),
            (reply(b"ok", "text/plain", **{"Content-Length": "10000"}), {"max_bytes": 100}, "response_too_large"),
            (reply(b"123456", "text/plain"), {"max_chars": 5}, "extracted_text_too_large"),
            (reply(b"<p>123456</p>"), {"max_chars": 5}, "extracted_text_too_large"),
            (reply(b"<table><tr><td></td><td></td></tr></table>"), {"max_chars": 5}, "extracted_text_too_large"),
            (reply(b"<script>invisible</script>"), {}, "empty_document"),
        ]
        for payload, config, code in variants:
            with self.subTest(code=code, config=config):
                collector = NewsSourceCollector(fetch=Mock(return_value=payload), **config)
                self.assertIsNone(collector.fetch(URL))
                self.assertEqual(collector.errors[-1]["code"], code)
                self.assertEqual(collector.documents, {})


class SourceSafetyTests(unittest.TestCase):
    def test_private_credentialed_and_invalid_urls_never_reach_injected_transport(self):
        urls = ["file:///etc/passwd", "https://alice:password@example.org/", "http://localhost/",
                "http://sub.localhost/", "http://127.0.0.1/", "https://10.1.2.3/",
                "http://169.254.169.254/latest/", "http://100.64.0.1/", "http://[::1]/",
                "http://[fc00::1]/", "http://[::ffff:127.0.0.1]/", "http://224.0.0.1/",
                "http://example.org\\@localhost/", "https://example.org/\r\nAuthorization:secret",
                "https://example.org:99999/", "https://example.org/%zz", "https://intranet/"]
        transport = Mock()
        collector = NewsSourceCollector(fetch=transport)
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(collector.fetch(url))
        transport.assert_not_called()
        self.assertNotIn("password", json.dumps(collector.errors))
        self.assertNotIn("secret", json.dumps(collector.errors))

    def test_redirects_are_revalidated_before_the_next_connection(self):
        collector = NewsSourceCollector()
        with patch.object(collector, "_exchange", return_value=(302, {"location": "http://127.0.0.1/private"}, b"")) as exchange:
            self.assertIsNone(collector.fetch(URL))
        exchange.assert_called_once()
        self.assertEqual(collector.errors[-1]["code"], "non_public_url")
        redirected = NewsSourceCollector(fetch=Mock(return_value=reply(url="http://10.0.0.1/private")))
        self.assertIsNone(redirected.fetch(URL))
        self.assertEqual(redirected.errors[-1]["code"], "non_public_url")

    def test_public_relative_redirect_and_alias_cache(self):
        collector = NewsSourceCollector()
        with patch.object(collector, "_exchange", side_effect=[
            (302, {"location": "/final#section"}, b""),
            (200, {"content-type": "text/plain"}, b"Actual final document"),
        ]) as exchange:
            document = collector.fetch(URL)
            self.assertIs(collector.fetch(URL), document)
        self.assertEqual(document.url, "https://news.example.org/final")
        self.assertEqual(exchange.call_count, 2)
        self.assertEqual(exchange.call_args_list[1].args[0], document.url)

    def test_dns_private_and_mixed_answers_are_blocked_before_connecting(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        for answer in ([private], [public, private]):
            with self.subTest(answer=answer):
                collector = NewsSourceCollector()
                with patch("newsverify.news_sources.socket.getaddrinfo", return_value=answer), \
                     patch("newsverify.news_sources.socket.socket") as connect:
                    self.assertIsNone(collector.fetch(URL))
                connect.assert_not_called()
                self.assertEqual(collector.errors[-1]["code"], "non_public_address")

    def test_dns_timeout_does_not_start_a_connection(self):
        released = Event()
        def resolver(*args, **kwargs):
            released.wait(1)
            return []
        collector = NewsSourceCollector(timeout=0.01)
        try:
            with patch("newsverify.news_sources.socket.getaddrinfo", side_effect=resolver), \
                 patch("newsverify.news_sources.socket.socket") as connect:
                self.assertIsNone(collector.fetch(URL))
            connect.assert_not_called()
            self.assertEqual(collector.errors[-1]["code"], "timeout")
        finally:
            released.set()

    def test_http_connection_uses_validated_ip_and_tls_original_hostname(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        sock, context = MagicMock(), MagicMock()
        context.wrap_socket.return_value = sock
        response = Mock(status=200)
        response.getheaders.return_value = [("Content-Type", "text/plain"), ("Set-Cookie", "do not expose")]
        response.read1.side_effect = [b"Captured original text.", b""]
        connection = MagicMock()
        connection.getresponse.return_value = response
        collector = NewsSourceCollector()
        with patch("newsverify.news_sources.socket.getaddrinfo", return_value=[address]) as dns, \
             patch("newsverify.news_sources.socket.socket", return_value=sock), \
             patch("newsverify.news_sources._tls_context", return_value=context), \
             patch("newsverify.news_sources.http.client.HTTPConnection", return_value=connection):
            document = collector.fetch(URL)
        self.assertEqual(document.content, "Captured original text.")
        dns.assert_called_once()
        sock.connect.assert_called_once_with(("93.184.216.34", 443))
        context.wrap_socket.assert_called_once_with(sock, server_hostname="news.example.org")
        method, target = connection.request.call_args.args
        self.assertEqual((method, target), ("GET", "/story"))
        headers = connection.request.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Referer", headers)
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertNotIn("do not expose", json.dumps([collector.requests, collector.errors]))


class SourceBudgetTests(unittest.TestCase):
    def test_failed_fetches_consume_budget_and_diagnostics_are_sanitized(self):
        transport = Mock(side_effect=RuntimeError("Authorization: secret-value; Set-Cookie: private"))
        collector = NewsSourceCollector(max_documents=1, fetch=transport)
        self.assertIsNone(collector.fetch(URL))
        self.assertIsNone(collector.fetch(URL))
        self.assertIsNone(collector.fetch("https://second.example.org/"))
        transport.assert_called_once()
        self.assertEqual([e["code"] for e in collector.errors], ["fetch_failed", "document_limit"])
        self.assertNotIn("secret-value", json.dumps(collector.errors))

    def test_search_fetches_actual_results_and_never_substitutes_search_snippets(self):
        search = Mock(return_value=[{"url": URL, "title": "Invented search snippet"}, "https://other.example.org/doc"])
        transport = Mock(side_effect=lambda url: reply(b"Actual fetched text.", "text/plain", url))
        collector = NewsSourceCollector(max_documents=1, search=search, fetch=transport)
        found = collector.search("test origin", limit=3)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].content, "Actual fetched text.")
        self.assertEqual(found[0].title, URL)
        search.assert_called_once_with("test origin", 1)
        transport.assert_called_once_with(URL)

    def test_failed_searches_consume_budget_and_queries_are_bounded(self):
        search = Mock(side_effect=RuntimeError("secret provider response"))
        collector = NewsSourceCollector(max_searches=1, search=search, fetch=Mock())
        self.assertEqual(collector.search("x" * 513), [])
        self.assertEqual(collector.search("first"), [])
        self.assertEqual(collector.search("first"), [])
        self.assertEqual(collector.search("second"), [])
        search.assert_called_once()
        self.assertEqual([e["code"] for e in collector.errors], ["invalid_query", "search_failed", "search_limit"])
        self.assertNotIn("secret", json.dumps(collector.errors))

    def test_bing_rss_only_supplies_candidate_urls_and_pages_are_fetched(self):
        rss = b'<rss><channel><item><title>Snippet</title><link>https://news.example.org/story</link><description>Unverified snippet</description></item></channel></rss>'
        transport = Mock(return_value=reply(b"Actual page text.", "text/plain"))
        collector = NewsSourceCollector(fetch=transport)
        with patch.object(collector, "_download", return_value=("https://www.bing.com/", {}, rss)) as download:
            documents = collector.search("exact origin & date")
        endpoint = download.call_args.args[0]
        self.assertEqual(endpoint, "https://www.bing.com/search?format=rss&q=exact+origin+%26+date")
        self.assertEqual(documents[0].content, "Actual page text.")
        transport.assert_called_once_with(URL)

    def test_invalid_limits_fail_before_collection(self):
        for config in ({"max_documents": 0}, {"max_documents": True}, {"max_searches": -1},
                       {"max_bytes": 0}, {"max_chars": 0}, {"timeout": 0}, {"timeout": float("inf")}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                NewsSourceCollector(**config)


if __name__ == "__main__":
    unittest.main()
