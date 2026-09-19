"""Additive, transactionally versioned history migration; no network access."""
from importlib.resources import files
import sqlite3

SCHEMA_VERSION=1


def migrate(db):
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('BEGIN IMMEDIATE')
    try:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version>SCHEMA_VERSION: raise ValueError('Database schema is newer than this Core version')
        if version==SCHEMA_VERSION:
            db.commit(); return
        for statement in files('modrecon').joinpath('schema.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip(): db.execute(statement)
        for declaration in ('server_name TEXT','display_name TEXT','confirmation_collected_at TEXT',
                            'confirmation_observations INTEGER','legacy_backfill INTEGER NOT NULL DEFAULT 1','destination_key TEXT'):
            db.execute('ALTER TABLE change_events ADD COLUMN '+declaration)
        db.execute('ALTER TABLE change_items ADD COLUMN metadata_retrieved_at TEXT')
        db.execute('CREATE TABLE event_publications(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL REFERENCES change_events(id),schema_version INTEGER NOT NULL,published_at TEXT NOT NULL,event_json TEXT NOT NULL)')
        db.execute('CREATE TABLE webhook_outbox(event_id TEXT NOT NULL REFERENCES change_events(id),destination_key TEXT NOT NULL,payload_json TEXT,delivered_at TEXT,next_attempt REAL NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(event_id,destination_key))')
        db.execute("CREATE TRIGGER immutable_publication_update BEFORE UPDATE ON event_publications BEGIN SELECT RAISE(ABORT,'Published events are immutable'); END")
        db.execute("CREATE TRIGGER immutable_publication_delete BEFORE DELETE ON event_publications BEGIN SELECT RAISE(ABORT,'Published events cannot be deleted'); END")
        # Historical names and metadata retrieval timestamps were not captured.
        # Never manufacture them or fetch missing metadata while migrating.
        from .events import publish_stored
        for event in db.execute('SELECT * FROM change_events ORDER BY detected_at,id').fetchall():
            db.execute('UPDATE change_events SET destination_key=?,enrichment_done=1 WHERE id=?',('legacy:'+event['server_id'],event['id']))
            event=db.execute('SELECT * FROM change_events WHERE id=?',(event['id'],)).fetchone()
            publish_stored(db,event)
        db.execute('PRAGMA user_version=1')
        db.commit()
    except BaseException:
        db.rollback(); raise
