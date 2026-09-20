"""Verified offline backups; never initialize or alter monitoring state."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .engine import InstanceLock


def _copy_database(source, destination):
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Backup integrity verification failed; do not continue deployment')
            if dst.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('Backup reference verification failed; do not continue deployment')
    digest = hashlib.sha256()
    with destination.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            digest.update(chunk)
    return {'file': destination.name, 'sha256': digest.hexdigest()}


def backup_state(database_path, output):
    source, output = Path(database_path).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError('No history database to back up')
    # Refuse an active runner and existing backup directories. Never overwrite.
    with InstanceLock(source):
        output.mkdir(parents=True, exist_ok=False)
        records = [_copy_database(source, output/'history.db')]
        budget = Path(str(source)+'.api.db')
        if budget.exists():
            records.append(_copy_database(budget, output/'api-budget.db'))
        manifest = {'completed_at': datetime.now(timezone.utc).isoformat(),
                    'files': records, 'metadata_cache': 'rebuildable; not included'}
        # Only this marker certifies that every included database passed checks.
        (output/'complete.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest
