"""Independent server workers with shared upstream budget and webhook pacing."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
import logging
from pathlib import Path
import sqlite3
import threading
import time

from watch import API, RequestFailure, Watch

LOG = logging.getLogger('mod-recon')


class Budget:
    """Persistent request budget shared by lookup, polling, and enrichment."""
    def __init__(self,path,minute=50,day=5000,clock=time.time):
        self.path,self.minute,self.day,self.clock = str(path),minute,day,clock
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path,timeout=30) as db:
            db.executescript('CREATE TABLE IF NOT EXISTS requests(at REAL NOT NULL); CREATE TABLE IF NOT EXISTS cooldown(until REAL);')

    def take(self):
        now=self.clock(); midnight=now//86400*86400
        with sqlite3.connect(self.path,timeout=30) as db:
            db.execute('BEGIN IMMEDIATE')
            delay=db.execute('SELECT MAX(until) FROM cooldown').fetchone()[0] or 0
            if delay>now:
                raise RequestFailure('shared upstream cooldown',delay-now)
            db.execute('DELETE FROM requests WHERE at<?',(midnight,))
            if db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]>=self.day:
                raise RequestFailure('daily API budget exhausted',midnight+86400-now)
            count,oldest=db.execute('SELECT COUNT(*),MIN(at) FROM requests WHERE at>?',(now-60,)).fetchone()
            if count>=self.minute:
                raise RequestFailure('minute API budget exhausted',oldest+60-now)
            db.execute('INSERT INTO requests VALUES(?)',(now,))

    def defer(self,seconds):
        with sqlite3.connect(self.path,timeout=30) as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT MAX(until) FROM cooldown').fetchone()[0] or 0
            db.execute('DELETE FROM cooldown')
            db.execute('INSERT INTO cooldown VALUES(?)',(max(old,self.clock()+seconds),))


class SharedAPI(API):
    def __init__(self,base, budget):
        super().__init__(base,'mod-recon/0.2.0')
        self.budget=budget

    def get(self,path):
        self.budget.take()
        try:
            return super().get(path)
        except RequestFailure as exc:
            if exc.retry_after:
                self.budget.defer(exc.retry_after)
            raise


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
        self.api=api or SharedAPI(config.base_url,Budget(str(config.database_path)+'.api.db',config.requests_per_minute,config.requests_per_day))
        self.destinations=destinations or Destinations()
        self.setup_lock=threading.Lock()
        self.stop=threading.Event()

    def worker(self,server,once=False,offset=0):
        if not once and self.stop.wait(offset): return
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
            pool=ThreadPoolExecutor(max_workers=len(self.config.servers),thread_name_prefix='modrecon')
            futures=[pool.submit(self.worker,s,once,i*self.config.poll_interval/len(self.config.servers)) for i,s in enumerate(self.config.servers)]
            try:
                finished,_=wait(futures,return_when=FIRST_EXCEPTION)
                for future in finished: future.result()
            except KeyboardInterrupt:
                self.stop.set()
            finally:
                self.stop.set()
                pool.shutdown(wait=True)
