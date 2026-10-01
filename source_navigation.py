"""Keep navigable originals separate from normalized document identities."""
from link_groups import canonical_url


def original_url(db,url):
    value=str(url or '').strip()
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='repaired_source_links'").fetchone():
        repaired=[r[0] for r in db.execute('SELECT new_url FROM repaired_source_links WHERE old_url=?',(value,))]
        if len(repaired)==1:return repaired[0]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='archived_urls'").fetchone():return value
    # If the caller already supplied an observed original, preserve that exact
    # spelling, parameter order, signature and fragment.
    exact=db.execute('SELECT 1 FROM archived_urls WHERE original_url=? AND active=1 LIMIT 1',(value,)).fetchone()
    if exact:return value
    key=canonical_url(value)
    candidates=db.execute('SELECT original_url FROM archived_urls WHERE canonical_url=? AND active=1 ORDER BY published_at DESC,occurrence',(key,))
    return next((r[0] for r in candidates if canonical_url(r[0])==key),value)
