import argparse
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

import yaml
from watch import RequestFailure, Unsafe, load_env, validate
from .config import ConfigError, load, parse, read_raw
from .engine import Budget, Engine, SharedAPI


def lookup(api,query,page=1):
    # Search one discriminating token upstream; filter punctuation-insensitively.
    # This matches "WCS NA7" to "[NA7] W.C.S." without selecting a server for users.
    tokens=re.findall(r'[a-z0-9]+',query.lower().replace('w.c.s.','wcs'))
    if not tokens: raise ConfigError('Enter a server name to search')
    seed=max((t for t in tokens if t!='wcs'),key=len,default='W.C.S.')
    entries,pages=api.search(seed,page)
    def words(text): return set(re.findall(r'[a-z0-9]+',text.lower().replace('w.c.s.','wcs')))
    return [s for s in entries if all(t in words(s['name']) for t in tokens)],pages


def add_server(path,api,server_id,name,webhook_env):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',server_id):
        raise ConfigError('Invalid server ID')
    raw=read_raw(path) if path.exists() else {'servers':[],'poll_interval':120,'confirmation_polls':2}
    if not isinstance(raw.get('servers'),list): raise ConfigError('servers must be a list')
    if any(isinstance(s,dict) and s.get('server_id')==server_id for s in raw['servers']):
        raise ConfigError('This server is already configured')
    payload=api.server(server_id)
    server,dataset=payload.get('server'),payload.get('dataset')
    if (payload.get('status')!='success' or not isinstance(server,dict) or server.get('id')!=server_id
            or not isinstance(server.get('name'),str) or not server['name'].strip()
            or type(server.get('online')) is not bool or type(server.get('present')) is not bool
            or not isinstance(dataset,dict) or any(type(dataset.get(k)) is not bool for k in ('stale','warming'))):
        raise ConfigError('Cannot verify server identity; upstream schema mismatch')
    if dataset['stale'] or dataset['warming']:
        raise ConfigError('Cannot verify server while upstream dataset is stale or warming')
    if not name:
        name=input(f"Display name [{server['name']}]: ").strip() or server['name']
    if not webhook_env:
        webhook_env=input('Webhook environment-variable name (for example WCS_WEBHOOK): ').strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',webhook_env):
        raise ConfigError('Use an environment-variable name, not the webhook secret')
    raw['servers'].append({'name':name,'server_id':server_id,'webhook_url':'${'+webhook_env+'}'})
    parse(raw,path,require_webhooks=False)
    # Serialize references, never resolved secrets; atomic replacement avoids partial YAML.
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.modrecon-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            yaml.safe_dump(raw,handle,sort_keys=False,allow_unicode=True)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return name,webhook_env


def status(config):
    if not config.database_path.exists():
        print('No history yet. Run modrecon run.'); return
    with sqlite3.connect(config.database_path.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        for server in config.servers:
            row=db.execute('SELECT id,last_poll_at,last_poll_status,last_accepted_snapshot_id,candidate_seen_count FROM servers WHERE upstream_server_id=?',(server.server_id,)).fetchone()
            state=dict(row) if row else {}
            state.pop('id',None)
            print(json.dumps({'name':server.name,'server_id':server.server_id,**state},ensure_ascii=False))


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(prog='modrecon',description='Monitor public Arma Reforger mod manifests.')
    parser.add_argument('--config',default='modrecon.yaml',help='YAML config path (default: modrecon.yaml)')
    commands=parser.add_subparsers(dest='command',required=True)
    find=commands.add_parser('find',help='Find matching public servers')
    find.add_argument('query'); find.add_argument('--page',type=int,default=1)
    add=commands.add_parser('add',help='Verify a server ID and add it to configuration')
    add.add_argument('server_id'); add.add_argument('--name'); add.add_argument('--webhook-env')
    run=commands.add_parser('run',help='Monitor all configured servers')
    run.add_argument('--once',action='store_true')
    commands.add_parser('check',help='Validate configuration without polling or posting')
    commands.add_parser('status',help='Show stored status without polling or posting')
    args=parser.parse_args(argv)
    path=Path(args.config).resolve()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(threadName)s %(levelname)s %(message)s')
    try:
        load_env(path.parent/'.env')
        if args.command in ('find','add'):
            raw=read_raw(path) if path.exists() else {}
            if raw:
                configured=parse(raw,path,require_webhooks=False)
                base=configured.base_url
                budget_path=str(configured.database_path)+'.api.db'
                budget=Budget(budget_path,configured.requests_per_minute,configured.requests_per_day)
            else:
                base='https://api.reforgermods.net/v2'
                budget=Budget(path.parent/'data/mod-recon.db.api.db')
            api=SharedAPI(base,budget)
            if args.command=='add':
                name,env=add_server(path,api,args.server_id,args.name,args.webhook_env)
                print(f'Added {name}. Set {env} in {path.parent / ".env"}, then run modrecon check. No message sent.')
            else:
                if args.page<1: raise ConfigError('page must be positive')
                matches,pages=lookup(api,args.query,args.page)
                for i,server in enumerate(matches,1):
                    def clean(value): return ' '.join(str(value).split())
                    print(f"{i}. {clean(server['name'])}\n   {clean(server.get('scenarioName') or 'Scenario unavailable')}\n   {server.get('players','?')}/{server.get('maxPlayers','?')} players | {'online' if server['online'] else 'offline'}\n   ID: {server['id']}\n")
                if not matches: print('No matches on this page. Try a shorter name or the next page.')
                print(f'Upstream search page {args.page}/{max(1,pages)}; use --page for more results.')
        elif args.command=='status': status(load(path,require_webhooks=False))
        else:
            config=load(path)
            if args.command=='check': print(f'Valid configuration: {len(config.servers)} servers; polling every {config.poll_interval}s; confirmation after {config.confirmation_polls} observations.')
            else: Engine(config).run(args.once)
    except (ConfigError,RequestFailure,Unsafe,ValueError) as exc:
        # Expected errors are deliberately safe messages, not raw config/HTTP bodies.
        parser.exit(2,f'Error: {exc}\n')
    except (OSError,sqlite3.Error):
        parser.exit(2,'Error: local file/database operation failed; check paths and permissions.\n')
