"""Persistent, exact-version metadata cache. Never supplies server manifests."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time

from watch import RequestFailure


class EnrichmentCache:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        self.guard = threading.Lock()
        self.locks = {}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata(mod_id TEXT, version TEXT, payload TEXT, expires REAL, PRIMARY KEY(mod_id,version))')

    def get(self, mod_id, version, fetch):
        key = (mod_id.upper(), version)
        # One in-flight lookup per exact version across all workers in this engine.
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            with closing(sqlite3.connect(self.path, timeout=30)) as db:
                row = db.execute('SELECT payload,expires FROM metadata WHERE mod_id=? AND version=?',key).fetchone()
            if row and row[1] > self.clock():
                if row[0] is None:
                    raise RequestFailure('cached metadata unavailable',row[1]-self.clock())
                return json.loads(row[0])
            try:
                detail = fetch(*key)
            except RequestFailure:
                # Brief negative cache prevents a deployment causing repeated failed lookups.
                self.put(key, None, 60)
                raise
            self.put(key, detail, 86400)
            return detail

    def put(self, key, detail, ttl):
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?,?)',
                       (*key,json.dumps(detail) if detail is not None else None,self.clock()+ttl))
