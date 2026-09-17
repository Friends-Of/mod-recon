import copy
from email import policy
from email.parser import BytesParser
import json
import unittest

from presentation import encode_webhook, render_message

EVENT = {'id': 'test-event', 'detected_at': '2026-09-17T14:48:17+00:00'}

def item(n, name, version='8.2.0', kind='updated'):
    return dict(mod_id=f'{n:016X}', mod_name=name, change_type=kind,
                before_version=None if kind == 'added' else '8.1.0',
                after_version=None if kind == 'removed' else version,
                size_bytes=None if kind == 'removed' else 1024**3,
                changelog='No buildlog items provided.', metadata_status='complete')

class PresentationTests(unittest.TestCase):
    def test_large_deployment_groups_versions_and_preserves_exception(self):
        items = [item(n, f'WCS_Package{n}') for n in range(38)]
        items += [item(38,'WCS_M1A1','8.2.1')]
        items += [item(39,'RHS - Content Pack 01','0.16.5208'), item(40,'RHS - Content Pack 02','0.16.5208'), item(41,'RHS - Status Quo','0.16.5208')]
        items += [item(n,f'WCS_New{n}',kind='added') for n in range(42,45)]
        items += [item(45,'ACE Cook-Off Dev',kind='removed')]
        for i in items[41:45]: i['size_bytes'] = None
        original = copy.deepcopy(items)
        msg = render_message('WCS NA7', EVENT, items, 'https://example.com/support')
        description = msg['embeds'][0]['description']
        self.assertIn('+3 Added · ↑42 Updated · −1 Removed', description)
        self.assertIn('38 of 39 updated WCS packages → **8.2.0**', description)
        self.assertIn('3 of 3 updated RHS packages → **0.16.5208**', description)
        self.assertIn('M1A1 → **8.2.1**', description)
        self.assertIn('41.00 GiB across 41 changed packages', description)
        self.assertIn('Size unavailable for 4', description)
        self.assertIn('Support Reforger Watch', description)
        self.assertNotIn('No buildlog',description)
        self.assertLess(len(description),1800)
        report = msg['_text_attachment']['text']
        for i in items: self.assertIn(i['mod_id'],report)
        self.assertIn('No buildlog',report)
        self.assertEqual(items,original)
    def test_small_update_lists_exact_before_and_after(self):
        msg = render_message('NA7',EVENT,[item(1,'Package')])
        self.assertIn('8.1.0 → 8.2.0',msg['embeds'][0]['description'])
        self.assertNotIn('Support Reforger Watch',str(msg))
    def test_no_false_majority(self):
        items = [item(n,f'WCS_{n}',str(n)) for n in range(15)]
        msg = render_message('NA7',EVENT,items)
        self.assertNotIn('updated WCS packages',msg['embeds'][0]['description'])
        self.assertIn('12 other updated packages',msg['embeds'][0]['description'])
    def test_multipart_round_trip_and_legacy_payload(self):
        msg = render_message('NA7',EVENT,[item(1,'Package')])
        original = copy.deepcopy(msg)
        body, content_type = encode_webhook(msg)
        mime = BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
        parts=list(mime.iter_parts())
        wire=json.loads(parts[0].get_payload(decode=True))
        self.assertNotIn('_text_attachment',wire)
        self.assertEqual(wire['allowed_mentions'],{'parse':[]})
        self.assertEqual(parts[1].get_filename(),'changes-test-event.txt')
        self.assertEqual(parts[1].get_payload(decode=True).decode(),msg['_text_attachment']['text'])
        self.assertEqual(msg,original)
        old={'content':'Existing queued notification'}
        body, content_type=encode_webhook(old)
        self.assertEqual(content_type,'application/json')
        self.assertEqual(json.loads(body),old)

if __name__ == '__main__': unittest.main()
