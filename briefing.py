"""Deterministic editorial briefing built only from stored Telegram evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import re

from classification import TOPICS
from link_groups import CONTENT_TYPE_TITLES, _tokens as link_title_tokens
from link_groups import canonical_url, extract_links


_SPACE = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9._+#-]+|\d+(?:\.\d+)?%?")
_CHANGE_SIGNALS = re.compile(
    r"발표|공개|출시|업데이트|개선|증가|감소|승인|규제|투자|인수|협력|"
    r"launch|release|announce|update|improv|increase|decrease|approve|acquir",
    re.IGNORECASE,
)
_CHANGE_TITLES = {"new": "새 소식", "updated": "설명 변화", "recurring": "재등장"}


def _clean(value) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _description(value) -> str:
    return _clean(_URL.sub("", str(value or ""))).strip(" -—·:|\n")


def _normalized_description(item: dict) -> str:
    # Compare the complete stored article fragment. Whitespace and URL rendering are
    # cosmetic; every other wording change remains visible as an update signal.
    value = _description(item.get("text") or item.get("excerpt") or item.get("title"))
    return value.casefold()


def _item_urls(item: dict) -> list[str]:
    values = []
    source = str(item.get("source_url") or "").strip()
    if source:
        values.append(source)
    values.extend(item.get("embedded_urls") or [])
    values.extend(extract_links(item.get("text") or ""))
    result, seen = [], set()
    for value in values:
        canonical = canonical_url(value)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def _analysis_summary(group: dict) -> str:
    analysis = group.get("analysis")
    if not isinstance(analysis, dict):
        return ""
    result = analysis.get("result") if isinstance(analysis.get("result"), dict) else analysis
    if analysis.get("status") not in {None, "complete"} or not isinstance(result, dict):
        return ""
    return _description(result.get("summary"))


def _summary(item: dict, group: dict | None) -> str:
    analyzed = _analysis_summary(group or {})
    if analyzed:
        return analyzed[:420]
    value = _description(item.get("excerpt"))
    if not value or value == _description(item.get("title")):
        value = _description(item.get("text"))
    if not value:
        value = _clean(item.get("title"))
    return value[:420] + ("…" if len(value) > 420 else "")


def _mention_description(mention: dict) -> str:
    return _description(mention.get("text") or mention.get("excerpt") or mention.get("title")).casefold()


def _change_type(item: dict, group: dict | None, date: str) -> str:
    if not group:
        return "new"
    previous = [m for m in group.get("mentions", []) if str(m.get("day") or "") < date]
    if not previous:
        return "new"
    current = _normalized_description(item)
    previous_texts = {_mention_description(mention) for mention in previous}
    return "recurring" if current and current in previous_texts else "updated"


def _timeline_dates(group: dict | None, date: str, limit: int = 6) -> dict:
    dates = list(dict.fromkeys(str(day) for day in (group or {}).get("dates", []) if day))
    if date and date not in dates:
        dates.append(date)
        dates.sort()
    total = len(dates)
    if total > limit:
        # Retain both ends, plus dates nearest the selected day.
        selected_index = dates.index(date) if date in dates else total - 1
        indexed = sorted(range(total), key=lambda index: (abs(index - selected_index), index))
        keep = {0, total - 1}
        for index in indexed:
            if len(keep) >= limit:
                break
            keep.add(index)
        visible = [day for index, day in enumerate(dates) if index in keep]
    else:
        visible = dates
    return {"dates": visible, "total": total, "hidden_count": max(0, total - len(visible))}


def _related(group: dict | None, groups_by_id: dict[str, dict], title: str) -> list[dict]:
    if not group:
        return []
    related = []
    source_id = str(group.get("id") or "")
    source_url = canonical_url(group.get("canonical_url") or "")
    source_title = _clean(title or group.get("title"))
    source_tokens = link_title_tokens(source_title)
    source_title = source_title.casefold()
    seen = set()
    for candidate in group.get("related", []):
        other = groups_by_id.get(str(candidate.get("id") or ""), {})
        candidate_id = str(candidate.get("id") or other.get("id") or "")
        candidate_url = canonical_url(
            candidate.get("canonical_url") or other.get("canonical_url") or "")
        candidate_title = _clean(candidate.get("title") or other.get("title"))
        # Same-link records belong to dates/timeline. Identical titles on another
        # URL are usually mirrors or duplicate posts, rather than another story.
        if (not candidate_id or candidate_id == source_id or not candidate_url
                or candidate_url == source_url or candidate_title.casefold() == source_title):
            continue
        if candidate_id in seen:
            continue
        shared = sorted(source_tokens & link_title_tokens(candidate_title))
        if len(shared) < 2:
            continue
        seen.add(candidate_id)
        other_date = str(other.get("last_seen") or "")
        related.append({
            "date": other_date,
            "title": candidate_title,
            "group_id": candidate_id,
            "source_url": candidate_url,
            "relation_type": "shared_keywords",
            "shared_keywords": shared,
            "relationship_label": "공통 키워드 기반 관련 뉴스 · 인과관계 미확인",
        })
        if len(related) >= 6:
            break
    return related


def _editorial_score(story: dict) -> tuple[int, int, str]:
    summary = story["summary"]
    tokens = _TOKEN.findall(summary)
    score = 0
    if story["source_url"]:
        score += 2
    if len(summary) >= 70:
        score += 3
    elif len(summary) >= 30:
        score += 2
    elif len(summary) >= 12:
        score += 1
    if len(tokens) >= 12:
        score += 1
    if re.search(r"\d", summary):
        score += 1
    if _CHANGE_SIGNALS.search(f"{story['title']} {summary}"):
        score += 2
    if story["change_type"] == "new":
        score += 3
    elif story["change_type"] == "updated":
        score += 2
    # Recurrence count is deliberately absent: frequency is context, not importance.
    return score, len(summary), story["id"]


def _why_selected(story: dict) -> str:
    reasons = {
        "new": "해당 날짜에 처음 포착",
        "updated": "같은 링크의 설명이 이전 기록과 달라짐",
        "recurring": "이전에 다룬 링크가 다시 등장",
    }
    parts = [reasons[story["change_type"]]]
    if len(story["summary"]) >= 70:
        parts.append("구체적인 설명 포함")
    if re.search(r"\d", story["summary"]):
        parts.append("수치·버전 정보 포함")
    if _CHANGE_SIGNALS.search(f"{story['title']} {story['summary']}"):
        parts.append("발표·변화 신호 포함")
    if story["source_url"]:
        parts.append("원문 링크 포함")
    return " · ".join(parts)


def _make_story(
        item: dict, group: dict | None, groups_by_id: dict[str, dict], date: str) -> dict:
    if date == "all":
        date = str(item.get("day") or "")
    urls = _item_urls(item)
    source_url = (item.get("original_url") or (group or {}).get("original_url") or (group or {}).get("canonical_url")
                  or (urls[0] if urls else str(item.get("source_url") or "")))
    sources = item.get("sources") or []
    telegram_url = str(item.get("url") or "")
    if not telegram_url and sources:
        telegram_url = str(sources[0].get("url") or "")
    group_id = str((group or {}).get("id") or "")
    fingerprint = "\0".join((date, _clean(item.get("title")), _normalized_description(item)))
    item_id = group_id or sha256(fingerprint.encode()).hexdigest()[:24]
    content_type = str((group or {}).get("content_type") or item.get("content_type") or "other")
    topic = str(item.get("topic") or (group or {}).get("topic") or "general")
    change = _change_type(item, group, date)
    story = {
        "id": item_id,
        "title": _clean(item.get("title")) or _clean((group or {}).get("title")) or "제목 없는 소식",
        "summary": _summary(item, group),
        **{key:item[key] for key in ('briefing_title','source_title','title_origin','original_url') if key in item},
        "why_selected": "",
        "topic": topic,
        "topic_title": str(item.get("topic_title") or (group or {}).get("topic_title")
                           or TOPICS.get(topic, TOPICS["general"])),
        "content_type": content_type,
        "content_type_title": str(
            (group or {}).get("content_type_title") or item.get("content_type_title")
            or CONTENT_TYPE_TITLES.get(content_type, CONTENT_TYPE_TITLES["other"])),
        "keywords": list(item.get("keywords") or []),
        "keyword_record_id": str(item.get("keyword_record_id") or ""),
        "word_count": int(item.get("word_count") or 0),
        "source_context": item.get('source_context') or {},
        "source_url": source_url,
        "telegram_url": telegram_url,
        "group_id": group_id,
        "first_seen": str((group or {}).get("first_seen") or date),
        "last_seen": str((group or {}).get("last_seen") or date),
        "dates": list((group or {}).get("dates") or [date]),
        "change_type": change,
        "change_title": _CHANGE_TITLES[change],
        "related": _related(group, groups_by_id, _clean(item.get("title"))),
        "timeline": _timeline_dates(group, date),
    }
    story["why_selected"] = _why_selected(story)
    return story


def _select_editorial(stories: list[dict], maximum: int) -> list[dict]:
    ranked = sorted(stories, key=_editorial_score, reverse=True)
    if not ranked or maximum <= 0:
        return []
    selected = [ranked[0]]
    selected_ids = {ranked[0]["id"]}
    selected_topics = {ranked[0]["topic"]}
    # Give each available topic one representative before adding a second story.
    for story in ranked[1:]:
        if len(selected) >= maximum:
            break
        if story["topic"] not in selected_topics:
            selected.append(story)
            selected_ids.add(story["id"])
            selected_topics.add(story["topic"])
    for story in ranked[1:]:
        if len(selected) >= maximum:
            break
        if story["id"] not in selected_ids:
            selected.append(story)
            selected_ids.add(story["id"])
    return selected


def build_briefing(news_payload: dict, link_groups: list[dict], params: dict | None = None) -> dict:
    """Build an explainable daily briefing without network calls or inferred facts."""
    params = params or {}
    date = str(params.get("date") or news_payload.get("date") or "")
    if date == "all":
        date = str(news_payload.get("date") or "all")
    try:
        highlight_limit = max(0, min(12, int(params.get("highlights", 6))))
    except (TypeError, ValueError):
        highlight_limit = 6

    groups_by_id = {str(group.get("id") or ""): group for group in link_groups}
    groups_by_url = {str(group.get("canonical_url") or ""): group for group in link_groups}
    stories_by_key = {}
    for item in news_payload.get("items", []):
        urls = _item_urls(item)
        group = next((groups_by_url[url] for url in urls if url in groups_by_url), None)
        story_date = str(item.get("day") or "") if date == "all" else date
        story = _make_story(item, group, groups_by_id, story_date)
        # Consolidate same-link copies on the selected day while keeping changed history in the group.
        key = story["group_id"] or (story["title"].casefold(), _normalized_description(item))
        previous = stories_by_key.get(key)
        if previous is None or (_editorial_score(story), story["title"], story["summary"]) > (
                _editorial_score(previous), previous["title"], previous["summary"]):
            stories_by_key[key] = story
    stories = list(stories_by_key.values())
    stories.sort(key=_editorial_score, reverse=True)

    editorial = _select_editorial(stories, min(len(stories), highlight_limit + 1))
    lead = editorial[0] if editorial else None
    highlights = editorial[1:] if editorial else []
    selected_ids = {story["id"] for story in editorial}

    topic_groups = defaultdict(list)
    for story in stories:
        topic_groups[story["topic"]].append(story)
    topics = []
    for topic, topic_stories in topic_groups.items():
        topics.append({
            "id": topic,
            "title": TOPICS.get(topic, TOPICS["general"]),
            "count": len(topic_stories),
            "selected_count": sum(story["id"] in selected_ids for story in topic_stories),
            "stories": topic_stories,
        })
    topics.sort(key=lambda value: (-value["selected_count"], -value["count"], value["title"]))
    changes = Counter(story["change_type"] for story in stories)
    return {
        "date": date,
        "stats": {
            "available_count": len(stories),
            "selected_count": len(editorial),
            "unique_links": len({story["source_url"] for story in stories if story["source_url"]}),
            "topic_count": len(topic_groups),
            "new_count": changes["new"],
            "updated_count": changes["updated"],
            "recurring_count": changes["recurring"],
        },
        "lead": lead,
        "highlights": highlights,
        "topics": topics,
        "timeline": stories,
    }
