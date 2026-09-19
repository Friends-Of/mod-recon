from contextlib import closing, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
import json
import io
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from modrecon.backup import backup_state
from modrecon.core import Core, Monitor
from modrecon.cli import main
from modrecon.config import Config, Server
from modrecon.engine import Budget, Engine, SharedAPI
from modrecon.events import EventReader, publish_stored
from modrecon.storage import migrate
from test_multiserver import observation, HOOK1, HOOK2
from test_watch import FakeAPI, A
from watch import API, RequestFailure, Watch


def legacy_database(path):
    with closing(sqlite3.connect(path)) as db,db:
        db.executescript(Path('modrecon/schema.sql').read_text())
        db.execute("INSERT INTO servers(id,upstream_server_id,label,created_at) VALUES('internal','a','Everon Alpha','2026-09-01T00:00:00Z')")
        for i in range(1,4):
            db.execute("INSERT INTO manifest_snapshots(id,server_id,observed_at,mod_count,manifest_hash,state) VALUES(?,?,?,?,?,?)",(str(i),'internal',f'2026-09-01T00:0{i}:00Z',1,'hash'+str(i),'accepted'))
            db.execute('INSERT INTO snapshot_mods VALUES(?,?,?,?,?)',(str(i),A,'Example',str(i),0))
        db.execute("UPDATE servers SET last_accepted_snapshot_id='3'")
        payload='{"content": "Saved legacy message", "allowed_mentions": {"parse": []}}'
        for i in (1,2):
            eid='delivered' if i==1 else 'queued'
            db.execute('INSERT INTO change_events(id,server_id,previous_snapshot_id,new_snapshot_id,detected_at,added_count,removed_count,updated_count,delivered_at,payload_json,enrichment_done) VALUES(?,?,?,?,?,0,0,1,?,?,0)',(eid,'internal',str(i),str(i+1),f'2026-09-01T00:1{i}:00Z','2026-09-01T00:12:01Z' if i==1 else None,payload))
            db.execute("INSERT INTO change_items(event_id,change_type,mod_id,mod_name,before_version,after_version,metadata_status) VALUES(?,'updated',?,'Example',?,?,'not_requested')",(eid,A,str(i),str(i+1)))
    return payload


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'state.db';self.watches=[]
    def tearDown(self):
        for w in self.watches: w.db.close()
        self.tmp.cleanup()
    def watch(self,sid='a',hook=None,api=None):
        w=Watch(self.path,sid,'Everon Alpha',api or FakeAPI(),hook);self.watches.append(w);return w
    def change(self,w):
        for ver in ('old-beta','build-02','build-02'): w.observe(observation(w.server_id,ver))
    def test_first_baseline_silent_then_persisted_exact_event_without_delivery(self):
        w=self.watch();w.observe(observation('a','old-beta'));w.finalize_pending()
        self.assertEqual(EventReader(self.path).iter_events(),[])
        w.observe(observation('a','build-02'));w.observe(observation('a','build-02'));w.finalize_pending()
        reader=EventReader(self.path);events=reader.iter_events();self.assertEqual(len(events),1)
        event=events[0]
        self.assertEqual(event['sequence'],1);self.assertEqual(event['server_id'],'a')
        self.assertEqual(event['server_display_name'],'Everon Alpha')
        self.assertEqual(event['updated'][0]['old_version'],'old-beta')
        self.assertEqual(event['updated'][0]['new_version'],'build-02')
        self.assertLessEqual(event['observed_at'],event['confirmed_at'])
        self.assertEqual(event['provenance']['confirmation_observations'],2)
        self.assertEqual(event['changed_package_sizes']['total_bytes'],100)
        w.finalize_pending();self.assertEqual(events,reader.iter_events())
        event['updated'].clear();self.assertEqual(len(reader.get_event(event['event_id'])['updated']),1)

    def test_metadata_failure_publishes_unknown_not_zero(self):
        api=FakeAPI();api.fail=True;w=self.watch(api=api);self.change(w);w.finalize_pending()
        event=EventReader(self.path).iter_events()[0]
        self.assertIsNone(event['changed_package_sizes']['total_bytes'])
        self.assertEqual(event['updated'][0]['metadata']['status'],'unavailable')
        self.assertEqual(event['changed_package_sizes']['missing_packages'],1)

    def test_zero_package_size_and_captured_names_survive_rename(self):
        api=FakeAPI();api.version=lambda *args:{'size':0,'_retrieved_at':'2026-09-01T00:00:00Z'}
        w=self.watch(api=api);self.change(w);w.finalize_pending()
        before=EventReader(self.path).iter_events()[0]
        with w.db: w.db.execute("UPDATE servers SET label='Renamed'")
        self.assertEqual(before,EventReader(self.path).get_event(before['event_id']))
        self.assertEqual(before['changed_package_sizes']['total_bytes'],0)
        self.assertEqual(before['updated'][0]['metadata']['retrieved_at'],'2026-09-01T00:00:00Z')

    def test_migration_idempotent_no_network_preserves_queued_and_delivered(self):
        frozen=legacy_database(self.path);api=FakeAPI()
        w=self.watch(hook=HOOK1,api=api)
        first=EventReader(self.path).iter_events()
        migrate(w.db);self.assertEqual(first,EventReader(self.path).iter_events())
        self.assertEqual(api.calls,[])
        self.assertEqual(w.row()['id'],'internal');self.assertEqual(w.row()['last_accepted_snapshot_id'],'3')
        self.assertTrue(all(e['provenance']['legacy_backfill'] for e in first))
        self.assertIsNone(first[0]['server_name']);self.assertIsNone(first[0]['server_display_name'])
        sent=[];w.deliver_pending(sent.append);w.deliver_pending(sent.append)
        self.assertEqual(sent,[json.loads(frozen)])
        self.assertEqual(w.db.execute("SELECT payload_json FROM change_events WHERE id='queued'").fetchone()[0],frozen)
        self.assertEqual(w.db.execute('SELECT COUNT(*) FROM webhook_outbox WHERE delivered_at IS NULL').fetchone()[0],0)
        self.assertEqual(api.calls,[])

    def test_migration_transaction_rolls_back_if_backfill_fails(self):
        legacy_database(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            with patch('modrecon.events.publish_stored',side_effect=RuntimeError('injected')):
                with self.assertRaises(RuntimeError): migrate(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertNotIn('server_name',[r[1] for r in db.execute('PRAGMA table_info(change_events)')])
            migrate(db);self.assertEqual(db.execute('SELECT COUNT(*) FROM event_publications').fetchone()[0],2)

    def test_newer_schema_refused_without_modification(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):self.watch()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],999)

    def test_crash_during_publication_rolls_back_and_restart_publishes_once(self):
        api=FakeAPI();w=self.watch(hook=HOOK1,api=api);self.change(w)
        def interrupted(db,event):
            publish_stored(db,event);raise RuntimeError('crash after insert, before commit')
        with patch('modrecon.events.publish_stored',side_effect=interrupted):
            with self.assertRaises(RuntimeError):w.finalize_pending()
        self.assertEqual(EventReader(self.path).iter_events(),[])
        self.assertEqual(w.db.execute('SELECT COUNT(*) FROM webhook_outbox').fetchone()[0],0)
        calls=len(api.calls);w.db.close();self.watches=[]
        w=self.watch(hook=HOOK1,api=api);w.finalize_pending()
        original=EventReader(self.path).iter_events()
        self.assertEqual(len(api.calls),calls)
        w.db.close();self.watches=[]
        w=self.watch(hook=HOOK1,api=api);w.finalize_pending()
        self.assertEqual(EventReader(self.path).iter_events(),original)
        self.assertEqual(w.db.execute('SELECT COUNT(*) FROM webhook_outbox').fetchone()[0],1)

    def test_cursor_late_publication_cannot_skip_and_two_consumers_independent(self):
        a=self.watch('a');b=self.watch('b');self.change(a);self.change(b)
        b.finalize_pending();reader=EventReader(self.path);first=reader.iter_events(0,1)[0]
        a.finalize_pending();later=reader.iter_events(first['sequence'],1)
        self.assertEqual([e['server_id'] for e in later],['a'])
        self.assertEqual(len(reader.iter_events(0)),2)
        self.assertEqual(reader.iter_events(later[0]['sequence']),[])
        for cursor,limit in ((-1,1),(0,0),(True,5),(0,1001)):
            with self.assertRaises(ValueError):reader.iter_events(cursor,limit)

    def test_publication_is_immutable_in_storage(self):
        w=self.watch();self.change(w);w.finalize_pending()
        for sql in ('UPDATE event_publications SET event_json=event_json','DELETE FROM event_publications'):
            with self.assertRaises(sqlite3.IntegrityError),w.db:w.db.execute(sql)

    def test_reader_rejects_unknown_event_schema_instead_of_advancing_cursor(self):
        with self.assertRaises(ValueError):EventReader._decode('{"schema_version":2}')

    def test_group_membership_preserves_exact_version_exceptions(self):
        w=self.watch()
        baseline=observation('a')
        baseline['server']['mods']=[{'id':f'{i:016X}','name':'WCS_Example'+str(i),'version':'old'} for i in range(5)]
        baseline['server']['modCount']=5;w.observe(baseline)
        changed=json.loads(json.dumps(baseline))
        for i,mod in enumerate(changed['server']['mods']):mod['version']='opaque-release' if i<4 else 'exception'
        w.observe(changed);w.observe(changed);w.finalize_pending()
        event=EventReader(self.path).iter_events()[0]
        self.assertEqual(len(event['groups']),1)
        self.assertEqual(len(event['groups'][0]['member_mod_ids']),4)
        self.assertEqual(event['groups'][0]['evidence']['exact_version'],'opaque-release')
        self.assertEqual(len(event['updated']),5)

    def test_cli_migration_is_offline_and_repeatable(self):
        legacy_database(self.path)
        config=self.root/'config.yaml'
        config.write_text('servers:\n  - name: Everon Alpha\n    server_id: a\n    webhook_url: "${MISSING_MIGRATION_WEBHOOK}"\ndatabase_path: state.db\n')
        with patch('watch.urlopen') as network,patch('modrecon.cli.logging.basicConfig'),redirect_stdout(io.StringIO()):
            main(['--config',str(config),'migrate']);main(['--config',str(config),'migrate'])
            network.assert_not_called()
        self.assertEqual(len(EventReader(self.path).iter_events()),2)

    def test_migrated_backup_restores_events_cursor_and_queued_delivery(self):
        legacy_database(self.path);w=self.watch(hook=HOOK1)
        before=EventReader(self.path).iter_events();w.db.close();self.watches=[]
        target=self.root/'backup';backup_state(self.path,target)
        self.assertEqual(EventReader(target/'history.db').iter_events(),before)
        restored=Watch(target/'history.db','a','Everon Alpha',FakeAPI(),HOOK1)
        try:
            sent=[];restored.deliver_pending(sent.append);self.assertEqual(len(sent),1)
        finally:restored.db.close()

    def test_changed_webhook_does_not_silently_reroute_queued_event(self):
        w=self.watch(hook=HOOK1);self.change(w);w.finalize_pending();w.db.close();self.watches=[]
        w=self.watch(hook=HOOK2);sent=[];w.deliver_pending(sent.append)
        self.assertEqual(sent,[])
        self.assertEqual(w.db.execute('SELECT COUNT(*) FROM webhook_outbox WHERE delivered_at IS NULL').fetchone()[0],1)

    def test_blocked_webhook_does_not_delay_polling(self):
        w=self.watch(hook=HOOK1);self.change(w);w.finalize_pending();w.db.close();self.watches=[]
        blocked,release,enough=threading.Event(),threading.Event(),threading.Event();calls=[]
        class Upstream(FakeAPI):
            def server(self,sid):
                calls.append(time.monotonic())
                if len(calls)>=5:enough.set()
                return observation(sid,'build-02')
        class Destination:
            def send(self,*args):blocked.set();release.wait(5);raise RequestFailure(503)
        engine=Engine(Config((Server('a','Everon Alpha',HOOK1),),self.path,poll_interval=.02),api=Upstream(),destinations=Destination())
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(engine.run)
            try:self.assertTrue(blocked.wait(2));self.assertTrue(enough.wait(2))
            finally:release.set();engine.stop.set()
            future.result(timeout=5)
        self.assertGreaterEqual(len(calls),5)

    def test_blocked_enrichment_does_not_delay_polling(self):
        w=self.watch();self.change(w);w.db.close();self.watches=[]
        blocked,release,enough=threading.Event(),threading.Event(),threading.Event();calls=[]
        class Upstream(FakeAPI):
            def server(self,sid):
                calls.append(sid)
                if len(calls)>=5:enough.set()
                return observation(sid,'build-02')
            def version(self,*args):blocked.set();release.wait(5);return {'size':1}
        engine=Engine(Config((Server('a','Everon Alpha',None),),self.path,poll_interval=.02),api=Upstream())
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(engine.run)
            try:self.assertTrue(blocked.wait(2));self.assertTrue(enough.wait(2))
            finally:release.set();engine.stop.set()
            future.result(timeout=5)
        self.assertEqual(len(EventReader(self.path).iter_events()),1)


