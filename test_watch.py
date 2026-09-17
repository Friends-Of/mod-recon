import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from watch import API, RequestFailure, Watch, validate

A, B, C = 'A'*16, 'B'*16, 'C'*16

def payload(mods=None):
    mods = mods if mods is not None else [(A, '1', 'Alpha'), (B, '1', 'Beta')]
    return {'status':'success','dataset':{'warming':False,'stale':False,'snapshotAgeSeconds':12.5,'lastCollectionAt':'2026-09-17T08:00:00Z'},'server':{'id':'na7','present':True,'online':True,'modCount':len(mods),'mods':[{'id':i,'version':v,'name':n} for i,v,n in mods]}}

class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail = False
    def version(self, mod_id, version):
        self.calls.append((mod_id,version))
        if self.fail:
            raise RequestFailure(503)
        return {'size':100,'changelog':'A short note','gameVersion':'1.8','createdAt':'created','updatedAt':'updated'}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'test.db')
        self.api = FakeAPI()
        self.watch = Watch(self.path,'na7','WCS NA7',self.api)
        self.sent = []
    def tearDown(self):
        self.watch.db.close()
        self.tmp.cleanup()
    def observe(self, p):
        self.watch.observe(p)
        self.watch.process_pending(self.sent.append)
    def count(self):
        return self.watch.db.execute('SELECT COUNT(*) FROM change_events').fetchone()[0]
    def change(self):
        return payload([(A,'opaque-beta','Alpha'),(C,'2','Gamma')])
    def test_baseline_silent(self):
        self.observe(payload())
        self.assertIsNotNone(self.watch.row()['last_accepted_snapshot_id'])
        self.assertEqual(self.sent,[])
    def test_identical_silent(self):
        for _ in range(4): self.observe(payload())
        self.assertEqual(self.count(),0)
        self.assertEqual(self.api.calls,[])
    def test_transient_change(self):
        for p in (payload(),self.change(),payload()): self.observe(p)
        self.assertEqual(self.count(),0)
        self.assertIsNone(self.watch.row()['candidate_snapshot_id'])
    def test_confirmed_exactly_once(self):
        for p in (payload(),self.change(),self.change(),self.change()): self.observe(p)
        self.assertEqual(self.count(),1)
        self.assertEqual(len(self.sent),1)
    def test_diff_and_enrichment(self):
        for p in (payload(),self.change(),self.change()): self.observe(p)
        items = {r['mod_id']:dict(r) for r in self.watch.db.execute('SELECT * FROM change_items')}
        self.assertEqual(items[A]['change_type'],'updated')
        self.assertEqual(items[A]['before_version'],'1')
        self.assertEqual(items[A]['after_version'],'opaque-beta')
        self.assertEqual(items[B]['change_type'],'removed')
        self.assertEqual(items[B]['metadata_status'],'not_requested')
        self.assertEqual(items[C]['change_type'],'added')
        self.assertEqual(items[C]['size_bytes'],100)
        self.assertEqual(items[C]['game_version'],'1.8')
        self.assertEqual(set(self.api.calls),{(A,'opaque-beta'),(C,'2')})
    def test_names_and_order_ignored(self):
        self.observe(payload())
        p = payload([(B,'1','Renamed'),(A,'1','Other')])
        self.observe(p); self.observe(p)
        self.assertEqual(self.count(),0)
    def test_unsafe_observations(self):
        self.observe(payload())
        cases = []
        for field in ('online','present'):
            p = payload([]); p['server'][field] = False; cases.append(p)
        for field in ('stale','warming'):
            p = payload([]); p['dataset'][field] = True; cases.append(p)
        p = payload([]); p['server']['modCount'] = 2; cases.append(p)
        p = payload(); del p['server']['mods']; cases.append(p)
        p = payload(); p['server']['mods'][1]['id'] = A.lower(); cases.append(p)
        p = payload(); p['server']['mods'][0]['id'] = 'bad'; cases.append(p)
        p = payload(); p['server']['mods'][0]['version'] = None; cases.append(p)
        p = payload(); del p['dataset']['stale']; cases.append(p)
        p = payload(); p['server']['id'] = 'wrong'; cases.append(p)
        for p in cases:
            with self.subTest(p=p):
                self.observe(self.change()); self.observe(p)
                self.assertIsNone(self.watch.row()['candidate_snapshot_id'])
                self.assertEqual(self.count(),0)
    def test_rejection_breaks_confirmation(self):
        self.observe(payload()); self.observe(self.change())
        self.watch.reject('error','test')
        self.observe(self.change())
        self.assertEqual(self.count(),0)
        self.observe(self.change())
        self.assertEqual(self.count(),1)
    def test_third_hash_restarts_confirmation(self):
        self.observe(payload()); self.observe(self.change())
        third = payload([(A,'third','Alpha')])
        self.observe(third)
        self.assertEqual(self.count(),0)
        self.observe(third)
        self.assertEqual(self.count(),1)
    def test_metadata_failure_still_sends(self):
        self.api.fail = True
        for p in (payload(),self.change(),self.change()): self.observe(p)
        self.assertEqual(len(self.sent),1)
        statuses = [r[0] for r in self.watch.db.execute("SELECT metadata_status FROM change_items WHERE change_type!='removed'")]
        self.assertEqual(statuses,['unavailable','unavailable'])
    def test_restart_preserves_candidate_and_baseline(self):
        self.observe(payload()); self.observe(self.change())
        self.watch.db.close()
        self.watch = Watch(self.path,'na7','WCS NA7',self.api)
        self.observe(self.change())
        self.watch.db.close()
        self.watch = Watch(self.path,'na7','WCS NA7',self.api)
        self.observe(self.change())
        self.assertEqual(self.count(),1)
        self.assertEqual(len(self.sent),1)
    def test_retry_persisted_no_reenrichment(self):
        self.watch.observe(payload()); self.watch.observe(self.change()); self.watch.observe(self.change())
        def fail(p): raise RequestFailure(429,300)
        self.watch.process_pending(fail)
        self.assertEqual(len(self.api.calls),2)
        self.watch.db.close()
        self.watch = Watch(self.path,'na7','WCS NA7',self.api)
        self.watch.process_pending(self.sent.append)
        self.assertEqual(self.sent,[])
        with self.watch.db:
            self.watch.db.execute('UPDATE change_events SET delivery_next_at=0')
        self.watch.process_pending(self.sent.append)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(len(self.api.calls),2)
    def test_large_notification_within_discord_limits(self):
        self.watch.observe(payload([]))
        p = payload([(f'{i:016X}','version','@everyone '+('long '*40)) for i in range(100)])
        self.watch.observe(p); self.watch.observe(p)
        self.watch.process_pending(self.sent.append)
        message = self.sent[0]
        self.assertEqual(message['allowed_mentions']['parse'],[])
        self.assertLessEqual(sum(len(e['description']) for e in message['embeds']),6000)
        self.assertTrue(all(len(e['description']) <= 4096 for e in message['embeds']))
        self.assertIn('Full list of all 100 changes',str(message))
        self.assertEqual(message['_text_attachment']['text'].count('  After:'),100)
    def test_adapter_exact_version_schema(self):
        api = API('https://example.com','test')
        with patch.object(api,'get',return_value={'data':{'modId':A,'version':'1','size':42}}):
            self.assertEqual(api.version(A,'1')['size'],42)
        with patch.object(api,'get',return_value={'data':{'modId':A,'version':'2'}}):
            with self.assertRaises(RequestFailure): api.version(A,'1')

if __name__ == '__main__': unittest.main()
