"""Rebuild captions from phrase updates, independently of final arrival order."""
def verify_caption(events):
    phrases = {}
    states = {}
    count = 0
    overtakes = 0
    for e in events:
        p = e['phrase']
        if not e.get('event_only'):
            assert states.get(p) != 'Committed', ('update after final', p)
            phrases[p] = e['final'] if e['state'] == 'Committed' else e['preview']
            if e['state'] == 'Committed' or states.get(p) != 'Finalising':
                states[p] = e['state']
            if e['state'] == 'Speaking' and any(q < p and s == 'Finalising' for q,s in states.items()):
                overtakes += 1
        elif e['state'] == 'Finalising' and states.get(p) != 'Committed':
            states[p] = 'Finalising'
        expected = ' '.join(phrases[q] for q in sorted(phrases) if phrases[q])
        assert e['caption'] == expected, (p, e['state'], expected, e['caption'])
        base = e['prefix_before_phrase']
        assert all(states[q] == 'Committed' for q in phrases if q < base)
        prefix = ' '.join(phrases[q] for q in sorted(phrases) if q < base and phrases[q])
        assert e['committed_prefix'] == prefix
        segments = e['segments']
        assert [s['phrase'] for s in segments] == sorted(s['phrase'] for s in segments)
        for s in segments:
            assert s['phrase'] >= base
            assert s['text'] == phrases.get(s['phrase'], '')
            assert s['state'] == states[s['phrase']]
        assert e['caption'] == ' '.join(x for x in [prefix]+[s['text'] for s in segments] if x)
        count += 1
    return dict(caption_events_verified=count, previews_before_earlier_final=overtakes)
