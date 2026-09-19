"""Mod Recon collector; use `modrecon run` for multi-server configuration."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlencode
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

LOG = logging.getLogger('mod-recon')

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return str(uuid.uuid4())

class Unsafe(Exception):
    def __init__(self, status, reason):
        self.status = status
        super().__init__(reason)

class RequestFailure(Exception):
    def __init__(self, status, retry_after=0):
        self.retry_after = retry_after
        super().__init__(f'HTTP/API request failed ({status})')

def retry_seconds(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0

class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RequestFailure('authenticated redirects disabled')


class API:
    """All upstream schema and network behavior stays in this adapter."""
    def __init__(self, base, client, api_key=None):
        self.base = base.rstrip('/')
        if api_key and (self.base != 'https://api.reforgermods.net/v2'
                        or not isinstance(api_key, str) or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in api_key)):
            raise ValueError('API credentials require the official HTTPS V2 endpoint and a valid token')
        self._api_key = api_key
        self.headers = {'User-Agent': client, 'X-API-Client': client}
        self.next_allowed = 0

    def get(self, path):
        if time.time() < self.next_allowed:
            raise RequestFailure('cooldown', self.next_allowed - time.time())
        try:
            headers = dict(self.headers)
            if self._api_key:
                headers['Authorization'] = 'Bearer ' + self._api_key
            request = Request(self.base + path, headers=headers)
            open_request = build_opener(NoCredentialRedirect()).open if self._api_key else urlopen
            with open_request(request, timeout=15) as response:
                if response.status != 200:
                    raise RequestFailure(response.status)
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise RequestFailure('response exceeds size limit')
                result = json.loads(raw)
        except HTTPError as exc:
            delay = retry_seconds(exc.headers.get('Retry-After'))
            self.next_allowed = time.time() + delay
            exc.close()
            raise RequestFailure(exc.code, delay) from None
        except (URLError, TimeoutError, OSError, ValueError, RecursionError):
            raise RequestFailure('network or malformed JSON') from None
        if path == '/rate-limits' and isinstance(result, dict):
            return result
        if not isinstance(result, dict) or result.get('status') != 'success':
            raise RequestFailure('unsuccessful response')
        return result

    def server(self, server_id):
        return self.get('/servers/' + quote(server_id, safe=''))

    def search(self, query, page=1):
        result = self.get('/servers?' + urlencode({'search': query, 'page': page, 'perPage':100, 'includeOffline':'true', 'sort':'name'}))
        dataset, entries, meta = result.get('dataset'), result.get('data'), result.get('meta')
        if not isinstance(dataset,dict) or any(type(dataset.get(k)) is not bool for k in ('stale','warming')):
            raise RequestFailure('search freshness schema mismatch')
        if dataset['stale'] or dataset['warming']:
            raise RequestFailure('search dataset stale or warming')
        if not isinstance(entries,list) or not isinstance(meta,dict) or type(meta.get('totalPages')) is not int:
            raise RequestFailure('search schema mismatch')
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or not isinstance(entry.get('name'),str) or type(entry.get('online')) is not bool:
                raise RequestFailure('search server schema mismatch')
        return entries, meta['totalPages']

    def version(self, mod_id, version):
        result = self.get('/mods/' + quote(mod_id, safe='') + '/versions/' + quote(version, safe=''))
        detail = result.get('data')
        if not isinstance(detail, dict) or detail.get('version') != version or detail.get('modId') != mod_id:
            raise RequestFailure('version schema mismatch')
        size = detail.get('size')
        if size is not None and (type(size) is not int or size < 0):
            raise RequestFailure('invalid package size')
        for field in ('changelog', 'gameVersion', 'createdAt', 'updatedAt'):
            if detail.get(field) is not None and not isinstance(detail[field], str):
                raise RequestFailure('invalid metadata field')
            if isinstance(detail.get(field), str) and len(detail[field].encode('utf-8', errors='surrogatepass')) > 65536:
                raise RequestFailure('metadata field exceeds size limit')
        return detail

def validate(payload, expected_id):
    if not isinstance(payload, dict) or payload.get('status') != 'success':
        raise Unsafe('invalid', 'API envelope changed')
    dataset, server = payload.get('dataset'), payload.get('server')
    if not isinstance(dataset, dict) or not isinstance(server, dict):
        raise Unsafe('invalid', 'Missing dataset/server')
    for field in ('warming', 'stale'):
        if type(dataset.get(field)) is not bool:
            raise Unsafe('invalid', f'Missing/invalid dataset {field}')
    if dataset['warming'] or dataset['stale']:
        raise Unsafe('stale', 'Dataset is warming or stale')
    if server.get('id') != expected_id:
        raise Unsafe('invalid', 'Server identity mismatch; manually verify configured ID')
    for field in ('present', 'online'):
        if type(server.get(field)) is not bool:
            raise Unsafe('invalid', f'Missing/invalid server {field}')
    if not server['present'] or not server['online']:
        raise Unsafe('offline', 'Server absent or offline; manually verify ID if persistent')
    mods = server.get('mods')
    if not isinstance(mods, list) or type(server.get('modCount')) is not int or server['modCount'] != len(mods):
        raise Unsafe('invalid', 'Missing/incomplete reported mod list')
    seen = set()
    for mod in mods:
        if not isinstance(mod, dict) or not isinstance(mod.get('id'), str) or not re.fullmatch(r'[0-9A-Fa-f]{16}', mod['id']):
            raise Unsafe('invalid', 'Invalid Workshop ID')
        mod_id = mod['id'].upper()
        if mod_id in seen:
            raise Unsafe('invalid', 'Duplicate Workshop ID')
        seen.add(mod_id)
        if not isinstance(mod.get('version'), str) or not mod['version'].strip() or any(c in mod['version'] for c in '\r\n'):
            raise Unsafe('invalid', 'Missing/invalid version')
        if mod.get('name') is not None and not isinstance(mod['name'], str):
            raise Unsafe('invalid', 'Invalid mod name')
    normalized = '\n'.join(f"{m['id'].upper()}:{m['version']}" for m in sorted(mods, key=lambda m: m['id'].upper()))
    return dataset, server, hashlib.sha256(normalized.encode()).hexdigest()

class Watch:
    def __init__(self, path, server_id, label, api, webhook=None, donation_url=None, confirmation_polls=2):
        if type(confirmation_polls) is not int or confirmation_polls < 2:
            raise ValueError('confirmation_polls must be at least 2')
        self.confirmation_polls = confirmation_polls
        self.donation_url = donation_url
        if donation_url:
            parsed = urlparse(donation_url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or len(donation_url) > 500 or any(c.isspace() or c in '<>' for c in donation_url):
                raise ValueError('DONATION_URL must be an HTTPS URL of at most 500 characters')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        from importlib.resources import files
        self.db.executescript(files('modrecon').joinpath('schema.sql').read_text(encoding='utf-8'))
        self.api, self.webhook = api, webhook
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO servers(id,upstream_server_id,label,created_at) VALUES(?,?,?,?)', (uid(), server_id, label, now()))
            self.db.execute('UPDATE servers SET label=? WHERE upstream_server_id=?', (label, server_id))
        self.server_id = server_id

    def row(self):
        return self.db.execute('SELECT * FROM servers WHERE upstream_server_id=?', (self.server_id,)).fetchone()

    def clear_candidate(self, row):
        self.db.execute('UPDATE servers SET candidate_snapshot_id=NULL,candidate_seen_count=0 WHERE id=?', (row['id'],))
        if row['candidate_snapshot_id']:
            self.db.execute("DELETE FROM manifest_snapshots WHERE id=? AND state='candidate'", (row['candidate_snapshot_id'],))

    def snapshot(self, row, dataset, server, digest, state):
        snapshot_id = uid()
        self.db.execute('INSERT INTO manifest_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (snapshot_id, row['id'], now(), dataset.get('lastCollectionAt'), dataset.get('snapshotAgeSeconds'), server.get('gameVersion'), server.get('scenarioId'), server.get('scenarioName'), len(server['mods']), digest, state))
        self.db.executemany('INSERT INTO snapshot_mods VALUES(?,?,?,?,?)', [(snapshot_id, m['id'].upper(), m.get('name') or m['id'], m['version'], i) for i, m in enumerate(server['mods'])])
        return snapshot_id

    def mods(self, snapshot_id):
        return {r['mod_id']: dict(r) for r in self.db.execute('SELECT * FROM snapshot_mods WHERE snapshot_id=?', (snapshot_id,))}

    def observe(self, payload):
        row = self.row()
        try:
            dataset, server, digest = validate(payload, self.server_id)
        except Unsafe as exc:
            self.reject(exc.status, str(exc))
            return
        with self.db:
            self.db.execute("UPDATE servers SET last_poll_at=?,last_poll_status='ok',last_error=NULL WHERE id=?", (now(), row['id']))
            accepted = row['last_accepted_snapshot_id']
            if not accepted:
                baseline = self.snapshot(row, dataset, server, digest, 'accepted')
                self.db.execute('UPDATE servers SET last_accepted_snapshot_id=? WHERE id=?', (baseline, row['id']))
                LOG.info('Baseline accepted: %s mods', len(server['mods']))
                return
            accepted_hash = self.db.execute('SELECT manifest_hash FROM manifest_snapshots WHERE id=?', (accepted,)).fetchone()[0]
            if digest == accepted_hash:
                self.clear_candidate(row)
                return
            candidate = row['candidate_snapshot_id']
            candidate_hash = self.db.execute('SELECT manifest_hash FROM manifest_snapshots WHERE id=?', (candidate,)).fetchone() if candidate else None
            if not candidate_hash or candidate_hash[0] != digest:
                self.clear_candidate(row)
                candidate = self.snapshot(row, dataset, server, digest, 'candidate')
                self.db.execute('UPDATE servers SET candidate_snapshot_id=?,candidate_seen_count=1 WHERE id=?', (candidate, row['id']))
                LOG.info('Changed manifest awaits confirmation')
                return
            count = row['candidate_seen_count'] + 1
            if count < self.confirmation_polls:
                self.db.execute('UPDATE servers SET candidate_seen_count=? WHERE id=?', (count, row['id']))
                return
            old, new = self.mods(accepted), self.mods(candidate)
            items = []
            for mod_id in sorted(old.keys() | new.keys()):
                before, after = old.get(mod_id), new.get(mod_id)
                kind = 'added' if before is None else 'removed' if after is None else 'updated' if before['mod_version'] != after['mod_version'] else None
                if kind:
                    items.append((kind, mod_id, (after or before)['mod_name'], before['mod_version'] if before else None, after['mod_version'] if after else None))
            event_id = uid()
            self.db.execute('INSERT INTO change_events(id,server_id,previous_snapshot_id,new_snapshot_id,detected_at,added_count,removed_count,updated_count) VALUES(?,?,?,?,?,?,?,?)', (event_id, row['id'], accepted, candidate, now(), *(sum(i[0] == k for i in items) for k in ('added','removed','updated'))))
            self.db.executemany('INSERT INTO change_items(event_id,change_type,mod_id,mod_name,before_version,after_version,metadata_status) VALUES(?,?,?,?,?,?,?)', [(event_id, *item, 'not_requested') for item in items])
            self.db.execute("UPDATE manifest_snapshots SET state='accepted' WHERE id=?", (candidate,))
            self.db.execute('UPDATE servers SET last_accepted_snapshot_id=?,candidate_snapshot_id=NULL,candidate_seen_count=0 WHERE id=?', (candidate, row['id']))
            LOG.info('Confirmed event %s: %s changed mods', event_id, len(items))

    def reject(self, status, reason):
        with self.db:
            row = self.row()
            self.clear_candidate(row)
            self.db.execute('UPDATE servers SET last_poll_at=?,last_poll_status=?,last_error=? WHERE id=?', (now(), status, reason, row['id']))
        LOG.warning('Poll %s: %s', status, reason)

    def poll(self):
        if not self.row()['enabled']:
            return
        try:
            self.observe(self.api.server(self.server_id))
        except RequestFailure as exc:
            self.reject('error', str(exc))

    def process_pending(self, send=None):
        events = self.db.execute('SELECT * FROM change_events WHERE server_id=? AND delivered_at IS NULL ORDER BY detected_at LIMIT 1', (self.row()['id'],)).fetchall()
        for event in events:
            if event['delivery_next_at'] > time.time():
                break
            if not event['enrichment_done']:
                deadline = time.monotonic() + 30
                items = self.db.execute('SELECT * FROM change_items WHERE event_id=?', (event['id'],)).fetchall()
                for item in items:
                    if item['change_type'] == 'removed' or item['metadata_status'] != 'not_requested':
                        continue
                    try:
                        if time.monotonic() >= deadline:
                            raise RequestFailure('enrichment budget exhausted')
                        detail = self.api.version(item['mod_id'], item['after_version'])
                        with self.db:
                            self.db.execute("UPDATE change_items SET size_bytes=?,changelog=?,game_version=?,created_at=?,updated_at=?,metadata_status='complete' WHERE event_id=? AND mod_id=?", (detail.get('size'), detail.get('changelog'), detail.get('gameVersion'), detail.get('createdAt'), detail.get('updatedAt'), event['id'], item['mod_id']))
                    except RequestFailure:
                        with self.db:
                            self.db.execute("UPDATE change_items SET metadata_status='unavailable' WHERE event_id=? AND mod_id=?", (event['id'], item['mod_id']))
                with self.db:
                    self.db.execute('UPDATE change_events SET enrichment_done=1 WHERE id=?', (event['id'],))
            event = self.db.execute('SELECT * FROM change_events WHERE id=?', (event['id'],)).fetchone()
            payload = json.loads(event['payload_json']) if event['payload_json'] else self.message(event)
            with self.db:
                self.db.execute('UPDATE change_events SET payload_json=? WHERE id=?', (json.dumps(payload), event['id']))
            if not send and not self.webhook:
                LOG.info('Event %s queued; webhook not configured', event['id'])
                break
            try:
                (send or self.send)(payload)
                with self.db:
                    self.db.execute('UPDATE change_events SET delivered_at=?,delivery_error=NULL WHERE id=?', (now(), event['id']))
            except RequestFailure as exc:
                with self.db:
                    self.db.execute('UPDATE change_events SET delivery_error=?,delivery_next_at=? WHERE id=?', (str(exc), time.time() + max(120, exc.retry_after), event['id']))
                LOG.warning('Discord delivery failed; queued for retry')
                break

    def message(self, event):
        from presentation import render_message
        items = self.db.execute('SELECT * FROM change_items WHERE event_id=? ORDER BY change_type,mod_id', (event['id'],)).fetchall()
        return render_message(self.row()['label'], event, items, self.donation_url)

    def send(self, payload):
        from presentation import encode_webhook
        body, content_type = encode_webhook(payload)
        request = Request(self.webhook + ('&' if '?' in self.webhook else '?') + 'wait=true', data=body, headers={'Content-Type': content_type, 'User-Agent': 'mod-recon/0.2.9'}, method='POST')
        try:
            with urlopen(request, timeout=15) as response:
                if response.status not in (200, 204):
                    raise RequestFailure(response.status)
        except HTTPError as exc:
            raise RequestFailure(exc.code, retry_seconds(exc.headers.get('Retry-After'))) from None
        except (URLError, TimeoutError, OSError):
            raise RequestFailure('Discord network failure') from None

def load_env(path):
    if Path(path).exists():
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    load_env(args.env_file)
    server_id = os.getenv('REFORGER_SERVER_ID')
    if not server_id:
        parser.error('Set REFORGER_SERVER_ID in .env or the environment')
    interval = float(os.getenv('POLL_INTERVAL_SECONDS', '120'))
    if interval < 120:
        parser.error('Polling interval must be at least 120 seconds')
    webhook = os.getenv('DISCORD_WEBHOOK_URL') or None
    if webhook:
        parsed = urlparse(webhook)
        if parsed.scheme != 'https' or parsed.hostname not in ('discord.com','discordapp.com') or not parsed.path.startswith('/api/webhooks/') or parsed.fragment:
            parser.error('DISCORD_WEBHOOK_URL must be a Discord HTTPS webhook URL')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    watcher = Watch(os.getenv('DATABASE_PATH','./data/reforger-watch.db'), server_id, os.getenv('SERVER_LABEL',server_id), API(os.getenv('REFORGERMODS_BASE_URL','https://api.reforgermods.net/v2'),os.getenv('CLIENT_NAME','mod-recon/0.2.0')), webhook, os.getenv('DONATION_URL') or None)
    try:
        if args.status:
            print(json.dumps(dict(watcher.row()), indent=2))
            print('Pending events:', watcher.db.execute('SELECT COUNT(*) FROM change_events WHERE delivered_at IS NULL').fetchone()[0])
            return
        while True:
            started = time.monotonic()
            watcher.poll()
            watcher.process_pending()
            if args.once:
                break
            time.sleep(max(0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        LOG.info('Stopped')
    finally:
        watcher.db.close()

if __name__ == '__main__':
    main()
