"""Independent server workers with shared upstream budget and webhook pacing."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
from contextlib import closing
import logging
import re
from pathlib import Path
import sqlite3
import threading
import time

from watch import API, RequestFailure, Watch
from .cache import EnrichmentCache

LOG = logging.getLogger('mod-recon')


class Budget:
    """Persistent request budget shared by lookup, polling, and enrichment."""
    def __init__(self,path,minute=50,day=5000,clock=time.time):
        self.path,self.minute,self.day,self.clock = str(path),minute,day,clock
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=30)) as db, db:
            db.executescript('CREATE TABLE IF NOT EXISTS requests(at REAL NOT NULL); CREATE TABLE IF NOT EXISTS cooldown(until REAL);')
            db.execute('CREATE TABLE IF NOT EXISTS optional_requests(at REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS requests_at ON requests(at)')
            db.execute('CREATE INDEX IF NOT EXISTS optional_requests_at ON optional_requests(at)')

    def take(self, purpose='poll'):
        now=self.clock(); midnight=now//86400*86400
        with closing(sqlite3.connect(self.path,timeout=30)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            delay=db.execute('SELECT MAX(until) FROM cooldown').fetchone()[0] or 0
            if delay>now:
                raise RequestFailure('shared upstream cooldown',delay-now)
            db.execute('DELETE FROM requests WHERE at<?',(midnight,))
            db.execute('DELETE FROM optional_requests WHERE at<?',(midnight,))
            if db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]>=self.day:
                raise RequestFailure('daily API budget exhausted',midnight+86400-now)
            count,oldest=db.execute('SELECT COUNT(*),MIN(at) FROM requests WHERE at>?',(now-60,)).fetchone()
            if count>=self.minute:
                raise RequestFailure('minute API budget exhausted',oldest+60-now)
            if purpose!='poll':
                optional_day=db.execute('SELECT COUNT(*) FROM optional_requests').fetchone()[0]
                optional_minute=db.execute('SELECT COUNT(*) FROM optional_requests WHERE at>?',(now-60,)).fetchone()[0]
                if optional_day>=int(self.day*.2) or optional_minute>=int(self.minute*.2):
                    raise RequestFailure('optional API budget exhausted; polling capacity reserved',60)
                db.execute('INSERT INTO optional_requests VALUES(?)',(now,))
            db.execute('INSERT INTO requests VALUES(?)',(now,))

    def defer(self,seconds):
        with closing(sqlite3.connect(self.path,timeout=30)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT MAX(until) FROM cooldown').fetchone()[0] or 0
            db.execute('DELETE FROM cooldown')
            db.execute('INSERT INTO cooldown VALUES(?)',(max(old,self.clock()+seconds),))


class SharedAPI(API):
    def __init__(self,base, budget, api_key=None, cache=None):
        super().__init__(base,'mod-recon/0.2.9',api_key)
        self.budget=budget
        self.cache=cache
        self.pace_lock=threading.Lock()
        self.next_request=0

    def version(self,mod_id,version):
        if self.cache:
            return self.cache.get(mod_id,version,super().version)
        return super().version(mod_id,version)

    def get(self,path):
        purpose='poll' if re.fullmatch(r'/servers/[A-Za-z0-9_-]+',path) else 'optional'
        # Smooth requests across threads; do not hold this lock during network I/O.
        with self.pace_lock:
            time.sleep(max(0,self.next_request-time.monotonic()))
            self.budget.take(purpose)
            self.next_request=time.monotonic()+60/self.budget.minute
        try:
            return super().get(path)
        except RequestFailure as exc:
            if exc.retry_after:
                self.budget.defer(exc.retry_after)
            raise


def configured_api(config):
    return SharedAPI(config.base_url,
                     Budget(str(config.database_path)+'.api.db',config.requests_per_minute,config.requests_per_day),
                     config.api_key,EnrichmentCache(str(config.database_path)+'.metadata.db'))


def verify_quota(api,config):
    result=api.get('/rate-limits')
    rate=result.get('rate_limit',{})
    requires_auth=bool(config.api_key or config.required_api_plan or config.minimum_api_daily_quota)
    if (type(result.get('authenticated')) is not bool or not isinstance(rate,dict)
            or (requires_auth and result.get('authenticated') is not True)
            or rate.get('plan') not in (('developer','pro') if requires_auth else ('free','developer','pro'))
            or (config.required_api_plan and rate.get('plan')!=config.required_api_plan)):
        raise ValueError('API plan verification failed; monitoring was not started')
    for key,required in [('limit_per_day',max(config.requests_per_day,config.minimum_api_daily_quota)),
                         ('limit_per_minute',config.requests_per_minute)]:
        if type(rate.get(key)) is not int or rate[key]<required:
            raise ValueError('Live API quota is below the required allowance; monitoring was not started')
    if type(rate.get('burst')) is not int or rate['burst']<1:
        raise ValueError('Live API burst allowance unavailable; monitoring was not started')
    return {k:rate[k] for k in ('plan','limit_per_day','limit_per_minute','burst')}


class Destinations:
    """Serialize only posts sharing a webhook, and respect its Retry-After."""
    def __init__(self):
        self.guard=threading.Lock(); self.locks={}; self.next_at={}

    def send(self,watch,payload):
        key=watch.webhook
        with self.guard:
            lock=self.locks.setdefault(key,threading.Lock())
        with lock:
            delay=self.next_at.get(key,0)-time.time()
            if delay>0:
                raise RequestFailure('destination cooldown',delay)
            try:
                watch.send(payload)
                self.next_at[key]=time.time()+1
            except RequestFailure as exc:
                self.next_at[key]=time.time()+max(1,exc.retry_after)
                raise


class InstanceLock:
    """OS-held lock, automatically released after a crash."""
    def __init__(self,path): self.path=Path(str(path)+'.lock'); self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b')
        try:
            if __import__('os').fstat(self.file.fileno()).st_size==0:
                self.file.write(b'0'); self.file.flush()
            self.file.seek(0)
            if __import__('os').name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('Another v0.2 monitor is using this database') from None
        return self
    def __exit__(self,*args): self.file.close()


class Engine:
    def __init__(self,config,api=None,destinations=None):
        self.config=config
        self.api=api or configured_api(config)
        self.destinations=destinations or Destinations()
        self.setup_lock=threading.Lock()
        self.stop=threading.Event()

    def worker(self,server,once=False,offset=0):
        if self.stop.wait(offset): return
        # Each worker creates, owns, and closes its SQLite connection.
        with self.setup_lock:
            watch=Watch(self.config.database_path,server.server_id,server.name,self.api,
                        server.webhook_url,self.config.donation_url,self.config.confirmation_polls)
        try:
            while not self.stop.is_set():
                started=time.monotonic()
                try:
                    watch.poll()
                    watch.process_pending(lambda payload:self.destinations.send(watch,payload))
                except Exception as exc:
                    # Do not log exception text: unexpected HTTP errors can contain secrets.
                    LOG.error('Server %s worker failed (%s); retry next poll',server.server_id,type(exc).__name__)
                    watch.reject('error','Worker failure; see server worker log')
                    if once: raise
                if once: return
                self.stop.wait(max(0,self.config.poll_interval-(time.monotonic()-started)))
        finally:
            watch.db.close()

    def run(self,once=False):
        with InstanceLock(self.config.database_path):
            if self.config.api_key or self.config.required_api_plan or self.config.minimum_api_daily_quota:
                quota=verify_quota(self.api,self.config)
                LOG.info('Verified API plan %s: %s/day, %s/minute',quota['plan'],quota['limit_per_day'],quota['limit_per_minute'])
            pool=ThreadPoolExecutor(max_workers=len(self.config.servers),thread_name_prefix='modrecon')
            # Once-mode is paced too, without waiting an entire poll interval.
            spacing=max(.01,60/self.config.requests_per_minute) if once else self.config.poll_interval/len(self.config.servers)
            futures=[pool.submit(self.worker,s,once,i*spacing) for i,s in enumerate(self.config.servers)]
            try:
                finished,_=wait(futures,return_when=FIRST_EXCEPTION)
                for future in finished: future.result()
            except KeyboardInterrupt:
                self.stop.set()
            finally:
                self.stop.set()
                pool.shutdown(wait=True)
