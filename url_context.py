"""Select source-adjacent Telegram text without borrowing sibling-news prose."""
import hashlib
import re
from datetime import date

from link_groups import canonical_url, extract_links


def _focused_article_date(text, context_day=None):
    """Read an explicit date label on this story's heading, never its briefing."""
    heading=next((line.strip() for line in text.splitlines() if line.strip()), '')
    clean=heading.strip(' *#-•■🔹\t')
    match=re.match(r'^\[?(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:\]|\s|$)',clean)
    if not match and not re.search(r'브리핑|뉴스|동향|briefing|newsletter',clean,re.I):
        match=re.search(r'\|\s*(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s*$',clean)
    if not match:
        short=re.search(r'\((\d{1,2})[-/](\d{1,2})\)\s*$',clean)
        if not short:return None
        try:
            context=date.fromisoformat(str(context_day))
            month,day=map(int,short.groups())
            parsed=date(context.year,month,day)
            if (parsed-context).days>183:parsed=date(context.year-1,month,day)
            elif (parsed-context).days < -183:parsed=date(context.year+1,month,day)
            return parsed.isoformat(),heading
        except (ValueError,TypeError):return None
    try:return date(*map(int,match.groups())).isoformat(),heading
    except ValueError:return None


def focus_url_context(item, target_url, limit=1100):
    """Return a derived item; the original Telegram/archive row is never edited.

    One parsed article with one URL stays intact. Mixed briefings use only the
    target paragraph, or the lines immediately before its link. Ambiguous shared
    link lines retain only the link rather than assigning another article's text.
    """
    target = canonical_url(target_url)
    text = str(item.get('text') or '')
    urls = {canonical_url(url) for url in extract_links(text)}
    result = dict(item, source_url=target)
    if not target:
        return result
    primary = canonical_url(item.get('source_url') or '')
    if urls <= {target} and (target in urls or primary == target):
        result['url_context'] = {'method': 'single_article', 'start': 0, 'end': len(text),
                                 'target_url': target, 'original_length': len(text), 'truncated': False, 'verbatim': True}
        return result
    paragraphs = list(re.finditer(r'\S[\s\S]*?(?=\n[ \t]*\n|\Z)', text))
    start = end = 0
    method = 'unresolved_link'
    focused = target
    for index, paragraph in enumerate(paragraphs):
        value = paragraph.group()
        local_urls = {canonical_url(url) for url in extract_links(value)}
        if target not in local_urls:
            continue
        if local_urls == {target}:
            start, end = paragraph.span()
            non_url = value
            for url in extract_links(value):
                non_url = non_url.replace(url, '')
            # Link-only paragraphs attach at most one adjacent, URL-free block.
            if not re.search(r'[가-힣A-Za-z0-9]{3}', non_url) and index:
                previous = paragraphs[index-1]
                if not extract_links(previous.group()) and not re.match(r'^(?:📡|[-=━_]{3}|#{1,3}\s)', previous.group()):
                    start = previous.start()
            focused, method = text[start:end], 'url_paragraph'
        else:
            offset, boundary = paragraph.start(), paragraph.start()
            for line in value.splitlines(keepends=True):
                line_urls = {canonical_url(url) for url in extract_links(line)}
                if target in line_urls:
                    if line_urls == {target}:
                        start, end = boundary, offset+len(line)
                        focused, method = text[start:end], 'url_line_block'
                    else:
                        # A markdown label explicitly bound to this URL is safe.
                        label = next((m[1] for m in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+)\)', line)
                                      if canonical_url(m[2]) == target), '')
                        focused = (label+'\n' if label else '')+target
                        start, end, method = offset, offset+len(line), 'shared_link_line'
                    break
                if line_urls:
                    boundary = offset+len(line)
                offset += len(line)
        break
    # A very long preceding block is trimmed from the link end, not the start
    # of the whole briefing. This cannot cross an earlier foreign URL boundary.
    truncated = len(focused) > limit
    if truncated:
        focused = focused[-limit:]
        newline = focused.find('\n')
        if newline >= 0 and len(focused)-newline > 150:
            focused = focused[newline+1:]
        elif newline < 0 and re.search(r'\s', focused):
            focused = re.split(r'\s', focused, maxsplit=1)[1]
        start = max(start, end-len(focused))
    verbatim = method in {'url_paragraph', 'url_line_block'}
    if verbatim:
        start += len(focused)-len(focused.lstrip())
    focused = focused.strip()
    if verbatim:
        end = start+len(focused)
    lines = [line.strip(' *#-•\t') for line in focused.splitlines() if line.strip() and not extract_links(line)]
    title = lines[0][:160] if lines else '원문 링크 · '+target[:130]
    result.update(title=title, text=focused, excerpt=focused[:240], summary='', description='',
                  original_title=str(item.get('title') or ''),
                  url_context={'method': method, 'start': start, 'end': end, 'target_url': target,
                               'original_length': len(text), 'truncated': truncated,
                               'verbatim': verbatim,
                               'original_text_hash': hashlib.sha256(text.encode()).hexdigest()})
    article_date=_focused_article_date(focused,item.get('day')) if verbatim else None
    if article_date:
        result.update(original_day=item.get('day'),original_date_basis=item.get('date_basis'),
                      day=article_date[0],date_basis='article')
        result['url_context'].update(article_date_quote=article_date[1],article_date_source='focused_heading_label',
                                     article_date_year_inferred=not bool(re.search(r'\d{4}[-./]\d{1,2}[-./]\d{1,2}',article_date[1])))
    return result
