"""Normalized immutable event publications and evidence-based grouping."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def group_items(items):
    families=defaultdict(list)
    for item in items:
        if item['change_type']!='updated': continue
        name=item['mod_name'] or ''
        family='WCS' if name.startswith('WCS_') else 'RHS' if name.startswith('RHS - ') else None
        if family: families[family].append(item)
    groups=[]
    for family,members in sorted(families.items()):
        if len(members)<3: continue
        version,count=Counter(i['after_version'] for i in members).most_common(1)[0]
        if count<3 or count<=len(members)/2: continue
        groups.append({'rule_id':'literal-prefix-exact-version','rule_version':1,
                       'family':family,'version':version,'family_updated_count':len(members),
                       'member_mod_ids':sorted(i['mod_id'] for i in members if i['after_version']==version),
                       'evidence':{'name_prefix':'WCS_' if family=='WCS' else 'RHS - ', 'exact_version':version}})
    return groups


def document(db,event,sequence):
    server=db.execute('SELECT * FROM servers WHERE id=?',(event['server_id'],)).fetchone()
    previous=db.execute('SELECT * FROM manifest_snapshots WHERE id=?',(event['previous_snapshot_id'],)).fetchone()
    current=db.execute('SELECT * FROM manifest_snapshots WHERE id=?',(event['new_snapshot_id'],)).fetchone()
    items=[dict(r) for r in db.execute('SELECT * FROM change_items WHERE event_id=? ORDER BY mod_id',(event['id'],))]
    legacy=bool(event['legacy_backfill'])
    unknown=[]
    result={'schema_version':1,'event_id':event['id'],'sequence':sequence,
            'server_id':server['upstream_server_id'],'server_name':event['server_name'],
            'server_display_name':event['display_name'],
            'observed_at':current['observed_at'],'confirmed_at':event['detected_at'],
            'previous_manifest':{'snapshot_id':previous['id'],'sha256':previous['manifest_hash']},
            'new_manifest':{'snapshot_id':current['id'],'sha256':current['manifest_hash']},
            'added':[],'removed':[],'updated':[],'groups':group_items(items)}
    for field in ('server_name','display_name','confirmation_collected_at','confirmation_observations'):
        if event[field] is None: unknown.append(field)
    for item in items:
        status={'complete':'available'}.get(item['metadata_status'],item['metadata_status'])
        result[item['change_type']].append({
            'mod_id':item['mod_id'],'name':item['mod_name'],
            'old_version':item['before_version'],'new_version':item['after_version'],
            'old_package_size_bytes':None,'new_package_size_bytes':item['size_bytes'] if item['change_type']!='removed' else None,
            'metadata':{'status':status,'source':'reforgermods.v2.mod_version' if status!='not_requested' else None,
                        'requested_version':item['after_version'], 'retrieved_at':item['metadata_retrieved_at'],
                        'reason': 'metadata_unavailable' if status=='unavailable' else None,
                        'changelog':item['changelog'],'game_version':item['game_version'],
                        'upstream_created_at':item['created_at'],'upstream_updated_at':item['updated_at']}})
        if status=='available' and item['metadata_retrieved_at'] is None:
            unknown.append('metadata_retrieved_at:'+item['mod_id'])
    eligible=result['added']+result['updated']
    sizes=[i['new_package_size_bytes'] for i in eligible if i['new_package_size_bytes'] is not None]
    result['changed_package_sizes']={'total_bytes':sum(sizes) if sizes else None,'known_packages':len(sizes),
                                   'eligible_packages':len(eligible),'missing_packages':len(eligible)-len(sizes),
                                   'basis':'added_and_updated_new_versions'}
    result['provenance']={'manifest_source':'reforgermods.v2.server_detail','upstream_server_id':server['upstream_server_id'],
                          'first_upstream_collected_at':current['upstream_collected_at'],
                          'confirmation_upstream_collected_at':event['confirmation_collected_at'],
                          'confirmation_observations':event['confirmation_observations'],
                          'legacy_backfill':legacy,'unknown_fields':unknown}
    return result


def publish_stored(db,event):
    """Called within a write transaction; publication and local outbox are atomic."""
    existing=db.execute('SELECT sequence FROM event_publications WHERE event_id=?',(event['id'],)).fetchone()
    if existing: return existing[0]
    sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM event_publications').fetchone()[0]
    payload=document(db,event,sequence)
    db.execute('INSERT INTO event_publications(sequence,event_id,schema_version,published_at,event_json) VALUES(?,?,?,?,?)',
               (sequence,event['id'],1,datetime.now(timezone.utc).isoformat(),json.dumps(payload,sort_keys=True,ensure_ascii=True)))
    if event['destination_key']:
        db.execute('INSERT OR IGNORE INTO webhook_outbox(event_id,destination_key,payload_json,delivered_at,next_attempt,error) VALUES(?,?,?,?,?,?)',
                   (event['id'],event['destination_key'],event['payload_json'],event['delivered_at'],event['delivery_next_at'],event['delivery_error']))
    return sequence


class EventReader:
    """Read-only, bounded cursor consumption. Returned dicts are detached copies."""
    def __init__(self,path): self.path=Path(path).resolve()
    @staticmethod
    def _decode(raw):
        event=json.loads(raw)
        if event.get('schema_version')!=1: raise ValueError('Unsupported ChangeEvent schema version')
        return event
    def iter_events(self,after_sequence=0,limit=100):
        if type(after_sequence) is not int or after_sequence<0 or type(limit) is not int or not 1<=limit<=1000:
            raise ValueError('Use a nonnegative cursor and limit from 1 to 1000')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            return [self._decode(r[0]) for r in db.execute('SELECT event_json FROM event_publications WHERE sequence>? ORDER BY sequence LIMIT ?',(after_sequence,limit))]
    def get_event(self,event_id):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT event_json FROM event_publications WHERE event_id=?',(event_id,)).fetchone()
            return self._decode(row[0]) if row else None
