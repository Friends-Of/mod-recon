"""Display boundaries for untrusted names and author text."""
import unicodedata


def safe_text(value):
    return ''.join(c for c in str(value if value is not None else '')
                   if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf', 'Cs'))


def terminal(value, limit=300):
    return ' '.join(safe_text(value).split())[:limit]


TRUNCATED = '\n[Display shortened for safety; original records remain in local history.]'


def bounded(value, limit):
    source = str(value if value is not None else '')
    # Bound processing of legacy records too, before scanning or encoding them.
    text = safe_text(source[:limit])
    data = text.encode('utf-8')
    if len(source) <= limit and len(data) <= limit:
        return text
    return data[:limit - len(TRUNCATED.encode())].decode('utf-8', errors='ignore') + TRUNCATED
