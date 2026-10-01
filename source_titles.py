"""Display source-page titles without changing briefing evidence or analysis inputs."""
import re
from link_groups import canonical_url
from source_enrichment import source_quality_error

_SHELL = re.compile(r'^(?:just a moment|access denied|forbidden|not found|404\b|403\b|로그인|접근 차단|robot check|attention required)', re.I)


def title_projection(item):
    briefing = str(item.get('title') or '제목 없음')
    source = item.get('source_context') or {}
    title = re.sub(r'\s+', ' ', str(source.get('title') or '')).strip()
    target = canonical_url(item.get('source_url') or '')
    retrieved = (bool(target) and source.get('status') == 'fetched'
                 and canonical_url(source.get('url') or '') == target
                 and bool(title) and not source.get('error')
                 and not _SHELL.search(title) and not source_quality_error(source))
    return dict(title=title if retrieved else briefing, briefing_title=briefing,
                source_title=title if retrieved else '',
                title_origin='source_page' if retrieved else 'telegram_briefing')
