"""Fetch public web pages for later, evidence-grounded news analysis.

The fetcher deliberately has no cookie, authentication, proxy, or JavaScript
support.  DNS answers are validated and the connection is pinned to one of the
validated addresses so redirects cannot be used to reach local services.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 250_000
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 15.0
_ALLOWED_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
_REDIRECTS = {301, 302, 303, 307, 308}


def _safe_url(value: str) -> str:
    """Return a URL without user information, even on rejected inputs."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port else ""
        return urlunsplit((parts.scheme, host + port, parts.path, parts.query, ""))
    except (TypeError, ValueError):
        return ""


def _result(url: str, *, final_url: str = "", status: str, title: str = "",
            text: str = "", error: str = "", content_type: str = "",
            truncated: bool = False) -> dict:
    return {
        "url": _safe_url(url),
        "final_url": _safe_url(final_url or url),
        "status": status,
        "title": title,
        "text": text,
        "error": re.sub(r"[\x00-\x1f\x7f]+", " ", error).strip()[:240],
        "content_type": content_type,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
        "truncated": bool(truncated),
    }


def _public_addresses(hostname: str, port: int) -> list[str]:
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise OSError("DNS lookup failed") from exc
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise OSError("DNS lookup returned no addresses")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise PermissionError("DNS returned an invalid address") from exc
        if not parsed.is_global:
            raise PermissionError("destination is not a public IP address")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a checked IP while validating TLS for the URL hostname."""

    def __init__(self, hostname: str, address: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout,
                         context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout,
                                       self.source_address)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _connection_for(scheme: str, hostname: str, address: str, port: int,
                    timeout: float):
    if scheme == "https":
        return _PinnedHTTPSConnection(hostname, address, port, timeout)
    return http.client.HTTPConnection(address, port=port, timeout=timeout)


def _validated_target(url: str) -> tuple[str, str, int, str, str, list[str], str]:
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        raise ValueError("invalid URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid URL") from exc
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise NotImplementedError("only HTTP(S) URLs are supported")
    if parts.username is not None or parts.password is not None:
        raise PermissionError("URLs containing credentials are blocked")
    if not parts.hostname:
        raise ValueError("URL has no hostname")
    try:
        hostname = parts.hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("invalid hostname") from exc
    port = port or (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    addresses = _public_addresses(hostname, port)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    host_header = hostname
    if ":" in hostname:
        host_header = f"[{hostname}]"
    if port != (443 if scheme == "https" else 80):
        host_header += f":{port}"
    normalized = urlunsplit((scheme, host_header, parts.path or "/", parts.query, ""))
    return scheme, hostname, port, host_header, path, addresses, normalized


class _ArticleParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}
    _BLOCK = {"article", "aside", "blockquote", "br", "div", "h1", "h2", "h3",
              "h4", "h5", "h6", "li", "main", "p", "section", "table", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._preferred_depth = 0
        self._body_depth = 0
        self._title_depth = 0
        self._all: list[str] = []
        self._preferred: list[str] = []
        self._title: list[str] = []
        self.og_title = ""
        self.description = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        values = {str(key).casefold(): value for key, value in attrs if key}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            content = values.get("content") or ""
            if key == "og:title" and content:
                self.og_title = content
            elif key in {"description", "og:description"} and content and not self.description:
                self.description = content
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in {"main", "article"}:
            self._preferred_depth += 1
        if tag == "body":
            self._body_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag in self._BLOCK:
            self._append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag in self._BLOCK:
            self._append("\n")
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag == "body" and self._body_depth:
            self._body_depth -= 1
        if tag in {"main", "article"} and self._preferred_depth:
            self._preferred_depth -= 1
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._title_depth:
            self._title.append(data)
        if not self._skip_depth and (self._body_depth or self._preferred_depth):
            self._append(data)

    def _append(self, value: str):
        if self._skip_depth:
            return
        if self._body_depth or self._preferred_depth:
            self._all.append(value)
        if self._preferred_depth:
            self._preferred.append(value)


def _clean_text(parts: list[str]) -> str:
    raw = "".join(parts).replace("\xa0", " ")
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _decode(body: bytes, content_type_header: str) -> str:
    match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type_header,
                      flags=re.IGNORECASE)
    candidates = [match.group(1) if match else "", "utf-8", "windows-1252"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("utf-8", errors="replace")


def _extract(body: bytes, media_type: str, content_type_header: str) -> tuple[str, str, bool]:
    decoded = _decode(body, content_type_header)
    if media_type == "text/plain":
        text = _clean_text([decoded])
        clipped = len(text) > MAX_TEXT_CHARS
        return "", text[:MAX_TEXT_CHARS], clipped
    parser = _ArticleParser()
    parser.feed(decoded)
    parser.close()
    preferred = _clean_text(parser._preferred)
    text = preferred if len(preferred) >= 200 else _clean_text(parser._all)
    if len(text) < 80 and parser.description:
        text = _clean_text([text, "\n", parser.description])
    title = _clean_text([parser.og_title or ""]) or _clean_text(parser._title)
    clipped = len(text) > MAX_TEXT_CHARS
    return title[:500], text[:MAX_TEXT_CHARS], clipped


def fetch_source(url: str, *, raw: bool = False) -> dict:
    """Fetch one public HTTP(S) source without credentials, cookies, or JS."""
    current = url
    started = time.monotonic()
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            scheme, hostname, port, host_header, path, addresses, normalized = _validated_target(current)
        except NotImplementedError as exc:
            return _result(url, final_url=current, status="unsupported", error=str(exc))
        except PermissionError as exc:
            return _result(url, final_url=current, status="blocked", error=str(exc))
        except (OSError, ValueError):
            return _result(url, final_url=current, status="failed", error="invalid URL or DNS lookup failed")

        remaining = FETCH_TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            return _result(url, final_url=normalized, status="failed", error="fetch timeout")
        connection = None
        try:
            connection = _connection_for(scheme, hostname, addresses[0], port, remaining)
            connection.request("GET", path, headers={
                "Host": host_header,
                "User-Agent": "HollobitNewsResearch/1.0",
                "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9, */*;q=0.1",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            response = connection.getresponse()
            if response.status in _REDIRECTS:
                location = response.getheader("Location")
                if not location:
                    return _result(url, final_url=normalized, status="failed",
                                   error="redirect has no Location header")
                if redirect_count >= MAX_REDIRECTS:
                    return _result(url, final_url=normalized, status="failed",
                                   error="too many redirects")
                current = urljoin(normalized, location)
                continue
            content_type_header = response.getheader("Content-Type") or ""
            media_type = content_type_header.partition(";")[0].strip().casefold()
            if media_type == "application/pdf" or urlsplit(normalized).path.casefold().endswith(".pdf"):
                return _result(url, final_url=normalized, status="unsupported",
                               error="PDF extraction is not supported", content_type=media_type)
            if response.status < 200 or response.status >= 300:
                return _result(url, final_url=normalized, status="failed",
                               error=f"HTTP {response.status}", content_type=media_type)
            if not raw and media_type not in _ALLOWED_TYPES:
                return _result(url, final_url=normalized, status="unsupported",
                               error="unsupported content type", content_type=media_type)
            encoding = (response.getheader("Content-Encoding") or "identity").casefold()
            if encoding not in {"", "identity"}:
                return _result(url, final_url=normalized, status="unsupported",
                               error="compressed response was not requested", content_type=media_type)
            length = response.getheader("Content-Length")
            if length and length.isdecimal() and int(length) > MAX_RESPONSE_BYTES:
                return _result(url, final_url=normalized, status="blocked",
                               error="response exceeds 2 MiB limit", content_type=media_type,
                               truncated=True)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return _result(url, final_url=normalized, status="blocked",
                               error="response exceeds 2 MiB limit", content_type=media_type,
                               truncated=True)
            if raw:
                result = _result(url, final_url=normalized, status="fetched", content_type=media_type)
                result['body'] = body
                return result
            title, text, clipped = _extract(body, media_type, content_type_header)
            return _result(url, final_url=normalized, status="fetched", title=title,
                           text=text, content_type=media_type, truncated=clipped)
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
            return _result(url, final_url=normalized, status="failed", error="network request failed")
        finally:
            if connection is not None:
                connection.close()
    return _result(url, final_url=current, status="failed", error="too many redirects")
