"""Small delivery-free integration API. One managed worker per upstream ID."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import threading

from .config import Config, Server
from .discovery import search_servers
from .engine import Engine, InstanceLock, verify_quota
from .events import EventReader
from .storage import migrate


@dataclass(frozen=True)
class Monitor:
    server_id: str
    display_name: str
    poll_interval: int = 120


class Core(EventReader):
    def __init__(self,database_path,*,confirmation_polls=2,requests_per_minute=50,
                 requests_per_day=5000,api_key=None,api=None):
        super().__init__(database_path)
        for value,minimum in ((confirmation_polls,2),(requests_per_minute,1),(requests_per_day,1)):
            if type(value) is not int or value<minimum: raise ValueError('Invalid monitoring limits')
        self.config=Config((),self.path,confirmation_polls=confirmation_polls,
                           requests_per_minute=requests_per_minute,requests_per_day=requests_per_day,api_key=api_key)
        self.engine=Engine(self.config,api=api)
        self.guard=threading.RLock()
        self.workers={}
        self.active=False
        self.lock=None

    def __enter__(self):
        with self.guard:
            if self.active: raise ValueError('Core is already running')
            self.lock=InstanceLock(self.path)
            self.lock.__enter__()
            try:
                if self.config.api_key: verify_quota(self.engine.api,self.config)
                with closing(sqlite3.connect(self.path,timeout=30)) as db: migrate(db)
                self.active=True
            except BaseException:
                self.lock.__exit__();self.lock=None
                raise
        return self

    def _require_active(self):
        if not self.active: raise ValueError('Use Core as a context manager')

    def apply_monitor_set(self,monitors):
        desired={}
        for spec in monitors:
            if (not isinstance(spec,Monitor) or not isinstance(spec.server_id,str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',spec.server_id)
                    or not isinstance(spec.display_name,str) or not spec.display_name.strip() or len(spec.display_name)>180
                    or type(spec.poll_interval) is not int or spec.poll_interval<120):
                raise ValueError('Invalid monitor identity, display name, or interval')
            if spec.server_id in desired: raise ValueError('Duplicate upstream server IDs')
            desired[spec.server_id]=spec
        if (sum(86400/s.poll_interval for s in desired.values())>self.config.requests_per_day*.8
                or sum(60/s.poll_interval for s in desired.values())>self.config.requests_per_minute*.8):
            raise ValueError('Desired monitors exceed the reserved polling allowance')
        with self.guard:
            self._require_active()
            changing=[sid for sid,(spec,stop,thread) in self.workers.items()
                      if desired.get(sid)!=spec or not thread.is_alive()]
            for sid in changing: self.workers[sid][1].set()
            for sid in changing:
                self.workers.pop(sid)[2].join()
                with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
                    db.execute('UPDATE servers SET enabled=0 WHERE upstream_server_id=?',(sid,))
            new=[s for s in desired.values() if s.server_id not in self.workers]
            for index,spec in enumerate(new):
                stop=threading.Event()
                server=Server(spec.server_id,spec.display_name,None)
                thread=threading.Thread(target=self.engine.worker,
                                        kwargs={'server':server,'offset':index*60/self.config.requests_per_minute,
                                                'stop_event':stop,'poll_interval':spec.poll_interval},
                                        name='modrecon-poll-'+spec.server_id)
                self.workers[spec.server_id]=(spec,stop,thread)
                thread.start()
            return tuple(desired)

    def search_servers(self,query,page=1):
        self._require_active()
        return search_servers(self.engine.api,query,page)

    def get_status(self,server_id):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT upstream_server_id AS server_id,label AS display_name,enabled,last_poll_at,last_poll_status,last_error,last_accepted_snapshot_id,candidate_seen_count FROM servers WHERE upstream_server_id=?',(server_id,)).fetchone()
            return dict(row) if row else None

    def close(self):
        with self.guard:
            if not self.active: return
            for spec,stop,thread in self.workers.values(): stop.set()
            for spec,stop,thread in self.workers.values(): thread.join()
            self.workers.clear()
            self.active=False
            self.lock.__exit__();self.lock=None

    def __exit__(self,*args): self.close()
