import socket
import unittest
from unittest.mock import patch

import source_content


PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = {key.casefold(): value for key, value in (headers or {}).items()}

    def getheader(self, name):
        return self.headers.get(name.casefold())

    def read(self, amount=None):
        return self.body if amount is None else self.body[:amount]


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method, path, headers=None):
        self.requests.append((method, path, headers or {}))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class SourceContentTests(unittest.TestCase):
    def fetch_with(self, response, url="https://example.com/story"):
        connection = FakeConnection(response)
        with patch("source_content.socket.getaddrinfo", return_value=PUBLIC_DNS), \
             patch("source_content._connection_for", return_value=connection):
            result = source_content.fetch_source(url)
        return result, connection

    def test_fetches_article_main_text_and_omits_navigation_and_scripts(self):
        html = b"""<html><head><title>Fallback</title>
          <meta property='og:title' content='A careful AI result'></head><body>
          <nav>Subscribe Home Markets</nav><main><h1>A careful AI result</h1>
          <p>This is the first substantial paragraph with evidence and context.</p>
          <p>This is the second substantial paragraph describing the consequences.</p>
          <script>steal()</script></main><footer>Privacy</footer></body></html>"""
        result, connection = self.fetch_with(FakeResponse(200, html, {
            "Content-Type": "text/html; charset=utf-8",
        }))
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["title"], "A careful AI result")
        self.assertIn("first substantial paragraph", result["text"])
        self.assertNotIn("Subscribe", result["text"])
        self.assertNotIn("steal", result["text"])
        self.assertRegex(result["content_hash"], r"^[0-9a-f]{64}$")
        method, path, headers = connection.requests[0]
        self.assertEqual((method, path), ("GET", "/story"))
        self.assertEqual(headers["Host"], "example.com")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)

    def test_blocks_private_or_mixed_dns_before_connection(self):
        mixed = PUBLIC_DNS + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("source_content.socket.getaddrinfo", return_value=mixed), \
             patch("source_content._connection_for") as connect:
            result = source_content.fetch_source("https://example.com/admin")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("public IP", result["error"])
        connect.assert_not_called()

    def test_revalidates_redirect_and_blocks_private_destination(self):
        redirect = FakeConnection(FakeResponse(302, headers={"Location": "http://localhost/secret"}))

        def dns(host, port, type):
            if host == "localhost":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return PUBLIC_DNS

        with patch("source_content.socket.getaddrinfo", side_effect=dns), \
             patch("source_content._connection_for", return_value=redirect):
            result = source_content.fetch_source("https://example.com/start")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["final_url"], "http://localhost/secret")

    def test_rejects_credentials_without_retaining_them(self):
        with patch("source_content.socket.getaddrinfo") as dns:
            result = source_content.fetch_source("https://user:secret@example.com/story")
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("secret", result["url"])
        self.assertNotIn("user", result["url"])
        dns.assert_not_called()

    def test_pdf_is_explicitly_unsupported(self):
        result, _ = self.fetch_with(FakeResponse(200, b"%PDF", {
            "Content-Type": "application/pdf",
        }), "https://example.com/paper.pdf")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertEqual(result["text"], "")

    def test_connection_setup_failure_is_sanitized(self):
        with patch("source_content.socket.getaddrinfo", return_value=PUBLIC_DNS), \
             patch("source_content._connection_for", side_effect=OSError("secret detail")):
            result = source_content.fetch_source("https://example.com/story")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "network request failed")
        self.assertNotIn("secret", result["error"])

    def test_size_limit_blocks_declared_and_streamed_oversize_content(self):
        declared, _ = self.fetch_with(FakeResponse(200, b"small", {
            "Content-Type": "text/html", "Content-Length": str(source_content.MAX_RESPONSE_BYTES + 1),
        }))
        streamed, _ = self.fetch_with(FakeResponse(200, b"x" * (source_content.MAX_RESPONSE_BYTES + 1), {
            "Content-Type": "text/plain",
        }))
        self.assertEqual(declared["status"], "blocked")
        self.assertTrue(declared["truncated"])
        self.assertEqual(streamed["status"], "blocked")

    def test_plaintext_and_unsupported_scheme_have_complete_shape(self):
        plain, _ = self.fetch_with(FakeResponse(200, "hello\nworld".encode(), {
            "Content-Type": "text/plain",
        }))
        unsupported = source_content.fetch_source("file:///etc/passwd")
        self.assertEqual(plain["text"], "hello\nworld")
        self.assertEqual(unsupported["status"], "unsupported")
        expected = {"url", "final_url", "status", "title", "text", "error",
                    "content_type", "fetched_at", "content_hash", "truncated"}
        self.assertEqual(set(plain), expected)
        self.assertEqual(set(unsupported), expected)


if __name__ == "__main__":
    unittest.main()
