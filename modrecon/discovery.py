"""Human-readable server lookup shared by CLI and library consumers."""
import re
from .config import ConfigError


def search_servers(api,query,page=1):
    if not isinstance(query,str) or not 1<=len(query)<=180 or type(page) is not int or page<1:
        raise ConfigError('Use a search string of 1-180 characters and a positive page')
    normalized=re.sub(r'(?<=[a-z])\.(?=[a-z])','',query.lower())
    tokens=re.findall(r'[a-z0-9]+',normalized)
    if not tokens: raise ConfigError('Enter a server name to search')
    seed=max(tokens,key=lambda token:(any(c.isdigit() for c in token),len(token)),default='server')
    entries,pages=api.search(seed,page)
    def words(text): return set(re.findall(r'[a-z0-9]+',re.sub(r'(?<=[a-z])\.(?=[a-z])','',text.lower())))
    return [s for s in entries if all(t in words(s['name']) for t in tokens)],pages
