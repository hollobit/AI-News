"""Deterministic parsing and topic classification for Hermes news messages."""

from collections import OrderedDict
from datetime import date, datetime
import re
from urllib.parse import urlparse


TOPICS = OrderedDict([
    ("agents", "에이전트·개발"),
    ("models", "AI 모델"),
    ("robotics", "로봇·자율주행"),
    ("medical", "의료·바이오"),
    ("hardware", "반도체·인프라"),
    ("policy", "정책·보안"),
    ("business", "기업·산업"),
    ("research", "연구·논문"),
    ("general", "기타 뉴스"),
])

_FULL_DATE = re.compile(r"(?<!\d)(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_SHORT_DATE = re.compile(r"(?<![\d/])(\d{1,2})[-/](\d{1,2})(?![\d/])")
_ITEM_START = re.compile(r"^\s*(?:[-•]\s+|\*\s+|\d+[.)]\s+)")
_URL = re.compile(r"https?://[^\s<>\])}]+", re.IGNORECASE)
_MARKDOWN_URL = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_HEADER_WORDS = re.compile(
    r"뉴스|브리핑|동향|news|briefing|safety|robotics|physical\s+ai",
    re.IGNORECASE,
)
_BOILERPLATE = re.compile(
    r"^\s*(?:"
    r"cronjob\s+response\s*:|"
    r"\(?job_?id\s*:|"
    r"[-=]{5,}\s*$|"
    r"(?:📧|✉️)?\s*이메일(?:도)?\s+.*(?:발송|전송).*완료|"
    r"이메일\s+발송\s+완료(?:되었습니다|했습니다)?|"
    r"to\s+stop\s+or\s+manage\s+this\s+job"
    r")",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"^\s*(?:---+|━━+|___+)\s*$")
_ARTICLE_BOUNDARY = re.compile(
    r"^(?:"
    r"📡|📌|"
    r"(?:오늘|이번\s*(?:주|주말))\s+핵심\s*(?:테마|키워드|요약)|"
    r"형님[,\s]+(?:오늘|이번\s*(?:주|주말)).{0,30}핵심|"
    r"(?:arxiv|hacker\s+news|twitter/?x|reddit)\s*$"
    r")",
    re.IGNORECASE,
)

_TOPIC_PATTERNS = {
    "medical": (
        r"의료|의학|바이오|신약|임상|환자|병원|질병|치료|진단|헬스케어|"
        r"\bmedical\b|\bmedicine\b|\bbiotech\b|\bclinical\b|\bdrug\b|\bhealthcare\b",
    ),
    "robotics": (
        r"로봇|자율주행|로보택시|휴머노이드|구체지능|피지컬\s*ai|드론|"
        r"\brobot(?:ics)?\b|\bhumanoid\b|\bautonomous\s+(?:vehicle|driving)\b|"
        r"\bself-driving\b|\bvla\b|sim-to-real",
    ),
    "hardware": (
        r"반도체|메모리\s*칩|칩셋|gpu|npu|hbm|데이터\s*센터|클라우드\s*인프라|"
        r"컴퓨팅\s*인프라|서버|파운드리|\bsemiconductor\b|\bchip(?:s|set)?\b|"
        r"\bdatacenter\b|\bdata\s+center\b",
    ),
    "policy": (
        r"규제|법안|법률|정책|정부|수출\s*통제|제재|저작권|소송|보안|취약점|"
        r"프롬프트\s*인젝션|랜섬웨어|사이버|안전성|"
        r"\bregulat(?:ion|ory)\b|\bpolicy\b|\blaw\b|\bsecurity\b|"
        r"\bvulnerabilit(?:y|ies)\b|\bransomware\b|\bcopyright\b|\bsafety\b",
    ),
    "agents": (
        r"에이전트|에이전틱|코딩\s*도구|코딩\s*어시스턴트|mcp|오케스트레이터|"
        r"워크플로우|바이브\s*코딩|\bagents?\b|\bagentic\b|\bcoding\s+assistant\b|"
        r"\borchestrat(?:or|ion)\b|\bworkflow\b",
    ),
    "research": (
        r"논문|연구진|연구\s*결과|학술|벤치마크|arxiv|과학\s*발견|"
        r"\bpaper\b|\bresearch(?:er|ers)?\b|\bstudy\b|\bbenchmark\b",
    ),
    "business": (
        r"기업|산업|스타트업|투자|펀딩|인수|합병|상장|ipo|매출|시장|사업부|"
        r"\bstartup\b|\bfunding\b|\bacquisition\b|\brevenue\b|\bmarket\b",
    ),
    "models": (
        r"인공지능|생성형\s*ai|언어\s*모델|멀티모달|파운데이션\s*모델|"
        r"딥시크|클로드|챗gpt|오픈ai|앤트로픽|제미나이|llm|"
        r"(?<![a-z])ai(?![a-z])|\bmodel(?:s)?\b|\bopenai\b|\banthropic\b|"
        r"\bdeepseek\b|\bchatgpt\b|\bgemini\b|\bclaude\b",
    ),
}


def _valid_date(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def _telegram_day(row):
    value = str(row.get("day") or "")[:10]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    parsed = _valid_date(*match.groups()) if match else None
    if parsed:
        return parsed
    published = str(row.get("published_at") or "")
    try:
        return datetime.fromisoformat(published).date()
    except ValueError:
        return None


def _header_date(lines):
    """Find a dated Hermes heading, without treating the first article as a heading."""
    for index, line in enumerate(lines[:16]):
        if _ITEM_START.match(line):
            break
        plain = _plain(line)
        if _safe_urls(line) or not _HEADER_WORDS.search(plain):
            continue
        match = _FULL_DATE.search(plain)
        header_shape = (
            "📰" in line
            or bool(re.search(r"브리핑|동향|briefing", plain, re.IGNORECASE))
            or bool(re.search(r"\([^)]*\d{4}[-./]\d{1,2}[-./]\d{1,2}|"
                              r"\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}", plain))
        )
        if match and header_shape:
            parsed = _valid_date(*match.groups())
            if parsed:
                return parsed
        # A heading buried after article prose is a section, not the message date.
        if index > 8:
            break
    return None


def _context_year(text, header):
    if header:
        return header.year
    years = set()
    for line in text.splitlines():
        # Publication dates embedded in source URLs are not date context.
        without_urls = _URL.sub("", line)
        without_urls = re.sub(r"\[[^\]]*\]\(https?://[^)]+\)", "", without_urls)
        for match in _FULL_DATE.finditer(without_urls):
            if _valid_date(*match.groups()):
                years.add(int(match.group(1)))
    return years.pop() if len(years) == 1 else None


def _item_date(first_line, context_year, header):
    line = _ITEM_START.sub("", first_line, count=1)
    # A date must be a leading label, not a model version or a date inside a title.
    prefix = _plain(line[:48])
    prefix = re.sub(r"^⭐\s*\d+\s*\|\s*", "", prefix)
    prefix = re.sub(r"^\[(?!\d)[^\]]+\]\s*", "", prefix)
    prefix = prefix.lstrip("[")
    full = _FULL_DATE.match(prefix)
    if full:
        return _valid_date(*full.groups())
    short = _SHORT_DATE.match(prefix)
    if not short or context_year is None:
        return None
    month, day = map(int, short.groups())
    year = context_year
    parsed = _valid_date(year, month, day)
    if not parsed:
        return None
    if header:
        delta = (parsed - header).days
        if delta > 183:
            parsed = _valid_date(year - 1, month, day)
        elif delta < -183:
            parsed = _valid_date(year + 1, month, day)
    return parsed


def _safe_urls(text):
    candidates = _MARKDOWN_URL.findall(text) + _URL.findall(text)
    urls = []
    for candidate in candidates:
        candidate = candidate.rstrip(".,;:!?'\"")
        parsed = urlparse(candidate)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            if candidate not in urls:
                urls.append(candidate)
    return urls


def _plain(text):
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_lines(lines):
    cleaned = []
    for line in lines:
        if _BOILERPLATE.match(_plain(line)) or _SEPARATOR.match(line):
            continue
        cleaned.append(line.rstrip())
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return cleaned


def _article_lines(lines):
    """Keep one article and its sources, stopping before the next report section."""
    if not lines:
        return []
    article = [lines[0]]
    for line in lines[1:]:
        plain = _plain(line)
        if (_SEPARATOR.match(line) or _BOILERPLATE.match(plain)
                or line.lstrip().startswith("#") or _ARTICLE_BOUNDARY.match(plain)):
            break
        article.append(line)
    return _clean_lines(article)


def _title_and_excerpt(lines):
    first = _plain(_ITEM_START.sub("", lines[0], count=1)) if lines else ""
    first = re.sub(r"^(?:⭐\s*\d+\s*\|\s*)", "", first)
    first = re.sub(r"^\[?\d{4}[-./]\d{1,2}[-./]\d{1,2}\]?\s*", "", first)
    first = re.sub(r"^\[?\d{1,2}[-/]\d{1,2}\]?\s*", "", first)
    first = re.sub(r"^\[([^\]]+)\]\s*", r"\1: ", first)
    parts = re.split(r"\s+[—–]\s+", first, maxsplit=1)
    title = parts[0].strip(" -|:[]")
    excerpt_parts = ([parts[1]] if len(parts) == 2 else []) + [
        _plain(line) for line in lines[1:] if _plain(line) and not _safe_urls(line)
    ]
    excerpt = re.sub(r"\s+", " ", " ".join(excerpt_parts)).strip()
    if not title:
        title = excerpt[:120] or "제목 없는 뉴스"
    return title[:240], excerpt[:500]


def _credible_undated_item(first_line):
    """Recognize prose news bullets while rejecting numbered recap lists."""
    if re.match(r"^\s*\d+[.)]\s+", first_line):
        return False
    plain = _plain(_ITEM_START.sub("", first_line, count=1))
    parts = re.split(r"\s+[—–]\s+", plain, maxsplit=1)
    return len(parts) == 2 and len(parts[0].strip()) >= 6 and len(parts[1].strip()) >= 6


def _topic(text):
    lowered = text.lower()
    # In Hermes feeds, an MCP server is an agent integration, not server hardware.
    if re.search(r"\bmcp\b.{0,40}\bserver\b|\bserver\b.{0,40}\bmcp\b|mcp\s*서버", lowered):
        return "agents"
    for topic, patterns in _TOPIC_PATTERNS.items():
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            return topic
    return "general"


def _entry(lines, day, date_basis, kind):
    cleaned = _clean_lines(lines)
    text = "\n".join(cleaned).strip()
    title, excerpt = _title_and_excerpt(cleaned)
    urls = _safe_urls(text)
    return {
        "title": title,
        "excerpt": excerpt,
        "text": text,
        "day": day.isoformat() if day else "",
        "date_basis": date_basis,
        "topic": _topic(f"{title}\n{excerpt}"),
        "source_url": urls[0] if urls else "",
        "kind": kind,
    }


def classify_message(row: dict) -> list[dict]:
    """Split one stored Telegram row into dated, topic-classified news entries."""
    body = str(row.get("text") or "").strip()
    if not body:
        return []
    lines = body.splitlines()
    header = _header_date(lines)
    telegram = _telegram_day(row)
    context_year = _context_year(body, header)

    starts = [index for index, line in enumerate(lines) if _ITEM_START.match(line)]
    articles = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = _article_lines(lines[start:end])
        if not block:
            continue
        explicit = _item_date(block[0], context_year, header)
        urls = _safe_urls("\n".join(block))
        # Linkless numbered recap bullets are commentary, not duplicate articles.
        if not explicit and not urls and not _credible_undated_item(block[0]):
            continue
        day = explicit or header or telegram
        basis = "article" if explicit else ("briefing" if header else "telegram")
        articles.append(_entry(block, day, basis, "article"))

    if articles:
        return articles

    cleaned = _clean_lines(lines)
    if not cleaned:
        # The raw message consisted only of management metadata and has no news.
        return []
    day = header or telegram
    basis = "briefing" if header else "telegram"
    return [_entry(cleaned, day, basis, "briefing")]
