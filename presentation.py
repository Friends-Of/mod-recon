"""Discord presentation only: consumes persisted events without changing them."""
from collections import Counter, defaultdict
import json
import re
import uuid
from modrecon.text import bounded, safe_text, TRUNCATED

MAX_ATTACHMENT_BYTES = 512 * 1024
MAX_FIELD_BYTES = 8192


def plain(value):
    return ' '.join(safe_text(value).split())


def display(value, limit=90):
    value = plain(value)
    if len(value) > limit:
        value = value[:limit - 1] + '…'
    return re.sub(r'([\\`*_~|>\[\]])', r'\\\1', value)


def full_report(label, event, items):
    lines = [f'{label} Mod Update', f'{len(items)} server mods changed',
             f"Detected: {event['detected_at']}", f"Event: {event['id']}", '']
    for kind in ('added', 'updated', 'removed'):
        group = [i for i in items if i['change_type'] == kind]
        if not group:
            continue
        lines.append(kind.upper())
        for item in group:
            lines += [f"{item['mod_name']} [{item['mod_id']}]",
                      f"  Before: {item['before_version'] if item['before_version'] is not None else '(absent)'}",
                      f"  After: {item['after_version'] if item['after_version'] is not None else '(absent)'}",
                      f"  Metadata: {item['metadata_status']}"]
            for key, title in (('size_bytes', 'Package size (bytes)'), ('game_version', 'Game version'),
                               ('created_at', 'Created'), ('updated_at', 'Updated'), ('changelog', 'Author changelog')):
                if item.get(key) is not None:
                    lines.append(f'  {title}: {item[key]}')
            lines.append('')
    return bounded('\n'.join(bounded(line, MAX_FIELD_BYTES) for line in lines), MAX_ATTACHMENT_BYTES)


def render_message(label, event, items, donation_url=None):
    items = sorted((dict(i) for i in items), key=lambda i: (i['change_type'], i['mod_id']))
    groups = {kind: [i for i in items if i['change_type'] == kind] for kind in ('added', 'updated', 'removed')}
    total = len(items)
    large = total > 12
    lines = [f'**{total} server mods changed**',
             f"**+{len(groups['added'])} Added · ↑{len(groups['updated'])} Updated · −{len(groups['removed'])} Removed**", '']

    if large:
        # Group only literal package-name prefixes and exact version strings.
        # This reports observed versions, never infers release contents.
        families = defaultdict(list)
        for item in groups['updated']:
            name = item['mod_name'] or ''
            family = 'WCS' if name.startswith('WCS_') else 'RHS' if name.startswith('RHS - ') else None
            if family:
                families[family].append(item)
        covered = set()
        for family, members in sorted(families.items()):
            if len(members) < 3:
                continue
            counts = Counter(i['after_version'] for i in members)
            version, count = counts.most_common(1)[0]
            if count < 3 or count <= len(members) / 2:
                continue
            lines.append(f"{count} of {len(members)} updated {family} packages → **{display(version, 40)}**")
            covered.update(i['mod_id'] for i in members if i['after_version'] == version)
        remaining = [i for i in groups['updated'] if i['mod_id'] not in covered]
        for item in remaining[:3]:
            lines.append(f"{display(item['mod_name'], 65)} → **{display(item['after_version'], 40)}**")
        if len(remaining) > 3:
            lines.append(f'{len(remaining) - 3} other updated packages in the full list.')
        lines.append('')

    for kind, title in (('added', 'Added'), ('updated', 'Updated'), ('removed', 'Removed')):
        group = groups[kind]
        if not group or (large and kind == 'updated'):
            continue
        lines.append(f'**{title}**')
        limit = 4 if large else 12
        for item in group[:limit]:
            version = (f"{display(item['before_version'], 40)} → {display(item['after_version'], 40)}"
                       if kind == 'updated' else display(item['after_version'] or item['before_version'], 40))
            lines.append(f"• {display(item['mod_name'], 65)} — {version}")
        if len(group) > limit:
            lines.append(f'… {len(group) - limit} more {kind} in the full list.')
        lines.append('')

    sizes = [i['size_bytes'] for i in items if i['change_type'] != 'removed' and i['size_bytes'] is not None]
    if sizes:
        lines.append(f'**{sum(sizes) / 1024**3:.2f} GiB across {len(sizes)} changed packages**')
        missing = len(groups['added']) + len(groups['updated']) - len(sizes)
        if missing:
            lines.append(f'*Size unavailable for {missing} changed packages.*')
    else:
        lines.append('*Changed package sizes unavailable.*' if groups['added'] or groups['updated'] else '*No added or updated packages.*')
    lines += ['', f'*Full list of all {total} changes: attached text file.*']
    if donation_url:
        link = donation_url.replace('(', '%28').replace(')', '%29')
        lines += ['', f'**[Support Mod Recon]({link})** — Keep server updates free, open source, and running for everyone.']

    description = '\n'.join(lines)
    # Defensive fallback for pathological display names/versions; attachment is complete.
    if len(description) > 3900:
        description = '\n'.join([lines[0], lines[1], '', f'Full list of all {total} changes: attached text file.'])
    report = full_report(label, event, items)
    if TRUNCATED in report:
        description = description.replace(f'Full list of all {total} changes: attached text file.',
                                          'Recorded changes attached; oversized text shortened. Original records remain in local history.')
    filename = f"changes-{event['id']}.txt"
    return {
        'username': 'Mod Recon',
        'allowed_mentions': {'parse': []},
        'embeds': [{'title': f'{plain(label)[:180]} Mod Update', 'description': description,
                    'color': 0x8B9C80, 'timestamp': event['detected_at'],
                    'footer': {'text': f"Mod Recon • Event {event['id']}"}}],
        '_text_attachment': {'filename': filename, 'text': report},
    }


def encode_webhook(payload):
    """Encode a saved notification; older JSON-only queued messages still work."""
    payload = dict(payload)
    attachment = payload.pop('_text_attachment', None)
    if attachment is None:
        return json.dumps(payload).encode(), 'application/json'
    filename = attachment['filename']
    if not re.fullmatch(r'changes-[a-zA-Z0-9-]+\.txt', filename):
        raise ValueError('Invalid attachment filename')
    boundary = 'modrecon' + uuid.uuid4().hex
    attachment_text = bounded(attachment['text'], MAX_ATTACHMENT_BYTES)
    if TRUNCATED in attachment_text:
        payload['embeds'] = [dict(embed, description=embed.get('description', '')[:3600]
                                 + '\n\n*Attachment display shortened for safety; original records remain in local history.*')
                             for embed in payload.get('embeds', [])]
    payload['attachments'] = [{'id': 0, 'filename': filename, 'description': 'Recorded mod changes (display limits apply)'}]
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n'.encode()
        + json.dumps(payload).encode()
        + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{filename}"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'.encode()
        + attachment_text.encode('utf-8')
        + f'\r\n--{boundary}--\r\n'.encode()
    )
    return body, f'multipart/form-data; boundary={boundary}'
