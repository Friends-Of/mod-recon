"""Render an existing event read-only. Does not poll, send, or alter history."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from presentation import render_message

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='data/reforger-watch.db')
    parser.add_argument('--output', default='preview')
    args = parser.parse_args()
    db = sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    try:
        event = db.execute('SELECT * FROM change_events ORDER BY detected_at DESC LIMIT 1').fetchone()
        if event is None:
            parser.error('No recorded event to preview')
        label = db.execute('SELECT label FROM servers WHERE id=?',(event['server_id'],)).fetchone()[0]
        items = db.execute('SELECT * FROM change_items WHERE event_id=?',(event['id'],)).fetchall()
        message = render_message(label,event,items)
    finally:
        db.close()
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    embed=message['embeds'][0]
    (out/'notification.md').write_text('**'+embed['title']+'**\n\n'+embed['description']+'\n\n'+embed['timestamp']+'\n',encoding='utf-8')
    (out/'notification.json').write_text(json.dumps(message,indent=2,ensure_ascii=False),encoding='utf-8')
    attachment=message['_text_attachment']
    (out/attachment['filename']).write_text(attachment['text'],encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(embed['description'])
