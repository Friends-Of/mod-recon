from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from modrecon.cache import EnrichmentCache
from modrecon.config import Config, ConfigError, Server, parse
from modrecon.engine import Budget, Engine, SharedAPI, verify_quota
from test_multiserver import observation, HOOK1
from watch import API, NoCredentialRedirect, RequestFailure


class ScalingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def test_optional_key_config_and_repr(self):
        raw={'servers':[{'name':'Example','server_id':'a','webhook_url':HOOK1}]}
        with patch.dict(os.environ,{},clear=True):
            self.assertIsNone(parse(raw,self.root/'config.yaml').api_key)
            with self.assertRaises(ConfigError):
                parse({**raw,'required_api_plan':'developer'},self.root/'config.yaml')
        with patch.dict(os.environ,{'REFORGERMODS_API_KEY':'test-secret'},clear=True):
            config=parse(raw,self.root/'config.yaml')
            self.assertEqual(config.api_key,'test-secret')
            self.assertNotIn('test-secret',repr(config))
            with self.assertRaises(ConfigError):
                parse({**raw,'base_url':'https://example.com/v2'},self.root/'config.yaml')

    def test_auth_header_and_no_redirect_or_anonymous_fallback(self):
        response=MagicMock()
        response.__enter__.return_value=response
        response.status=200
        response.read.return_value=b'{"status":"success"}'
        opener=MagicMock();opener.open.return_value=response
        with patch('watch.build_opener',return_value=opener),patch('watch.urlopen') as anonymous:
            api=API('https://api.reforgermods.net/v2','test','test-secret')
            api.get('/servers/a')
            req=opener.open.call_args.args[0]
            self.assertEqual(req.get_header('Authorization'),'Bearer test-secret')
            anonymous.assert_not_called()
            opener.open.side_effect=HTTPError(req.full_url,401,'test-secret',{},None)
            with self.assertRaises(RequestFailure) as error: api.get('/servers/a')
            self.assertNotIn('test-secret',str(error.exception))
            anonymous.assert_not_called()
        with self.assertRaises(RequestFailure):
            NoCredentialRedirect().redirect_request(Request('https://api.reforgermods.net/v2/servers/a'),None,302,'redirect',{},'https://example.com')

    def test_cache_single_flight_exact_versions_persistence_and_expiry(self):
        clock=[1000];path=self.root/'metadata.db'
        cache=EnrichmentCache(path,clock=lambda:clock[0]);calls=[]
        def fetch(mid,version):
            calls.append((mid,version));time.sleep(.05)
            return {'modId':mid,'version':version,'size':123}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:cache.get('a'*16,'2.0',fetch),range(8)))
        self.assertEqual(len(calls),1)
        self.assertTrue(all(r==results[0] for r in results))
        fresh=EnrichmentCache(path,clock=lambda:clock[0])
        fresh.get('A'*16,'2.0',fetch)
        fresh.get('A'*16,'2.0-beta',fetch)
        fresh.get('B'*16,'2.0',fetch)
        self.assertEqual(len(calls),3)
        clock[0]+=86401;fresh.get('A'*16,'2.0',fetch)
        self.assertEqual(len(calls),4)

    def test_negative_cache_expires_and_does_not_replace_other_versions(self):
        clock=[1];cache=EnrichmentCache(self.root/'metadata.db',clock=lambda:clock[0]);calls=[]
        def fail(mid,ver):calls.append(ver);raise RequestFailure(503)
        for _ in range(2):
            with self.assertRaises(RequestFailure):cache.get('A','bad',fail)
        self.assertEqual(calls,['bad'])
        self.assertEqual(cache.get('A','good',lambda *_:{'size':1}),{'size':1})
        clock[0]+=61
        with self.assertRaises(RequestFailure):cache.get('A','bad',fail)
        self.assertEqual(len(calls),2)

    def test_cache_hit_spends_no_api_budget(self):
        budget=Budget(self.root/'budget.db',minute=10000)
        api=SharedAPI('https://api.reforgermods.net/v2',budget,cache=EnrichmentCache(self.root/'cache.db'))
        detail={'status':'success','data':{'modId':'A'*16,'version':'2','size':1}}
        with patch.object(API,'get',return_value=detail) as network:
            api.version('A'*16,'2');api.version('a'*16,'2')
            self.assertEqual(network.call_count,1)
        with closing(sqlite3.connect(budget.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],1)

    def test_optional_minute_cap_preserves_poll_slots_across_restart(self):
        path=self.root/'budget.db'
        budget=Budget(path,minute=10,day=1000,clock=lambda:100000)
        budget.take('optional');budget.take('optional')
        with self.assertRaises(RequestFailure):budget.take('optional')
        budget=Budget(path,minute=10,day=1000,clock=lambda:100000)
        for _ in range(8):budget.take('poll')
        with self.assertRaises(RequestFailure):budget.take('poll')

    def test_optional_day_cap_preserves_poll_slots_and_legacy_budget(self):
        path=self.root/'budget.db'
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE requests(at REAL NOT NULL)')
            db.execute('INSERT INTO requests VALUES(99999)')
        budget=Budget(path,minute=100,day=10,clock=lambda:100000)
        budget.take('optional');budget.take('optional')
        with self.assertRaises(RequestFailure):budget.take('optional')
        for _ in range(7):budget.take('poll')
        with self.assertRaises(RequestFailure):budget.take('poll')

    def test_quota_gate_rejects_mismatch_before_creating_server_state(self):
        config=Config((Server('a','A',HOOK1),),self.root/'history.db',api_key='test-secret',
                      required_api_plan='developer',minimum_api_daily_quota=100000,requests_per_day=100000)
        api=MagicMock()
        api.get.return_value={'authenticated':False,'rate_limit':{'plan':'free','limit_per_day':5000,'limit_per_minute':60,'burst':20}}
        with self.assertRaises(ValueError):Engine(config,api=api).run(True)
        api.server.assert_not_called();self.assertFalse(config.database_path.exists())
        api.get.return_value={'authenticated':True,'rate_limit':{'plan':'developer','limit_per_day':99999,'limit_per_minute':300,'burst':100}}
        with self.assertRaises(ValueError):verify_quota(api,config)
        api.get.return_value['rate_limit']['limit_per_day']=100000
        self.assertEqual(verify_quota(api,config)['plan'],'developer')

    def test_five_free_baselines_are_independent_silent_and_staggered(self):
        names=[str(i) for i in range(5)];seen=[]
        config=Config(tuple(Server(i,'Example '+i,HOOK1) for i in names),self.root/'history.db',requests_per_minute=200)
        class Fake:
            def server(self,sid):seen.append((sid,time.monotonic()));return observation(sid)
        destinations=MagicMock()
        Engine(config,api=Fake(),destinations=destinations).run(True)
        self.assertEqual({sid for sid,_ in seen},set(names))
        self.assertGreater(max(t for _,t in seen)-min(t for _,t in seen),1)
        destinations.send.assert_not_called()
        with closing(sqlite3.connect(config.database_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT last_accepted_snapshot_id) FROM servers').fetchone()[0],5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM change_events').fetchone()[0],0)

    def test_free_quota_is_valid_without_credentials(self):
        config=Config((Server('a','A',HOOK1),),self.root/'history.db')
        api=MagicMock()
        api.get.return_value={'authenticated':False,'rate_limit':{'plan':'free','limit_per_day':5000,'limit_per_minute':60,'burst':20}}
        self.assertEqual(verify_quota(api,config)['plan'],'free')

    def test_server_poll_is_one_detail_request_and_not_metadata_cache(self):
        api=SharedAPI('https://api.reforgermods.net/v2',Budget(self.root/'budget.db',minute=10000))
        with patch.object(API,'get',return_value=observation('a')) as network:
            api.server('a')
            network.assert_called_once_with('/servers/a')
        with closing(sqlite3.connect(api.budget.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM optional_requests').fetchone()[0],0)
