"""Delivery adapter: consumes published events, never observes or enriches."""
import json
import logging
import time

from watch import RequestFailure, now

LOG=logging.getLogger('mod-recon')


def deliver_one(watch,send=None):
    if not watch.destination_key or (not send and not watch.webhook): return
    row=watch.db.execute('SELECT o.*,p.event_json FROM webhook_outbox o JOIN event_publications p ON p.event_id=o.event_id JOIN change_events e ON e.id=o.event_id WHERE e.server_id=? AND o.destination_key=? AND o.delivered_at IS NULL ORDER BY p.sequence LIMIT 1',(watch.row()['id'],watch.destination_key)).fetchone()
    if not row or row['next_attempt']>time.time(): return
    from presentation import render_change_event
    payload=json.loads(row['payload_json']) if row['payload_json'] else render_change_event(json.loads(row['event_json']),watch.donation_url,watch.row()['label'])
    with watch.db:
        encoded=row['payload_json'] or json.dumps(payload)
        watch.db.execute('UPDATE webhook_outbox SET payload_json=? WHERE event_id=? AND destination_key=?',(encoded,row['event_id'],watch.destination_key))
        watch.db.execute('UPDATE change_events SET payload_json=? WHERE id=?',(encoded,row['event_id']))
    try:
        (send or watch.send)(payload)
        with watch.db:
            stamp=now()
            watch.db.execute('UPDATE webhook_outbox SET delivered_at=?,error=NULL WHERE event_id=? AND destination_key=?',(stamp,row['event_id'],watch.destination_key))
            watch.db.execute('UPDATE change_events SET delivered_at=?,delivery_error=NULL WHERE id=?',(stamp,row['event_id']))
    except RequestFailure as exc:
        delay=time.time()+max(120,exc.retry_after)
        with watch.db:
            watch.db.execute('UPDATE webhook_outbox SET error=?,next_attempt=? WHERE event_id=? AND destination_key=?',(str(exc),delay,row['event_id'],watch.destination_key))
            watch.db.execute('UPDATE change_events SET delivery_error=?,delivery_next_at=? WHERE id=?',(str(exc),delay,row['event_id']))
        LOG.warning('Discord delivery failed; queued for retry')
