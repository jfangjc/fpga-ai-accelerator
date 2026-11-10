"""Score final-configuration T2 evidence; no board access.

Usage: python tests/t2/analyze_t2.py soak DIRECTORY
       python tests/t2/analyze_t2.py words T1_DIRECTORY [--clips ID ...]
Requires the existing streaming-study caches for word alignment.
"""
import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import pickle
import re
import statistics
import array
import wave

ROOT = Path(__file__).resolve().parents[2]

def read(path):
    return [json.loads(s) for s in path.read_text().splitlines()]

def quantile(values, q):
    if not values:
        return None
    v = sorted(values)
    x = (len(v)-1)*q
    i = int(x)
    return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)

def stats(values):
    return dict(n=len(values), p50=quantile(values,.5), p95=quantile(values,.95),
                max=max(values) if values else None)

def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2)+'\n', newline='\n')

def capture_stats(path):
    matches = re.findall(r'^capture_stats (.*)$', path.read_text(), re.M)
    assert len(matches) == 1, path
    return {k:int(v,0) for k,v in (s.split('=') for s in matches[0].split())}


def attribution(folder, updates):
    """Retain an auditable row for every merge and every >300 ms update."""
    finals = [e for e in updates if e['state']=='Committed']
    def begin(e):
        return e.get('inference_start', e['inference_time']-e['compute_ms']/1000)
    def owner(time):
        return next((f for f in finals if begin(f) <= time <= f['inference_time']), None)
    def detail(f):
        return dict(final_phrase=f['phrase'], final_frames=f['feature_count'],
                    final_backend=f['backend'], final_compute_ms=f['compute_ms']) if f else {}
    merges = []
    pattern = r'^merge_event (.*)$'
    for line in re.findall(pattern, (folder/'soak.stderr').read_text(), re.M):
        row = dict(s.split('=') for s in line.split())
        row = {k:float(v) if k in ('time','capture') else int(v) for k,v in row.items()}
        f = owner(row['time'])
        row.update(cause='final_in_flight' if row['blocked_final'] else 'other_inference', **detail(f))
        if not row['blocked_final']:
            active = next((e for e in updates if e['state']=='Speaking' and
                           begin(e) <= row['time'] <= e['inference_time']), None)
            if active:
                # Emission order is no longer submit order. Only requests on
                # this preview's engine can impose serial recovery queueing.
                engine = lambda e:'pl' if e['backend'].startswith('pl') else 'cpu'
                timeline = sorted((e for e in updates if engine(e)==engine(active)),key=begin)
                index = timeline.index(active)
                prior = timeline[index-1] if index else None
                row.update(active_preview_phrase=active['phrase'],
                           active_preview_frames=active['feature_count'],
                           active_preview_queue_ms=active.get('queue_ms'),
                           active_preview_compute_ms=active['compute_ms'])
                # The immediately preceding request was a final, and this
                # preview was already queued before that final completed.
                if prior and prior['state']=='Committed' and 'queue_ms' in active and (
                        begin(active)-active['queue_ms']/1000 < prior['inference_time']):
                    row.update(cause='final_recovery', **detail(prior))
        merges.append(row)
    if not merges:
        previous = 0
        for e in sorted(updates,key=begin):
            count = e.get('merged',0)-previous
            previous = e.get('merged',previous)
            if count > 0:
                # Old logs report only cumulative counts. Preserve that limit:
                # do not invent individual merge timestamps or exact causes.
                merges.append(dict(phrase=e['phrase'], count=count, capture=e['capture'],
                                   cause='unobserved_cumulative_delta'))
    slow = []
    for e in updates:
        if not e['latency_valid'] or e['update_ms'] <= 300:
            continue
        audio = e.get('audio_time',e['time']-e['update_ms']/1000)
        blocking = next((f for f in reversed(finals) if f is not e and
                         audio < f['inference_time'] <= begin(e)+.002 and begin(f) < begin(e)), None)
        ordered_blocker = None
        if e['state']=='Speaking' and 'inference_finished' in e and e.get('ordered_wait_ms',0)>50:
            ready=e['inference_finished']
            emitted=ready+e.get('ordered_wait_ms',0)/1000
            ordered_blocker=next((f for f in reversed(finals) if f['phrase']<e['phrase'] and
                ready<=f.get('inference_finished',f['inference_time'])<=emitted+.002),None)
            if ordered_blocker:
                blocking=ordered_blocker
        cause = ('own_final_layer_fallback' if e['state']=='Committed' and e['backend']=='pl-layer'
                 else 'own_final_compute' if e['state']=='Committed'
                 else 'ordered_final_wait' if ordered_blocker
                 else 'other_output_order_wait' if e.get('ordered_wait_ms',0)>50
                 else 'final_in_flight' if blocking else 'other')
        slow.append(dict(phrase=e['phrase'],state=e['state'],capture=e['capture'],
            update_ms=e['update_ms'], compute_ms=e['compute_ms'], frames=e['feature_count'],
            backend=e['backend'],queue_ms=e.get('queue_ms'),
            ordered_wait_ms=e.get('ordered_wait_ms'),cause=cause,**detail(blocking)))
    result = dict(merge_records=len(merges), merged=sum(r.get('count',1) for r in merges),
                  merge_causes={c:sum(r.get('count',1) for r in merges if r['cause']==c)
                                for c in sorted({r['cause'] for r in merges})},
                  slow_update_causes={c:sum(r['cause']==c for r in slow) for c in sorted({r['cause'] for r in slow})},
                  merges=merges, updates_over_300ms=slow)
    dump(folder/'attribution.json',result)
    return {k:v for k,v in result.items() if k not in ('merges','updates_over_300ms')}

