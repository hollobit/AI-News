"""Validate local public asset dependencies before switching a generation."""
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit
from public_site import PUBLIC_FILES as FILES

class _AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths=[]

    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag=='script' and values.get('src'):
            self.paths.append(values['src'])
        elif tag=='link' and 'stylesheet' in values.get('rel','').split() and values.get('href'):
            self.paths.append(values['href'])


def validate_static_dependencies(output, files=FILES):
    """Refuse to publish HTML or modules whose local assets are absent from the manifest."""
    root=Path(output)
    listed=set(files)
    for name in files:
        if name.endswith('.html'):
            parser=_AssetLinks()
            parser.feed((root/name).read_text())
            references=parser.paths
        elif name.endswith('.js'):
            references=re.findall(r'\b(?:from\s*|import\s*)[\'\"]\./([^\'\"]+)[\'\"]', (root/name).read_text())
        else:
            continue
        for reference in references:
            uri=urlsplit(reference)
            if uri.scheme or uri.netloc or uri.path.startswith('/'):
                continue
            dependency=uri.path.removeprefix('./')
            if dependency and (dependency not in listed or not (root/dependency).is_file()):
                raise RuntimeError(f'{name}: missing published asset {dependency}')

