import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from modrecon.config import Config, ConfigError, Server, read_raw
from modrecon.text import terminal, TRUNCATED
from presentation import MAX_ATTACHMENT_BYTES, encode_webhook, render_message
from test_presentation import EVENT, item
from test_watch import FakeAPI, payload, A
from watch import API, RequestFailure, Watch


class SecurityTests(unittest.TestCase):
    def test_controls_cannot_reach_terminal(self):
        text = terminal('Server\x1b[2J\x1b]52;c;secret\x07\r\u202eevil\x9b31m')
        for control in ('\x1b', '\x07', '\r', '\u202e', '\x9b'):
            self.assertNotIn(control, text)
        self.assertEqual(terminal('a' * 1000), 'a' * 300)

    def test_configuration_repr_hides_webhook(self):
        server = Server('server', 'Name', 'https://discord.com/api/webhooks/123/SECRET')
        self.assertNotIn('SECRET', repr(server))
        self.assertNotIn('SECRET', repr(Config((server,), Path('test.db'))))

    def test_oversized_report_is_bounded_without_mutating_items(self):
        items = [item(n, 'Package') for n in range(80)]
        for entry in items:
            entry['changelog'] = '界' * 100000
        original = copy.deepcopy(items)
        message = render_message('NA7', EVENT, items)
        report = message['_text_attachment']['text']
        self.assertLessEqual(len(report.encode()), MAX_ATTACHMENT_BYTES)
        self.assertIn(TRUNCATED, report)
        self.assertIn('oversized text shortened', message['embeds'][0]['description'])
        self.assertEqual(items, original)
        body, _ = encode_webhook(message)
        self.assertLess(len(body), MAX_ATTACHMENT_BYTES + 20000)

    def test_old_oversized_queue_payload_is_bounded_at_send_boundary(self):
        message = render_message('NA7', EVENT, [item(1, 'Package')])
        message['_text_attachment']['text'] = '界' * 1000000
        original = copy.deepcopy(message)
        body, _ = encode_webhook(message)
        self.assertLess(len(body), MAX_ATTACHMENT_BYTES + 20000)
        self.assertIn(TRUNCATED.encode(), body)
        self.assertEqual(message, original)

    def test_oversized_response_is_rejected_before_json_parsing(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b' ' * 4000001
        with patch('watch.urlopen', return_value=response), patch('watch.json.loads') as parse:
            with self.assertRaises(RequestFailure): API('https://example.com', 'test').get('/test')
            parse.assert_not_called()

    def test_oversized_metadata_is_unavailable_not_silently_truncated(self):
        api = API('https://example.com', 'test')
        with patch.object(api, 'get', return_value={'data': {'modId': A, 'version': '2', 'changelog': 'x'*65537}}):
            with self.assertRaises(RequestFailure): api.version(A, '2')

    def test_unsafe_yaml_errors_do_not_echo_secrets(self):
        examples = ['secret: &x SECRET\ncopy: *x', 'key: !!python/object/apply:os.system [SECRET]',
                    'secret: SECRET\nsecret: SECRET', 'x' * 262145, '[' * 2000 + 'SECRET']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            for example in examples:
                path.write_text(example, encoding='utf-8')
                with self.assertRaises(ConfigError) as error: read_raw(path)
                self.assertNotIn('SECRET', str(error.exception))

    def test_large_saved_event_delivers_and_next_event_can_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            watch = Watch(Path(directory)/'history.db', 'server', 'NA7', FakeAPI())
            try:
                for version in ('1', '2', '2', '3', '3'):
                    observation = payload([(A, version, 'Example')])
                    observation['server']['id'] = 'server'
                    watch.observe(observation)
                rows = watch.db.execute('SELECT * FROM change_events ORDER BY detected_at').fetchall()
                self.assertEqual(len(rows), 2)
                message = watch.message(rows[0])
                message['_text_attachment']['text'] = 'x' * 16000000
                with watch.db:
                    watch.db.execute('UPDATE change_events SET payload_json=?, enrichment_done=1 WHERE id=?',
                                     (json.dumps(message), rows[0]['id']))
                sent = []
                def deliver(message):
                    body, _ = encode_webhook(message)
                    self.assertLess(len(body), MAX_ATTACHMENT_BYTES + 20000)
                    sent.append(body)
                watch.process_pending(deliver)
                watch.process_pending(deliver)
                self.assertEqual(len(sent), 2)
                self.assertEqual(watch.db.execute('SELECT COUNT(*) FROM change_events WHERE delivered_at IS NULL').fetchone()[0], 0)
            finally:
                watch.db.close()