def soak(folder):
    result = json.loads((folder/'run-result.json').read_text())
    events = read(folder/'soak.jsonl')
    updates = [e for e in events if not e.get('event_only')]
    previews = [e for e in updates if e['state']=='Speaking']
    monitor = read(folder/'monitor.jsonl')
    power = read(folder/'power.jsonl')
    tcp = read(folder/'tcp.jsonl')
    counter = capture_stats(folder/'soak.stderr')
    causes = attribution(folder,updates)
    merge_rows = json.loads((folder/'attribution.json').read_text())['merges']
    buckets = []
    for i in range(6):
        ev = [e for e in previews if i*300 <= e['capture'] < (i+1)*300]
        m = [r for r in monitor if i*300 <= r['seconds'] < (i+1)*300]
        p = [r['watts'] for r in power if i*300 <= r['seconds'] < (i+1)*300]
        buckets.append(dict(minutes=f'{i*5}-{(i+1)*5}',
            latency_ms=stats([e['update_ms'] for e in ev if e['latency_valid']]),
            invalid=sum(not e['latency_valid'] for e in ev),
            compute_ms=stats([e['compute_ms'] for e in ev]),
            merged=sum(r.get('count',1) for r in merge_rows if i*300 <= r['capture'] < (i+1)*300),
            rss_min_kib=min((r['rss_kib'] for r in m), default=None),
            rss_median_kib=statistics.median(r['rss_kib'] for r in m) if m else None,
            power_mean_w=statistics.mean(p) if p else None,
            power_max_w=max(p) if p else None,
            rss_max_kib=max((r['rss_kib'] for r in m), default=None)))
    warm_start = min(150, result['audio_seconds']/2)
    warm = [r for r in monitor if r['seconds'] >= warm_start]
    xs, ys = [r['seconds']/60 for r in warm], [r['rss_kib'] for r in warm]
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x-xm)*(y-ym) for x,y in zip(xs,ys))/sum((x-xm)**2 for x in xs)
    temps = {}
    for row in monitor:
        for path, value in row['temperatures_mc'].items():
            temps.setdefault(path, []).append(value/1000)
    lost = sum(max(e.get('lost_frames',0) for e in updates if e['phrase']==p)
               for p in {e['phrase'] for e in updates})
    served = {(e['phrase'],e['state'],e['time']) for e in events}
    received = {(r['event']['phrase'],r['event']['state'],r['event']['time']) for r in tcp}
    expected_frames = (result['audio_seconds']*16000+12800-320)//160+1
    result.update(capture=counter, expected_frames=expected_frames, events=len(events), previews=len(previews),
        attribution=causes,
        finals=sum(e['state']=='Committed' for e in updates),
        merged=max(e.get('merged',0) for e in updates), lost_stitch_frames=lost,
        latency_ms=stats([e['update_ms'] for e in previews if e['latency_valid']]),
        invalid_preview_latencies=sum(not e['latency_valid'] for e in previews),
        buckets=buckets,
        rss_after_150s=dict(first_kib=ys[0], last_kib=ys[-1], min_kib=min(ys), max_kib=max(ys),
            delta_kib=ys[-1]-ys[0], slope_kib_per_minute=slope),
        power=dict(samples=len(power), mean_w=statistics.mean(r['watts'] for r in power),
            max_w=max(r['watts'] for r in power), first_seconds=power[0]['seconds'],
            last_seconds=power[-1]['seconds']),
        temperature_c={p:dict(min=min(v),mean=statistics.mean(v),max=max(v)) for p,v in temps.items()},
        led_on_samples=sum(bool(r['control']&64) for r in monitor),
        led_off_samples=sum(not bool(r['control']&64) for r in monitor),
        button_sticky_samples=sum(bool(r['status']&8) for r in monitor),
        fifo_overflow_samples=sum(bool(r['status']&2) for r in monitor),
        fifo_underrun_samples=sum(bool(r['status']&4) for r in monitor),
        maximum_fifo_occupancy=max(r['status']>>4 for r in monitor),
        tcp_events=len(tcp), tcp_missing_events=len(served-received))
    result['zero_loss_pass'] = (result['exit']==0 and counter['frames']==expected_frames and lost==0 and
        result['fifo_overflow_samples']==0 and all(counter[k]==0 for k in
        ('sequence_errors','ring_overruns','tx_underruns','mic_overruns','consumer_dropped','error')))
    result['strict_t2_pass'] = (result['zero_loss_pass'] and result['merged']==0 and
        result['invalid_preview_latencies']==0 and result['rss_after_150s']['delta_kib']<=0 and
        result['wall_seconds']>=1800 and result['tcp_missing_events']==0)
    # Additional diagnostics do not change any acceptance predicate above.
    cpu = [r for r in monitor if 'ps_cpu_ticks' in r]
    if len(cpu)>1:
        loads=[100*(b['ps_cpu_ticks']-a['ps_cpu_ticks'])/a['clock_ticks']/
               (b['seconds']-a['seconds']) for a,b in zip(cpu,cpu[1:])]
        result['ps_cpu_percent_one_core']=dict(mean=statistics.mean(loads),**stats(loads))
        for bucket,i in zip(buckets,range(6)):
            values=[v for r,v in zip(cpu[1:],loads) if i*300<=r['seconds']<(i+1)*300]
            bucket['ps_cpu_mean_percent_one_core']=statistics.mean(values) if values else None
    result['ordered_preview_wait_ms']=stats([e.get('ordered_wait_ms',0) for e in previews])
    result['final_compute_ms']=stats([e['compute_ms'] for e in updates if e['state']=='Committed'])
    # Measure end of speech using the unchanged fixed waveform RMS policy:
    # end of the last active 10 ms block in each committed phrase. The final
    # boundary includes VAD silence, so capture->display alone understates it.
    wav=folder/'soak.wav'
    if wav.exists():
        voiced=[]
        with wave.open(str(wav),'rb') as source:
            assert source.getparams()[:3]==(1,2,16000)
            pos=0
            while block:=source.readframes(160):
                pcm=array.array('h',block)
                if sum(x*x for x in pcm)/len(pcm)/(32768**2)>=.01**2:
                    voiced.append(pos+len(pcm))
                pos+=len(pcm)
        rows=[]
        for e in updates:
            if e['state']!='Committed': continue
            ends=[s for s in voiced if e['phrase_begin']*16000<s<=round(e['capture']*16000)]
            if not ends: continue
            speech=ends[-1]
            mapped=e['audio_time']+(speech-e['oldest_sample'])/e['sample_rate']
            rows.append(dict(phrase=e['phrase'],speech_end_sample=speech,
                             boundary_sample=round(e['capture']*16000),
                             phrase_cap_boundary=e['capture']-e['phrase_begin']>=15-1e-6,
                             final_display_ms=(e['time']-mapped)*1000))
        result['speech_end_to_final_display_ms']=stats([r['final_display_ms'] for r in rows])
        result['non_cap_speech_end_to_final_display_ms']=stats([
            r['final_display_ms'] for r in rows if not r['phrase_cap_boundary']])
        result['phrase_cap_final_display_ms']=stats([
            r['final_display_ms'] for r in rows if r['phrase_cap_boundary']])
        dump(folder/'final-latency.json',dict(method='Last active 10 ms waveform RMS block (0.01), mapped with event sample clock to software framebuffer completion.',rows=rows))
    dump(folder/'analysis.json', result)
    print(json.dumps(result, indent=2))

