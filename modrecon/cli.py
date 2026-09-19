import argparse
from contextlib import closing
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
from .engine import Budget, Engine, SharedAPI, InstanceLock, configured_api, verify_quota
from .text import terminal
from .backup import backup_state


def lookup(api,query,page=1):
    from .discovery import search_servers
    return search_servers(api,query,page)


def add_server(path,api,server_id,name,webhook_env):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',server_id):
        raise ConfigError('Invalid server ID')
    raw=read_raw(path) if path.exists() else {'servers':[],'poll_interval':120,'confirmation_polls':2}
    if not isinstance(raw.get('servers'),list): raise ConfigError('servers must be a list')
    if any(isinstance(s,dict) and s.get('server_id')==server_id for s in raw['servers']):
        raise ConfigError('This server is already configured')
    payload=getattr(api,'verify_server',api.server)(server_id)
    server,dataset=payload.get('server'),payload.get('dataset')
    if (payload.get('status')!='success' or not isinstance(server,dict) or server.get('id')!=server_id
            or not isinstance(server.get('name'),str) or not server['name'].strip()
            or type(server.get('online')) is not bool or type(server.get('present')) is not bool
            or not isinstance(dataset,dict) or any(type(dataset.get(k)) is not bool for k in ('stale','warming'))):
        raise ConfigError('Cannot verify server identity; upstream schema mismatch')
    if dataset['stale'] or dataset['warming']:
        raise ConfigError('Cannot verify server while upstream dataset is stale or warming')
    if not name:
        name=input(f"Display name [{terminal(server['name'], 180)}]: ").strip() or terminal(server['name'], 180)
    if not webhook_env:
        webhook_env=input('Webhook environment-variable name (for example EXAMPLE_WEBHOOK): ').strip()
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
    with closing(sqlite3.connect(config.database_path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        for server in config.servers:
            row=db.execute('SELECT id,last_poll_at,last_poll_status,last_accepted_snapshot_id,candidate_seen_count FROM servers WHERE upstream_server_id=?',(server.server_id,)).fetchone()
            state=dict(row) if row else {}
            state.pop('id',None)
            print(json.dumps({'name':terminal(server.name),'server_id':server.server_id,**state},ensure_ascii=True))


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
    commands.add_parser('quota',help='Verify the effective free or authenticated API allowance')
    backup=commands.add_parser('backup',help='Back up stopped monitoring state and verify integrity')
    backup.add_argument('--output',required=True,help='New private backup directory; must not exist')
    commands.add_parser('migrate',help='Upgrade stopped history using stored facts only; back up first')
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
            api=SharedAPI(base,budget,configured.api_key if raw else os.environ.get('REFORGERMODS_API_KEY'))
            if args.command=='add':
                name,env=add_server(path,api,args.server_id,args.name,args.webhook_env)
                print(f'Added {terminal(name)}. Set {env} in {terminal(path.parent / ".env")}, then run modrecon check. No message sent.')
            else:
                if args.page<1: raise ConfigError('page must be positive')
                matches,pages=lookup(api,args.query,args.page)
                for i,server in enumerate(matches,1):
                    print(f"{i}. {terminal(server['name'])}\n   {terminal(server.get('scenarioName') or 'Scenario unavailable')}\n   {terminal(server.get('players','?'))}/{terminal(server.get('maxPlayers','?'))} players | {'online' if server['online'] else 'offline'}\n   ID: {terminal(server['id'])}\n")
                if not matches: print('No matches on this page. Try a shorter name or the next page.')
                print(f'Upstream search page {args.page}/{max(1,pages)}; use --page for more results.')
        elif args.command=='migrate':
            config=load(path,require_webhooks=False)
            if not config.database_path.is_file(): raise ConfigError('No history database to migrate')
            from .storage import migrate
            with InstanceLock(config.database_path), closing(sqlite3.connect(config.database_path,timeout=30)) as db:
                migrate(db)
            print('History schema verified at version 1; no polling or delivery performed.')
        elif args.command=='backup':
            config=load(path,require_webhooks=False)
            result=backup_state(config.database_path,args.output)
            print(f'Backup verified: {len(result["files"])} databases; complete.json written. No polling or messages sent.')
        elif args.command=='status': status(load(path,require_webhooks=False))
        else:
            config=load(path)
            if args.command=='check': print(f'Valid configuration: {len(config.servers)} servers; polling every {config.poll_interval}s; confirmation after {config.confirmation_polls} observations.')
            elif args.command=='quota': print(json.dumps(verify_quota(configured_api(config),config)))
            else: Engine(config).run(args.once)
    except (ConfigError,RequestFailure,Unsafe,ValueError) as exc:
        # Expected errors are deliberately safe messages, not raw config/HTTP bodies.
        parser.exit(2,f'Error: {terminal(exc)}\n')
    except (OSError,sqlite3.Error):
        parser.exit(2,'Error: local file/database operation failed; check paths and permissions.\n')
