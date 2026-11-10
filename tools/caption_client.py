"""Print caption events from sttcap's line-delimited JSON TCP server.

The server accepts clients at any time; a client joining mid-session first
receives the latest event with "snapshot": true (current caption state).
"""
import argparse
import json
import socket
import time
from datetime import datetime, timezone


def caption_text(event, annotate_pending=False):
    """Snapshots include the entire caption even when finals arrive late."""
    if 'segments' in event:
        parts = [event['committed_prefix']]
        parts += [p['text'] for p in sorted(event['segments'], key=lambda p: p['phrase'])]
        text = ' '.join(p for p in parts if p)
        assert text == event['caption'], event
        if annotate_pending:
            parts = [event['committed_prefix']]
            parts += [f"[{p['phrase']} pending: {p['text']}]" if p['state']=='Finalising'
                      else p['text'] for p in sorted(event['segments'],key=lambda p:p['phrase'])]
            text = ' '.join(p for p in parts if p)
        return text
    return event.get('caption') or event.get('final') or event.get('tentative') or event.get('preview', '')


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('host')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--json', action='store_true')
    p.add_argument('--timestamps', action='store_true', help='add PC UTC receipt timestamps')
    p.add_argument('--output', help='also save the printed transcript as UTF-8')
    p.add_argument('--json-output', help='also save raw events plus received_utc as JSONL')
    p.add_argument('--wait', type=float, default=0,
                   help='retry connecting for this many seconds (server starting)')
    args = p.parse_args()
    deadline = time.monotonic()+args.wait
    while True:
        try:
            connection = socket.create_connection((args.host, args.port))
            break
        except ConnectionRefusedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)
    output = open(args.output, 'w', encoding='utf-8', newline='\n') if args.output else None
    raw = open(args.json_output, 'w', encoding='utf-8', newline='\n') if args.json_output else None
    def emit(line):
        print(line, flush=True)
        if output:
            output.write(line+'\n'); output.flush()
    with connection:
        with connection.makefile('r', encoding='utf-8') as stream:
            for line in stream:
                event = json.loads(line)
                receipt = datetime.now(timezone.utc).isoformat()
                if raw:
                    raw.write(json.dumps(dict(event, received_utc=receipt))+'\n')
                    raw.flush()
                if args.json:
                    if args.timestamps:
                        event['received_utc'] = receipt
                    emit(json.dumps(event) if args.timestamps else line.rstrip('\n'))
                else:
                    text = caption_text(event, annotate_pending=True)
                    # The first line after connecting may be the server's
                    # "snapshot" of the current caption state.
                    mark = ' (snapshot)' if event.get('snapshot') else ''
                    prefix = receipt+' ' if args.timestamps else ''
                    emit(f"{prefix}{event['phrase']} {event['state']}{mark}: {text}")
    for stream in (output, raw):
        if stream:
            stream.close()


if __name__ == '__main__':
    main()