def word_metrics(folder, clips):
    # Use the study's actual pure alignment functions without importing torch/model.
    tree = ast.parse((ROOT/'model/stream_sim.py').read_text())
    funcs = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('align','stable_onsets')]
    ns = {}
    exec(compile(ast.Module(body=funcs,type_ignores=[]),'stream_sim.py','exec'),ns)
    results, raw_words, gaps = {}, [], {}
    alignment_path = folder/'word-alignments.json'
    alignments = json.loads(alignment_path.read_text()) if alignment_path.exists() else {}
    reference_path = folder/'python-reference.json'
    references = json.loads(reference_path.read_text()) if reference_path.exists() else {}
    for backend in ('cpu','pl'):
        rows = []
        for clip in clips:
            path = folder/f'{clip}.{backend}.jsonl'
            if not path.exists():
                continue
            events = [e for e in read(path) if not e.get('event_only')]
            if clip not in alignments:
                caches = list((ROOT/'data/stream_cache_v1').glob(f'base-{clip}-cap15-*.pkl'))
                assert len(caches)==1, caches
                cached = pickle.loads(caches[0].read_bytes())
                alignments[clip] = dict(reference=cached['row']['text'].lower(), ends=cached['ends'],
                    source=str(caches[0].relative_to(ROOT)),
                    sha256=hashlib.sha256(caches[0].read_bytes()).hexdigest())
            alignment = alignments[clip]
            ref, ends = alignment['reference'].split(), alignment['ends']
            assert len(ref)==len(ends)
            # Recover renderer time relative to accepted audio. For each served
            # successor, L_update is measured from the preceding served capture.
            offsets = []
            previous = {}
            for e in events:
                before = previous.get(e['phrase'])
                if before and e.get('latency_valid'):
                    offsets.append(e['update_ms']/1000+before['capture']-e['time'])
                previous[e['phrase']] = e
            assert offsets
            clock_fit = all('audio_time' in e for e in events)
            if not clock_fit:
                assert max(offsets)-min(offsets)<.00001
            offset = statistics.median(offsets)
            stream, phrases = [], {}
            for e in events:
                if e['state']!='Speaking':
                    continue
                tokens = e['preview'].split()
                assert len(tokens)==len(e['word_ends']), (clip,e)
                # PL frame zero is centred one 10 ms hop after its start;
                # CPU phrase-local frame zero is centred at phrase begin.
                acoustic = [t+(.01 if backend=='pl' else 0) for t in e['word_ends']]
                phrases[e['phrase']] = list(zip(tokens,acoustic))
                words = [w for p in sorted(phrases) for w in phrases[p]]
                # A fitted audio clock has no single constant epoch offset.
                # Map renderer time into nominal sample coordinates using the
                # exact oldest-new-audio anchor exported with this update.
                rendered = (e['oldest_sample']/16000+e['update_ms']/1000
                            if clock_fit else e['time']+offset)
                stream.append((rendered,words))
            onsets = ns['stable_onsets'](stream,ref,ends)
            finals = sorted((e for e in events if e['state']=='Committed'),key=lambda e:e['phrase'])
            final_words, owners = [], []
            for e in finals:
                tokens = e['final'].split()
                final_words += tokens
                rendered = (e['oldest_sample']/16000+e['update_ms']/1000
                            if clock_fit else e['time']+offset)
                owners += [rendered]*len(tokens)
            in_final = ns['align'](ref,final_words)
            observed = [(o-end)*1000 for o,end in zip(onsets,ends) if o is not None]
            user = []
            for i,(word,end,onset) in enumerate(zip(ref,ends,onsets)):
                final_time = owners[in_final[i]] if i in in_final else None
                user_time = min(final_time,onset) if final_time is not None and onset is not None else final_time
                if user_time is not None:
                    user.append((user_time-end)*1000)
                raw_words.append(dict(clip=clip,backend=backend,index=i,word=word,end_s=end,
                    preview_onset_s=onset,user_onset_s=user_time,
                    preview_ms=(onset-end)*1000 if onset is not None else None,
                    user_ms=(user_time-end)*1000 if user_time is not None else None))
            rows.append(dict(clip=clip,words=len(ref), observed_ms=observed,user_ms=user,
                censored=len(ref)-len(observed), user_censored=len(ref)-len(user),
                audio_epoch_offset_s=offset, offset_spread_us=(max(offsets)-min(offsets))*1e6,
                alignment_cache=alignment['source'], alignment_sha256=alignment['sha256']))
            reference = ROOT/f'data/export/stream_ref/{clip}.json'
            if clip not in references and reference.exists():
                references[clip] = json.loads(reference.read_text())
            if clip in references:
                expected = [e for e in references[clip]['events']
                            if e['state']=='Speaking' and 'feature_count' in e]
                actual = [e for e in events if e['state']=='Speaking']
                key = lambda e:(e['phrase'],round(e['capture'],3))
                by_tick = {key(e):e for e in expected}
                fields = ['feature_count','window_start','emitted','preview','tentative']
                matched = [(a,by_tick[key(a)]) for a in actual if key(a) in by_tick]
                final_preview = lambda ev:' '.join(next(e['preview'] for e in reversed(ev) if e['phrase']==p)
                    for p in sorted({e['phrase'] for e in ev}))
                gaps[f'{clip}.{backend}'] = dict(actual_updates=len(actual),reference_updates=len(expected),
                    matching_ticks=len(matched),
                    differences={f:sum(a[f]!=b[f] for a,b in matched) for f in fields},
                    feature_count_deltas=sorted({a['feature_count']-b['feature_count'] for a,b in matched}),
                    board_last_preview=final_preview(actual),reference_last_preview=final_preview(expected))
        if rows:
            observed = [v for row in rows for v in row['observed_ms']]
            user = [v for row in rows for v in row['user_ms']]
            results[backend] = dict(clips=len(rows),words=sum(r['words'] for r in rows),
                preview_ms=stats(observed), censored=sum(r['censored'] for r in rows),
                negative_fraction=sum(v<0 for v in observed)/len(observed),
                user_ms=stats(user), user_censored=sum(r['user_censored'] for r in rows), per_clip=rows)
    dump(folder/'word-summary.json', results)
    dump(alignment_path, alignments)
    dump(reference_path, references)
    dump(folder/'reference-gap.json', gaps)
    with (folder/'word-latency.csv').open('w', newline='') as f:
        w = csv.DictWriter(f,fieldnames=list(raw_words[0]), lineterminator='\n'); w.writeheader(); w.writerows(raw_words)
    print(json.dumps({k:{n:v for n,v in r.items() if n!='per_clip'} for k,r in results.items()},indent=2))

if __name__=='__main__':
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('mode', choices=['soak','words'])
    p.add_argument('directory', type=Path)
    p.add_argument('--clips', nargs='+')
    a = p.parse_args()
    if a.mode=='soak':
        soak(a.directory)
    else:
        word_metrics(a.directory,a.clips or (ROOT/'model/testset_40.txt').read_text().split())
