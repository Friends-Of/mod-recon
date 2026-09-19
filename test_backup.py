from contextlib import closing, redirect_stdout, redirect_stderr
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modrecon.backup import backup_state, _copy_database
from modrecon.cli import main
from modrecon.engine import InstanceLock
from test_watch import FakeAPI, payload, A
from watch import Watch


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.source=self.root/'state.db'
        self.output=self.root/'backup'
        w=Watch(self.source,'example','Everon Alpha',FakeAPI())
        for version in ('1','2','2','3'):
            w.observe(payload_for(version))
        w.db.close()
        with closing(sqlite3.connect(str(self.source)+'.api.db')) as db, db:
            db.execute('CREATE TABLE requests(at REAL)')
            db.execute('INSERT INTO requests VALUES(123)')

    def tearDown(self): self.tmp.cleanup()

    def test_restore_preserves_history_pending_events_candidate_and_budget(self):
        before=self.source.read_bytes()
        manifest=backup_state(self.source,self.output)
        self.assertEqual(self.source.read_bytes(),before)
        for record in manifest['files']:
            copied=self.output/record['file']
            self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(),record['sha256'])
        with closing(sqlite3.connect(self.source)) as src, closing(sqlite3.connect(self.output/'history.db')) as dst:
            self.assertEqual(list(src.iterdump()),list(dst.iterdump()))
        restored=Watch(self.output/'history.db','example','Everon Alpha',FakeAPI())
        try:
            self.assertEqual(restored.row()['candidate_seen_count'],1)
            self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM change_events WHERE delivered_at IS NULL').fetchone()[0],1)
        finally: restored.db.close()
        with closing(sqlite3.connect(self.output/'api-budget.db')) as db:
            self.assertEqual(db.execute('SELECT at FROM requests').fetchall(),[(123,)])

    def test_refuses_running_instance_existing_output_and_missing_source(self):
        with InstanceLock(self.source), self.assertRaises(ValueError):
            backup_state(self.source,self.output)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        sentinel=self.output/'keep.txt';sentinel.write_text('keep')
        with self.assertRaises(FileExistsError): backup_state(self.source,self.output)
        self.assertEqual(sentinel.read_text(),'keep')
        missing=self.root/'missing.db'
        with self.assertRaises(ValueError): backup_state(missing,self.root/'other')
        self.assertFalse(missing.exists())

    def test_partial_failure_never_marks_backup_complete(self):
        def fail_budget(source,dest):
            if dest.name=='api-budget.db': raise sqlite3.DatabaseError('test failure')
            return _copy_database(source,dest)
        with patch('modrecon.backup._copy_database',side_effect=fail_budget):
            with self.assertRaises(sqlite3.DatabaseError): backup_state(self.source,self.output)
        self.assertFalse((self.output/'complete.json').exists())

    def test_cli_needs_no_webhook_secret_and_failed_backup_exits_nonzero(self):
        config=self.root/'config.yaml'
        config.write_text('servers:\n  - name: Everon Alpha\n    server_id: example\n    webhook_url: "${UNSET_BACKUP_WEBHOOK}"\ndatabase_path: state.db\n')
        with patch.dict(os.environ,{},clear=True), patch('modrecon.cli.Engine') as engine, patch('watch.urlopen') as network, patch('modrecon.cli.logging.basicConfig'), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(['--config',str(config),'backup','--output',str(self.output)])
            self.assertTrue((self.output/'complete.json').exists())
            with self.assertRaises(SystemExit) as failed:
                main(['--config',str(config),'backup','--output',str(self.output)])
            self.assertEqual(failed.exception.code,2)
            engine.assert_not_called();network.assert_not_called()


def payload_for(version):
    value=payload([(A,version,'Example mod')])
    value['server']['id']='example'
    return value
