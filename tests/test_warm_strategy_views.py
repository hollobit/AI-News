from pathlib import Path
import sqlite3


def test_warm_strategy_views_resolves_database_path_before_connecting():
    source = Path('app.py').read_text()
    assert 'db_path = str(Path(path).resolve())' in source
    assert 'sqlite3.connect(db_path, timeout=15)' in source
    assert 'if db is not None:' in source
