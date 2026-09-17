from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from modrecon.config import Config, ConfigError, Server, load, parse, read_raw
from modrecon.cli import add_server, lookup
from modrecon.engine import Budget, Destinations, Engine, InstanceLock
from watch import API, RequestFailure, Watch
from test_watch import payload, FakeAPI, A, B

HOOK1='https://discord.com/api/webhooks/123/first'
HOOK2='https://discord.com/api/webhooks/456/second'


def observation(sid,version='1'):
    p=payload([(A,version,'Example')]); p['server']['id']=sid
    p['server']['name']='[NA7] W.C.S. Example'
    return p


class MultiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'history.db'; self.api=FakeAPI(); self.watches=[]
    def tearDown(self):
        for w in self.watches: w.db.close()
        self.tmp.cleanup()
    def watch(self,sid,hook=HOOK1,count=2):
        w=Watch(self.path,sid,sid,self.api,hook,confirmation_polls=count)
        self.watches.append(w); return w
    def test_independent_events_candidates_destinations_and_restart(self):
        a,b=self.watch('a'),self.watch('b',HOOK2)
        for w in (a,b): w.observe(observation(w.server_id))
        a.observe(observation('a','2')); b.observe(observation('b','3'))
        b.observe(observation('b')) # B's transient candidate is discarded only for B.
        self.assertIsNotNone(a.row()['candidate_snapshot_id'])
        a.observe(observation('a','2'))
        b.observe(observation('b','4')); b.observe(observation('b','4'))
        delivered=[]
        for w in (a,b):
            w.process_pending(lambda p,w=w:delivered.append((w.webhook,p['embeds'][0]['title'])))
        self.assertEqual(delivered,[(HOOK1,'a Mod Update'),(HOOK2,'b Mod Update')])
        accepted=[w.row()['last_accepted_snapshot_id'] for w in (a,b)]
        for w in self.watches: w.db.close()
        self.watches=[]
        a,b=self.watch('a'),self.watch('b',HOOK2)
        a.observe(observation('a','2')); b.observe(observation('b','4'))
        self.assertEqual(accepted,[w.row()['last_accepted_snapshot_id'] for w in (a,b)])
        self.assertEqual(a.db.execute('SELECT count(*) FROM change_events').fetchone()[0],2)
        a.process_pending(lambda p:self.fail('Duplicate A')); b.process_pending(lambda p:self.fail('Duplicate B'))
    def test_configurable_confirmation_persists(self):
        w=self.watch('a',count=3); w.observe(observation('a')); w.observe(observation('a','2'))
        w.observe(observation('a','2')); self.assertEqual(w.row()['candidate_seen_count'],2)
        w.db.close(); self.watches=[]
        w=self.watch('a',count=3); w.observe(observation('a','2'))
        self.assertEqual(w.db.execute('SELECT count(*) FROM change_events').fetchone()[0],1)
    def test_one_worker_network_failure_does_not_delay_another(self):
        blocked,release,healthy=threading.Event(),threading.Event(),threading.Event()
        class Fake:
            def server(self,sid):
                if sid=='a': blocked.set(); release.wait(5); raise RequestFailure(503)
                healthy.set(); return observation(sid)
        config=Config((Server('a','A',HOOK1),Server('b','B',HOOK2)),self.path)
        engine=Engine(config,api=Fake())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(engine.run,True)
            try:
                self.assertTrue(blocked.wait(2)); self.assertTrue(healthy.wait(2))
            finally: release.set()
            future.result(timeout=5)
        with closing(sqlite3.connect(self.path)) as db:
            rows=dict(db.execute('SELECT upstream_server_id,last_poll_status FROM servers'))
            self.assertEqual(rows,{'a':'error','b':'ok'})
    def test_failed_webhook_does_not_block_other_destination(self):
        a,b=self.watch('a'),self.watch('b',HOOK2)
        for w in (a,b):
            for v in ('1','2','2'): w.observe(observation(w.server_id,v))
        a.process_pending(lambda p:(_ for _ in ()).throw(RequestFailure(429,300)))
        sent=[]; b.process_pending(sent.append)
        self.assertEqual(len(sent),1)
        self.assertIsNone(a.db.execute('SELECT delivered_at FROM change_events WHERE server_id=?',(a.row()['id'],)).fetchone()[0])
    def test_shared_webhook_routes_both_and_cooldown_is_per_destination(self):
        a,b=self.watch('a'),self.watch('b')
        routes=Destinations(); sent=[]
        a.send=lambda p:sent.append('a'); b.send=lambda p:sent.append('b')
        with patch('modrecon.engine.time.time',return_value=100): routes.send(a,{})
        with patch('modrecon.engine.time.time',return_value=102): routes.send(b,{})
        self.assertEqual(sent,['a','b'])
        a.send=lambda p:(_ for _ in ()).throw(RequestFailure(429,20))
        with patch('modrecon.engine.time.time',return_value=104):
            with self.assertRaises(RequestFailure): routes.send(a,{})
            with self.assertRaises(RequestFailure): routes.send(b,{})
            b.webhook=HOOK2; routes.send(b,{})
        self.assertEqual(sent,['a','b','b'])
    def test_pending_event_kept_through_upgrade_and_removed_server_not_processed(self):
        w=self.watch('a')
        for v in ('1','2','2'): w.observe(observation('a',v))
        before=dict(w.row()); event=w.db.execute('SELECT id FROM change_events').fetchone()[0]
        w.db.close(); self.watches=[]
        w=self.watch('a'); self.assertEqual(w.row()['last_accepted_snapshot_id'],before['last_accepted_snapshot_id'])
        other=self.watch('b'); sent=[]; other.process_pending(sent.append)
        self.assertEqual(sent,[])
        w.process_pending(sent.append); self.assertEqual(len(sent),1)
        self.assertEqual(w.db.execute('SELECT id FROM change_events').fetchone()[0],event)
    def test_budget_persists_and_shared_cooldown(self):
        clock=[100000.0]; path=self.root/'budget.db'
        budget=Budget(path,minute=2,day=3,clock=lambda:clock[0])
        budget.take(); budget.take()
        with self.assertRaises(RequestFailure): budget.take()
        clock[0]+=61
        budget=Budget(path,minute=2,day=3,clock=lambda:clock[0]); budget.take()
        with self.assertRaises(RequestFailure): budget.take()
        clock[0]=172800
        budget.take(); budget.defer(120)
        with self.assertRaises(RequestFailure): Budget(path,clock=lambda:clock[0]).take()
    def test_instance_lock(self):
        with InstanceLock(self.path):
            with self.assertRaises(ValueError):
                with InstanceLock(self.path): pass
        with InstanceLock(self.path): pass


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'modrecon.yaml'
        self.raw={'servers':[{'server_id':'a','name':'A','webhook_url':'${A_WEBHOOK}'}]}
        self.env=patch.dict(os.environ,{'A_WEBHOOK':HOOK1}); self.env.start()
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def test_paths_and_shared_webhook(self):
        self.raw['servers'].append({'server_id':'b','name':'B','webhook_url':'${A_WEBHOOK}'})
        config=parse(self.raw,self.path)
        self.assertEqual(config.servers[0].webhook_url,config.servers[1].webhook_url)
        self.assertEqual(config.database_path,self.path.parent/'data/mod-recon.db')
    def test_invalid_config_fails_before_polling(self):
        for field,value in [('poll_interval',1),('poll_interval',True),('confirmation_polls',1),('requests_per_day',1),('unknown','x')]:
            raw={**self.raw,field:value}
            with self.subTest(field=field),self.assertRaises(ConfigError): parse(raw,self.path)
        raw=copy.deepcopy(self.raw); raw['servers']*=2
        with self.assertRaises(ConfigError): parse(raw,self.path)
    def test_missing_and_invalid_secret_never_echoed(self):
        self.raw['servers'][0]['webhook_url']='${MISSING_MODRECON_TEST_SECRET}'
        with self.assertRaises(ConfigError): parse(self.raw,self.path)
        self.raw['servers'][0]['webhook_url']='secret-do-not-echo'
        with self.assertRaises(ConfigError) as exc: parse(self.raw,self.path)
        self.assertNotIn('secret-do-not-echo',str(exc.exception))
    def test_duplicate_yaml_keys(self):
        self.path.write_text('servers: []\nservers: []',encoding='utf-8')
        with self.assertRaises(ConfigError): read_raw(self.path)
    def test_add_writes_reference_and_duplicate_does_not_change_file(self):
        api=type('Fake',(),{'server':lambda _,sid:observation(sid)})()
        add_server(self.path,api,'a','Name','A_WEBHOOK')
        original=self.path.read_bytes()
        self.assertIn(b'${A_WEBHOOK}',original); self.assertNotIn(HOOK1.encode(),original)
        self.assertEqual(len(load(self.path).servers),1)
        with self.assertRaises(ConfigError): add_server(self.path,api,'a','Other','OTHER')
        self.assertEqual(self.path.read_bytes(),original)
    def test_find_handles_wcs_punctuation_and_does_not_choose(self):
        class Fake:
            def search(self,query,page):
                self.query=query
                return [{'id':'a','name':'[NA7] W.C.S. Realism','scenarioName':'Everon','online':True},
                        {'id':'b','name':'[NA7] W.C.S. Realism','scenarioName':'Fallujah','online':True},
                        {'id':'c','name':'Other NA7','online':True},
                        {'id':'d','name':'[NA70] W.C.S. Realism','online':True}],2
        api=Fake(); matches,pages=lookup(api,'WCS NA7')
        self.assertEqual(api.query,'na7'); self.assertEqual([m['id'] for m in matches],['a','b']); self.assertEqual(pages,2)
    def test_add_offline_server_verifies_identity_without_creating_baseline(self):
        p=observation('a'); p['server']['online']=False; p['server']['present']=False
        del p['server']['mods']
        api=type('Fake',(),{'server':lambda _,sid:p})()
        add_server(self.path,api,'a','Offline server','A_WEBHOOK')
        self.assertEqual(load(self.path).servers[0].server_id,'a')
        self.assertFalse((self.path.parent/'data/mod-recon.db').exists())
    def test_search_rejects_stale_and_unknown_schema(self):
        api=API('https://example.com','test')
        with patch.object(api,'get',return_value={'dataset':{'stale':True,'warming':False},'data':[],'meta':{'totalPages':1}}):
            with self.assertRaises(RequestFailure): api.search('NA7')


if __name__=='__main__': unittest.main()
