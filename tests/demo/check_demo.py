"""Validate a fetched rehearsal against the PC TCP transcript; writes summary.json."""
import hashlib
import json
from pathlib import Path
import re
import sys

folder = Path(sys.argv[1])
read = lambda name: [json.loads(s) for s in (folder/name).read_text(encoding='utf-8-sig').splitlines()]
result = json.loads((folder/'result.json').read_text())
assert result['exit'] == 0, result
events, tcp = read('session.jsonl'), read('pc-tcp.jsonl')
assert tcp and all('received_utc' in e for e in tcp)
key = lambda e: (e['phrase'], e['state'], e.get('time'))
received = {key(e) for e in tcp}
missing = [e for e in events if key(e) not in received]
# A joining client first receives the latest state as a snapshot (checked
# byte-for-byte below). Events from before that snapshot cannot reach it and
# may be absent; every event from the join onwards must be received.
assert tcp[0].get('snapshot') or not missing, 'client joined without a snapshot'
assert all(e['time'] < tcp[0]['time'] for e in missing), 'missing TCP captions'
assert received <= {key(e) for e in events}, 'extra TCP events'
by_key = {key(e): e for e in events}
for event in tcp:
    payload = {k: v for k, v in event.items() if k not in ('received_utc', 'snapshot')}
    assert payload == by_key[key(event)], 'TCP payload differs from SD log'
updates = [e for e in events if not e.get('event_only')]
finals = [e['final'] for e in sorted(updates,key=lambda e:e['phrase']) if e['state'] == 'Committed']
assert len(finals) == len(result['clips']), finals
assert all(e['display'] == 'fb' for e in updates), 'framebuffer output unavailable'
assert all(e.get('lost_frames', 0) == 0 for e in updates)
assert all((e['backend'].startswith('pl-') or
            (e['state']=='Committed' and e['backend']=='cpu')) if result['backend']=='pl' else e['backend']=='cpu'
           for e in updates)
stderr = (folder/'stderr.txt').read_text()
assert 'dropped' not in stderr.replace('consumer_dropped=0', ''), stderr
capture = None
if result['backend'] == 'pl':
    match = re.search(r'^capture_stats (.*)$', stderr, re.M)
    assert match, stderr
    capture = {k: int(v, 0) for k, v in (s.split('=') for s in match[1].split())}
    assert all(capture[k] == 0 for k in ('sequence_errors', 'ring_overruns', 'tx_underruns',
                                       'mic_overruns', 'consumer_dropped', 'error')), capture
ppm = (folder/'caption.ppm').read_bytes()
assert ppm.startswith(b'P6\n640 240\n255\n') and len(ppm) == 460815
assert len(set(ppm[15:])) > 2, 'blank snapshot'
summary = dict(result, events=len(events), tcp_events=len(tcp), tcp_missing_captions=0,
               startup_state_events_before_connection=len(missing), finals=finals,
               display='fb', capture=capture, merged=max(e.get('merged', 0) for e in updates),
               lost_frames=0, ppm_sha256=hashlib.sha256(ppm).hexdigest(), pass_rehearsal=True)
(folder/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', newline='\n')
print(json.dumps(summary, indent=2))
