"""Deterministic, network-free grouping and classification of article links."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from functools import lru_cache
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from classification import TOPICS


CONTENT_TYPE_TITLES = {
    "news": "뉴스 기사",
    "paper": "논문 소개",
    "blog": "블로그·해설",
    "social": "소셜 게시물",
    "tool": "도구·프로젝트",
    "other": "기타",
}

_TRACKING_KEYS = {"fbclid", "gclid"}
_URL_START = re.compile(r"https?://", re.IGNORECASE)
_TOKEN = re.compile(r"[가-힣]{2,}|[a-z0-9]+(?:[._+#-][a-z0-9]+)*", re.IGNORECASE)
_GENERIC_TOKENS = {
    "ai", "인공지능", "뉴스", "소식", "공개", "출시", "발표", "소개", "분석",
    "new", "news", "release", "released", "launch", "launched", "announces",
    "the", "and", "for", "with", "from", "this", "that", "about", "using",
}


def _clean_url_tail(candidate: str) -> str:
    """Remove prose/Markdown punctuation without damaging balanced URL parens."""
    candidate = candidate.rstrip(".,;:!?\"'…，。；：！？")
    while candidate and candidate[-1] in ")]}":
        closer = candidate[-1]
        opener = {")": "(", "]": "[", "}": "{"}[closer]
        if candidate.count(closer) > candidate.count(opener):
            candidate = candidate[:-1].rstrip(".,;:!?\"'…，。；：！？")
        else:
            break
    return candidate


def _is_valid_http_url(value: str) -> bool:
    if not value or any(ord(char) < 32 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed/non-numeric ports.
        _ = parsed.port
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def extract_links(text: str) -> list[str]:
    """Return ordered unique HTTP(S) links from Markdown or plain prose.

    Scanning instead of a single ``[^)]`` regex retains valid parentheses in URLs,
    while the tail cleanup removes the Markdown closing parenthesis.
    """
    source = str(text or "")
    links: list[str] = []
    seen: set[str] = set()
    for match in _URL_START.finditer(source):
        end = match.end()
        while end < len(source) and not source[end].isspace() and source[end] not in '<>"`':
            end += 1
        candidate = _clean_url_tail(source[match.start():end])
        if _is_valid_http_url(candidate) and candidate not in seen:
            seen.add(candidate)
            links.append(candidate)
    return links


def _netloc(parsed) -> str:
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80)
                                 or (parsed.scheme.lower() == "https" and port == 443)):
        host = f"{host}:{port}"
    return host


def _clean_query(query: str) -> str:
    pairs = [
        (key, value) for key, value in parse_qsl(query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    return urlencode(sorted(pairs, key=lambda pair: (pair[0], pair[1])), doseq=True)


@lru_cache(maxsize=32768)
def canonical_url(url: str) -> str:
    """Canonicalize only well-known aliases and unambiguous tracking noise."""
    value = _clean_url_tail(str(url or "").strip())
    if not _is_valid_http_url(value):
        return ""
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    host = _netloc(parsed)
    path = parsed.path or "/"
    query = _clean_query(parsed.query)
    fragment = parsed.fragment  # Fragments and hash routers may be semantic.

    bare_host = host[4:] if host.startswith("www.") else host

    if bare_host in {"reddit.com", "old.reddit.com", "m.reddit.com"}:
        match = re.search(
            r"/(?:r/[^/]+/)?comments/([a-z0-9]+)(?:/[^/]+/([a-z0-9]+))?",
            path, re.IGNORECASE,
        )
        if match:
            suffix = f"/{match.group(2).lower()}" if match.group(2) else ""
            return urlunsplit(("https", "reddit.com",
                               f"/comments/{match.group(1).lower()}{suffix}",
                               query, fragment))
        host = "reddit.com"
        scheme = "https"
    elif bare_host in {"twitter.com", "mobile.twitter.com", "x.com", "mobile.x.com"}:
        match = re.search(r"/(?:i/web/|[^/]+/)?status(?:es)?/(\d+)", path, re.IGNORECASE)
        if match:
            social_query = [(key, val) for key, val in parse_qsl(query, keep_blank_values=True)
                            if key.casefold() not in {"s", "t", "ref_src"}]
            return urlunsplit(("https", "x.com", f"/status/{match.group(1)}",
                               urlencode(social_query), fragment))
    elif bare_host in {"arxiv.org", "export.arxiv.org"}:
        match = re.match(r"/(?:abs|pdf|html)/([^?#]+?)(?:\.pdf)?/?$", path, re.IGNORECASE)
        if match:
            paper_id = re.sub(r"v\d+$", "", match.group(1), flags=re.IGNORECASE)
            return urlunsplit(("https", "arxiv.org", f"/abs/{paper_id}", query, fragment))
    elif bare_host in {"youtu.be", "youtube.com", "m.youtube.com"}:
        video_id = ""
        if bare_host == "youtu.be":
            video_id = path.strip("/").split("/", 1)[0]
        elif path.rstrip("/") == "/watch":
            video_id = next((v for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                             if k == "v"), "")
        elif re.match(r"/(?:shorts|embed)/[^/]+", path):
            video_id = path.strip("/").split("/", 1)[1]
        if video_id:
            remaining = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                         if k != "v" and not k.casefold().startswith("utm_")
                         and k.casefold() not in _TRACKING_KEYS | {"si", "feature"}]
            return urlunsplit(("https", "youtube.com", "/watch",
                               urlencode(sorted([("v", video_id)] + remaining)), fragment))
    elif bare_host in {"doi.org", "dx.doi.org"}:
        host, scheme = "doi.org", "https"
        path = "/" + path.strip("/").casefold()
    elif host.startswith("www.") and host[4:] in {
        "reuters.com", "bbc.com", "cnn.com", "nytimes.com", "theguardian.com",
        "bloomberg.com", "wsj.com", "ft.com", "techcrunch.com", "wired.com",
        "theverge.com", "arstechnica.com", "scmp.com", "cnbc.com", "zdnet.com",
        "venturebeat.com", "nature.com", "medium.com", "github.com", "economist.com",
    }:
        host = host[4:]
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, query, fragment))


_NEWS_HOSTS = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "cnn.com", "nytimes.com",
    "theguardian.com", "bloomberg.com", "wsj.com", "ft.com", "techcrunch.com",
    "wired.com", "theverge.com", "arstechnica.com", "scmp.com", "cnbc.com",
    "zdnet.com", "venturebeat.com", "yna.co.kr", "hani.co.kr", "chosun.com",
    "joongang.co.kr", "donga.com", "etnews.com", "mk.co.kr", "hankyung.com",
}
_BLOG_HOSTS = {"medium.com", "substack.com", "simonwillison.net", "towardsdatascience.com"}
_PAPER_HOSTS = {
    "arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov", "medrxiv.org", "biorxiv.org",
    "openreview.net", "aclanthology.org", "papers.ssrn.com", "semanticscholar.org",
}
_SOCIAL_HOSTS = {
    "x.com", "twitter.com", "reddit.com", "linkedin.com", "facebook.com",
    "instagram.com", "threads.net", "news.ycombinator.com",
}
_TOOL_HOSTS = {"github.com", "gitlab.com", "huggingface.co", "pypi.org", "npmjs.com"}
_CURATED_NEWS_HOSTS = {
    "leiphone.com", "pandaily.com", "36kr.com", "msn.com", "technode.com", "qbitai.com",
    "infoq.cn", "news.sbs.co.kr", "therundown.ai", "economist.com",
}


def _host_matches(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def classify_link(url: str, title: str = "", text: str = "") -> dict[str, str]:
    canonical = canonical_url(url)
    if not canonical:
        kind, reason = "other", "유효한 웹 주소로 확인되지 않음"
    else:
        parsed = urlsplit(canonical)
        host, path = parsed.hostname or "", parsed.path.casefold()
        hints = f"{title}\n{text}".casefold()
        if _host_matches(host, _PAPER_HOSTS):
            kind, reason = "paper", "학술 논문·초록을 직접 가리키는 링크"
        elif host.endswith("nature.com") and ("/news" in path or "/articles/d41586-" in path):
            kind, reason = "news", "Nature 뉴스·해설 기사 경로"
        elif host.endswith("nature.com") and path.startswith("/articles/"):
            kind, reason = "paper", "Nature의 학술 논문 경로"
        elif _host_matches(host, _SOCIAL_HOSTS) or host == "youtube.com":
            kind, reason = "social", "소셜·커뮤니티 게시물 링크"
        elif _host_matches(host, _TOOL_HOSTS):
            kind, reason = "tool", "코드·모델·패키지 프로젝트 링크"
        elif (_host_matches(host, _BLOG_HOSTS) or host.endswith(".substack.com")
              or host.endswith(".github.io") or host == "alignmentforum.org"
              or "blog." in host or path.startswith("/blog/") or "/blog/" in path
              or (host in {"openai.com", "www.openai.com"} and path.startswith("/index/"))):
            kind, reason = "blog", "블로그 또는 해설 글 링크"
        elif _host_matches(host, _NEWS_HOSTS):
            kind, reason = "news", "확인된 언론·뉴스 매체의 기사 링크"
        elif _host_matches(host, _CURATED_NEWS_HOSTS):
            kind, reason = "news", "출처 도메인 기반 분류"
        elif host == "news.hada.io":
            kind, reason = "social", "출처 도메인 기반 분류"
        elif ("/doi/" in path
              or re.search(r"/(?:article|articles|paper|papers|publication)/", path)
              and re.search(r"논문|paper|study|research|journal|abstract", hints)):
            kind, reason = "paper", "학술 문서 경로와 원문 설명에 따른 분류"
        else:
            kind, reason = "other", "도메인과 경로만으로 문서 유형을 확정하기 어려움"
    return {
        "content_type": kind,
        "content_type_title": CONTENT_TYPE_TITLES[kind],
        "type_reason": reason,
    }


def _stable_hash(*parts: object, length: int) -> str:
    material = "\0".join(str(part or "") for part in parts)
    return sha256(material.encode("utf-8")).hexdigest()[:length]


def _row_key(row: dict) -> tuple[str, str, str]:
    return (str(row.get("chat_id") or ""), str(row.get("message_id") or ""),
            str(row.get("item_index") or 0))


def _mention(row: dict, canonical: str, matching_url: str) -> dict:
    chat_id, message_id, item_index = _row_key(row)
    return {
        "id": _stable_hash(canonical, chat_id, message_id, item_index, length=16),
        "chat_id": row.get("chat_id"),
        "message_id": row.get("message_id"),
        "item_index": row.get("item_index"),
        "day": str(row.get("day") or ""),
        "date_basis": str(row.get("date_basis") or ""),
        "telegram_day": str(row.get("telegram_day") or ""),
        "published_at": str(row.get("published_at") or ""),
        "channel": str(row.get("channel") or ""),
        "telegram_url": str(row.get("url") or ""),
        "title": str(row.get("title") or ""),
        "text": str(row.get("text") or ""),
        "excerpt": str(row.get("excerpt") or ""),
        "source_url": matching_url,
    }


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(value or "")
            if token.casefold() not in _GENERIC_TOKENS and len(token) > 1}


def _add_related(groups: list[dict]) -> None:
    token_sets = [_tokens(group["title"]) for group in groups]
    postings: dict[str, list[int]] = defaultdict(list)
    for index, tokens in enumerate(token_sets):
        for token in sorted(tokens):
            if len(postings[token]) < 50:
                postings[token].append(index)
    total = max(len(groups), 1)
    idf = {token: math.log((total + 1) / (len(indices) + 1)) + 1
           for token, indices in postings.items()}
    for index, group in enumerate(groups):
        candidates: set[int] = set()
        for token in sorted(token_sets[index], key=lambda item: (-idf.get(item, 0), item)):
            candidates.update(postings[token])
            if len(candidates) >= 100:
                break
        scored = []
        for other in sorted(candidates):
            if other == index or groups[other]["canonical_url"] == group["canonical_url"]:
                continue
            shared = token_sets[index] & token_sets[other]
            # One company/product word is too weak; require two meaningful terms.
            if len(shared) < 2:
                continue
            union = token_sets[index] | token_sets[other]
            numerator = sum(idf.get(token, 1) ** 2 for token in shared)
            denominator = sum(idf.get(token, 1) ** 2 for token in union) or 1
            score = numerator / denominator
            if score >= 0.28:
                scored.append((score, groups[other]["id"], other))
        group["related"] = [{
            "id": groups[other]["id"],
            "title": groups[other]["title"],
            "canonical_url": groups[other]["canonical_url"],
            "reason": "제목의 핵심 용어가 겹치는 관련 후보",
            "score": round(score, 3),
        } for score, _, other in sorted(scored, key=lambda item: (-item[0], item[1]))[:5]]


def build_link_groups(rows) -> list[dict]:
    """Group every link in joined article rows across dates, preserving evidence."""
    accumulated: dict[str, dict] = {}
    for original_row in rows:
        row = dict(original_row)
        links = extract_links(row.get("text") or "")
        source_url = str(row.get("source_url") or "")
        if source_url and _is_valid_http_url(_clean_url_tail(source_url.strip())):
            links.append(_clean_url_tail(source_url.strip()))
        # Count a source article at most once in each canonical group.
        per_row: dict[str, set[str]] = defaultdict(set)
        for link in links:
            canonical = canonical_url(link)
            if canonical:
                per_row[canonical].add(link)
        for canonical, variants in per_row.items():
            bucket = accumulated.setdefault(canonical, {"variants": set(), "rows": {}})
            bucket["variants"].update(variants)
            row_bucket = bucket["rows"].setdefault(
                _row_key(row), {"row": row, "variants": set()})
            row_bucket["variants"].update(variants)

    groups = []
    for canonical in sorted(accumulated):
        bucket = accumulated[canonical]
        row_buckets = sorted(bucket["rows"].values(), key=lambda item: (
            str(item["row"].get("day") or ""),
            str(item["row"].get("published_at") or ""), _row_key(item["row"])))
        source_rows = [item["row"] for item in row_buckets]
        mentions = []
        for item in row_buckets:
            row = item["row"]
            preferred = str(row.get("source_url") or "")
            matching_url = (preferred if canonical_url(preferred) == canonical
                            else sorted(item["variants"])[0])
            mentions.append(_mention(row, canonical, matching_url))
        dates = sorted({mention["day"] for mention in mentions if mention["day"]})
        title = next((mention["title"] for mention in mentions if mention["title"]), canonical)
        representative = max(source_rows, key=lambda row: (
            len(str(row.get("title") or "")), str(row.get("title") or "")))
        typing = classify_link(canonical, title, str(representative.get("text") or ""))
        topics = Counter(str(row.get("topic") or "general") for row in source_rows)
        topic = sorted(topics, key=lambda key: (-topics[key], key))[0] if topics else "general"
        group = {
            "id": _stable_hash(canonical, length=24),
            "canonical_url": canonical,
            "title": title,
            "domain": urlsplit(canonical).hostname or "",
            **typing,
            "topic": topic,
            "topic_title": TOPICS.get(topic, TOPICS["general"]),
            "occurrence_count": len(mentions),
            "distinct_days": len(dates),
            "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "",
            "dates": dates,
            "date_basis_counts": dict(sorted(Counter(
                mention["date_basis"] for mention in mentions if mention["date_basis"]
            ).items())),
            "variants": sorted(bucket["variants"]),
            "mentions": mentions,
            "related": [],
        }
        groups.append(group)
    _add_related(groups)
    # Stable sorts prioritize recurrence, then frequency and most recent evidence.
    groups.sort(key=lambda group: group["canonical_url"])
    groups.sort(key=lambda group: group["last_seen"], reverse=True)
    groups.sort(key=lambda group: group["occurrence_count"], reverse=True)
    groups.sort(key=lambda group: group["distinct_days"], reverse=True)
    return groups