class IntegrationTests(unittest.TestCase):
    def test_reconcile_unique_monitors_preserve_history_and_keyless_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'state.db';seen=threading.Event();calls=[]
            class Upstream(FakeAPI):
                def server(self,sid):calls.append(sid);seen.set();return observation(sid)
            with Core(path,api=Upstream()) as core:
                spec=Monitor('a','Everon Alpha',120)
                with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:core.apply_monitor_set([spec]),range(2)))
                self.assertTrue(seen.wait(2));core.apply_monitor_set([])
                self.assertEqual(calls,['a']);self.assertEqual(core.get_status('a')['enabled'],0)
                baseline=core.get_status('a')['last_accepted_snapshot_id']
                with self.assertRaises(ValueError):core.apply_monitor_set([spec,spec])
                with self.assertRaises(ValueError):core.apply_monitor_set([Monitor(str(i),'Example') for i in range(6)])
                self.assertEqual(core.iter_events(),[])
            with Core(path,api=Upstream()) as core:
                self.assertEqual(core.get_status('a')['last_accepted_snapshot_id'],baseline)
                self.assertIsNone(core.config.api_key)

    def test_optional_detail_cannot_spend_polling_reserve(self):
        with tempfile.TemporaryDirectory() as root:
            budget=Budget(Path(root)/'budget.db',minute=5)
            api=SharedAPI('https://api.reforgermods.net/v2',budget)
            with patch.object(API,'get',return_value=observation('a')) as network,patch('modrecon.engine.time.sleep'):
                api.verify_server('a')
                with self.assertRaises(RequestFailure):api.verify_server('b')
                with self.assertRaises(RequestFailure):api.get('/servers/c')
                api.server('a')
                self.assertEqual(network.call_count,2)

    def test_distinct_request_purposes(self):
        with tempfile.TemporaryDirectory() as root:
            budget=Budget(Path(root)/'budget.db',minute=10000)
            api=SharedAPI('https://api.reforgermods.net/v2',budget)
            def response(path):
                if path.startswith('/mods/'):return {'data':{'modId':A,'version':'build-A','size':1}}
                if '?' in path:return {'dataset':{'stale':False,'warming':False},'data':[],'meta':{'totalPages':1}}
                return observation('a')
            with patch.object(API,'get',side_effect=response),patch.object(budget,'take',wraps=budget.take) as take:
                api.server('a');api.verify_server('a');api.search('Alpha');api.version(A,'build-A')
                self.assertEqual([c.args[0] for c in take.call_args_list],['poll','verification','discovery','enrichment'])
