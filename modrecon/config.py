"""Strict, file-based configuration. Secret values never appear in errors."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import yaml


class ConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ConfigError('Configuration keys must be unique strings')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def read_raw(path):
    try:
        with Path(path).open('rb') as handle:
            contents = handle.read(262145)
        if len(contents) > 262144:
            raise ConfigError('Configuration exceeds 256 KiB size limit')
        # Configuration needs neither aliases nor arbitrary object construction.
        for token in yaml.scan(contents):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise ConfigError('YAML anchors and aliases are not supported')
        raw = yaml.load(contents.decode('utf-8-sig'), Loader=UniqueLoader)
    except (yaml.YAMLError, OSError, UnicodeError, RecursionError):
        raise ConfigError('Cannot read configuration; check file path and YAML syntax') from None
    if not isinstance(raw, dict):
        raise ConfigError('Configuration must be a YAML mapping')
    return raw


def resolve(value, field):
    if not isinstance(value, str):
        raise ConfigError(f'{field} must be a string')
    def replacement(match):
        name = match[1]
        if not os.environ.get(name):
            raise ConfigError(f'Missing environment variable {name}')
        return os.environ[name]
    return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', replacement, value)


def webhook_valid(value):
    p = urlparse(value)
    return (p.scheme == 'https' and p.netloc in ('discord.com', 'discordapp.com')
            and re.fullmatch(r'/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+', p.path)
            and not p.query and not p.fragment)


@dataclass(frozen=True)
class Server:
    server_id: str
    name: str
    webhook_url: str = field(repr=False)


@dataclass(frozen=True)
class Config:
    servers: tuple
    database_path: Path
    poll_interval: int = 120
    confirmation_polls: int = 2
    base_url: str = 'https://api.reforgermods.net/v2'
    donation_url: str | None = None
    requests_per_minute: int = 50
    requests_per_day: int = 5000


def parse(raw, path, require_webhooks=True):
    allowed = {'servers','database_path','poll_interval','confirmation_polls','base_url','donation_url','requests_per_minute','requests_per_day'}
    if set(raw) - allowed:
        raise ConfigError('Unknown top-level configuration field')
    values = {}
    for key, default, minimum in [('poll_interval',120,120),('confirmation_polls',2,2),('requests_per_minute',50,1),('requests_per_day',5000,1)]:
        value = raw.get(key, default)
        if type(value) is not int or value < minimum:
            raise ConfigError(f'{key} must be an integer of at least {minimum}')
        values[key] = value
    servers = raw.get('servers')
    if not isinstance(servers, list) or not servers:
        raise ConfigError('Configure at least one server; use modrecon add')
    seen, entries = set(), []
    for index, server in enumerate(servers,1):
        if not isinstance(server,dict) or set(server) != {'name','server_id','webhook_url'}:
            raise ConfigError(f'Server {index} needs name, server_id, and webhook_url only')
        sid, name = server['server_id'], server['name']
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',sid):
            raise ConfigError(f'Server {index} has an invalid ID')
        if sid in seen:
            raise ConfigError('Duplicate server IDs are not supported')
        seen.add(sid)
        if not isinstance(name,str) or not name.strip() or len(name)>180:
            raise ConfigError(f'Server {index} needs a name of 1–180 characters')
        webhook = server['webhook_url']
        if require_webhooks:
            webhook = resolve(webhook,f'Server {index} webhook_url')
            if not webhook_valid(webhook):
                raise ConfigError(f'Server {index} requires a valid Discord webhook URL')
        elif not isinstance(webhook,str) or not (webhook_valid(webhook) or re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}',webhook)):
            raise ConfigError(f'Server {index} requires a webhook URL or environment reference')
        entries.append(Server(sid,name,webhook))
    base = raw.get('base_url','https://api.reforgermods.net/v2')
    if not isinstance(base,str) or urlparse(base).scheme != 'https' or not urlparse(base).hostname or urlparse(base).username:
        raise ConfigError('base_url must be an HTTPS URL without credentials')
    donation = raw.get('donation_url') or None
    if donation:
        donation = resolve(donation,'donation_url')
        p=urlparse(donation)
        if p.scheme!='https' or not p.hostname or p.username or len(donation)>500 or any(c.isspace() or c in '<>' for c in donation):
            raise ConfigError('donation_url must be an HTTPS URL of at most 500 characters')
    database = raw.get('database_path','data/mod-recon.db')
    if not isinstance(database,str) or not database.strip():
        raise ConfigError('database_path must be a nonempty path')
    database = (Path(path).resolve().parent / database).resolve()
    # Reserve 20% of the daily allowance for enrichment and discovery.
    if len(entries)*86400/values['poll_interval'] > values['requests_per_day']*.8:
        raise ConfigError('Polling exceeds the daily request budget; increase poll_interval or configure your verified API allowance')
    if len(entries)*60/values['poll_interval'] > values['requests_per_minute']*.8:
        raise ConfigError('Polling exceeds the minute request budget; increase poll_interval or configure your verified API allowance')
    return Config(tuple(entries),database,base_url=base,donation_url=donation,**values)


def load(path, require_webhooks=True):
    return parse(read_raw(path),path,require_webhooks)
