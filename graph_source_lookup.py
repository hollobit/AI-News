"""Read selected current originals lazily within the caller's SQLite snapshot."""
from collections.abc import Mapping
from functools import lru_cache
import hashlib

from link_groups import canonical_url
from task_lifecycle import checkpoint


def _url_index(rows):
    urls={}
    for raw in rows:
        checkpoint()
        urls[canonical_url(raw)]=raw
    external={'reach:'+hashlib.sha256(url.encode()).hexdigest()[:24]:(index,url)
              for index,url in enumerate(urls)}
    return urls,external


_cached_url_index=lru_cache(maxsize=2)(_url_index)


class CurrentSources(Mapping):
    def __init__(self, db, decode):
        self.db=db
        self.decode=decode
        self.loaded={}
        # Preserve the full reader's table traversal and canonical collision
        # precedence without decoding every unrelated original body.
        rows=[]
        for row in db.execute('SELECT canonical_url FROM source_excerpts NOT INDEXED'):
            checkpoint()
            rows.append(row[0])
        # Reuse only URL normalization/IDs, keyed by the exact ordered URL list.
        # Bodies and status are always read anew in this caller's transaction.
        build=_cached_url_index if len(rows)<=20000 and sum(map(len,rows))<=4000000 else _url_index
        self.urls,self.external=build(tuple(rows))

    def selected_external(self, identities):
        return [url for _,url in sorted(self.external[identity] for identity in identities if identity in self.external)]

    def __len__(self):return len(self.urls)

    def __iter__(self):return iter(self.urls)

    def __getitem__(self, url):
        checkpoint()
        if url not in self.loaded:
            raw=self.urls[url]
            row=self.db.execute('SELECT result_json FROM source_excerpts WHERE canonical_url=?',(raw,)).fetchone()
            self.loaded[url]=(self.decode(row[0]) or {}) if row else {}
        return self.loaded[url]
