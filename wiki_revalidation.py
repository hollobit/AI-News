"""Reuse an exact reviewed edition only after current-source admission succeeds."""
import json


def revalidate_latest(db, topic, bundle, policy_hash):
    """Caller holds BEGIN IMMEDIATE and has excluded live owners/manual retries.

    Keep the stale revision and attempt ledger intact. A new edition records
    revalidation, rather than pretending another model review took place.
    """
    from bulk_baseline import digest, js
    from knowledge_wiki import now, verified
    from wiki_registry import matches_config
    from wiki_sources import current
    from wiki_structure import purpose

    revision = db.execute('SELECT * FROM wiki_revisions WHERE topic=? ORDER BY id DESC LIMIT 1',
                          (topic,)).fetchone()
    if not revision or revision['status'] != 'stale' or revision['input_hash'] != bundle['input_hash']:
        return False
    try:
        result = json.loads(revision['result_json'])
        if (result.get('bundle') != bundle or result.get('purpose_hash') != policy_hash
                or not verified(result)):
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    if (digest(purpose()) != policy_hash or not matches_config(db, bundle)
            or not current(db, bundle['evidence'])):
        return False
    changes = json.loads(revision['changes_json'])
    changes['revalidated_revision'] = revision['id']
    stamp = now()
    new_id = db.execute('''INSERT INTO wiki_revisions(topic,input_hash,status,result_json,created_at,changes_json)
        VALUES (?,?,'complete',?,?,?)''',
        (topic, bundle['input_hash'], revision['result_json'], stamp, js(changes))).lastrowid
    db.execute('''UPDATE wiki_topics SET status='complete',published_revision=?,updated_at=?,error='',
        owner_pid=NULL WHERE id=?''', (new_id, stamp, topic))
    db.execute('INSERT INTO wiki_setting_events(kind,detail_json) VALUES (?,?)',
               ('revalidation', js(dict(topic=topic, source_revision=revision['id'], revision=new_id,
                                       method='current_evidence_review_reuse', model_calls=0))))
    return True
