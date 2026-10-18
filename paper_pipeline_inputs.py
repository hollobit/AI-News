"""Exact input fingerprints for one paper scheduler, refreshed from the change log.

This is a scheduling hint, never an analysis approval cache. Submission still
reads the current paper and applies the existing input/review contracts.
"""
from dataclasses import dataclass

from context_paper_delta import TABLES, changes, position


@dataclass(frozen=True)
class PaperInput:
    paper_id: str
    metadata_status: str
    input_hash: str


def _position(db):
    # A journal without every source trigger cannot prove absence of changes.
    triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if any(f'paper_search_v1_{table}_{event}' not in triggers
           for table in TABLES for event in ('insert', 'update', 'delete')):
        return None
    return position(db)


def _inputs(db, identities=None):
    from arxiv_papers import _paper_rows
    from paper_analysis import analysis_input_hash
    return {item['paper_id']: PaperInput(item['paper_id'], item['metadata_status'], analysis_input_hash(item))
            for item in _paper_rows(db, identities)}


def _ordered(values):
    return tuple(values[key] for key in sorted(values, reverse=True))


class PaperInputIndex:
    def __init__(self):
        self.position = None
        self.inputs = {}
        self.ordered = ()

    def current(self, db):
        # An enclosing write can roll back without changing the committed log.
        # Never let tentative rows poison the next scheduler tick's cache.
        if db.in_transaction:
            return _ordered(_inputs(db))
        db.execute('BEGIN')
        try:
            current = _position(db)
            delta = changes(db, self.position, current)
            if delta is None:
                inputs = _inputs(db)
            elif delta[0]:
                identities = delta[1]
                inputs = {key: value for key, value in self.inputs.items() if key not in identities}
                inputs.update(_inputs(db, sorted(identities)))
            else:
                inputs = self.inputs
            # Position and rows come from the same SQLite read snapshot.
            db.commit()
        except Exception:
            db.rollback()
            raise
        ordered = self.ordered if inputs is self.inputs else _ordered(inputs)
        self.position, self.inputs, self.ordered = current, inputs, ordered
        return ordered
