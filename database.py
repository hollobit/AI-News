"""Explicit database preparation, separate from ordinary request connections."""
from pathlib import Path
import sqlite3
import threading
from keyword_index import init_keyword_index, ensure_keyword_index
from url_archive import init_archive, backfill_archive
_KEYWORD_SCHEMA_PATHS = set()
_KEYWORD_SCHEMA_LOCK = threading.Lock()


def open_db(path, *, read_only=False, timeout=15):
    filename=Path(path).resolve()
    db=sqlite3.connect(filename.as_uri()+'?mode=ro' if read_only else str(filename), uri=read_only, timeout=timeout)
    db.row_factory=sqlite3.Row
    return db


def prepare_database(path, rebuild_articles, joined_articles, classification_version):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = open_db(path)
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS news (
            chat_id TEXT, message_id INTEGER, channel TEXT, title TEXT,
            excerpt TEXT, text TEXT, url TEXT, published_at TEXT, day TEXT,
            version INTEGER, PRIMARY KEY(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS news_day ON news(day);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS articles (
            chat_id TEXT, message_id INTEGER, item_index INTEGER,
            title TEXT, excerpt TEXT, text TEXT, day TEXT, date_basis TEXT,
            topic TEXT, source_url TEXT, kind TEXT,
            PRIMARY KEY(chat_id, message_id, item_index)
        );
        CREATE INDEX IF NOT EXISTS articles_day_topic ON articles(day, topic);
    """)
    db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
    db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1,strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
    init_archive(db)
    database_key = str(Path(path).resolve())
    with _KEYWORD_SCHEMA_LOCK:
        if database_key not in _KEYWORD_SCHEMA_PATHS:
            init_keyword_index(db)
            db.commit()
            _KEYWORD_SCHEMA_PATHS.add(database_key)
    if not db.execute("SELECT 1 FROM url_archive_meta WHERE key='legacy_news_backfill_complete'").fetchone():
        with db:
            db.execute("BEGIN IMMEDIATE")
            backfill_archive(db)
    version = db.execute("SELECT value FROM state WHERE key='classification_version'").fetchone()
    if version is None or version[0] != classification_version:
        rebuild_articles(db)
    from source_changes import install
    install(db)
    db.commit()
    ensure_keyword_index(db, lambda: joined_articles(db))
    db.commit()
    return db
