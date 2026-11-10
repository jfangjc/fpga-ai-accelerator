"""Buffered streaming caption reference for INT8 QuartzNet (spec: docs/streaming.md).

Each hop, the INT8 golden network runs on the newest W seconds of causal
running-normalized features. Output frames more than R seconds before the
window's right edge are COMMITTED and stitched; the remainder is a TENTATIVE
tail that the next hop may revise. After 700 ms of VAD silence the whole phrase
is re-decoded with utterance normalization (the FINAL).

Two stages, deliberately separated:
  inference (slow, cached):  phrases -> StreamingFrontend -> run_window
  replay    (fast, pure):    simulate_clip -> clip_metrics -> summarize
Lookahead R, service time c and queueing only affect replay, so sweeping them
re-uses the cache. W and padding change inference.

Run from the repository root. Commands are in docs/results/streaming.md.
Only golden_int8.network executes the INT8 graph.
"""
import argparse
import hashlib
import json
import math
import os
import pickle
import sys
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from frontend import features, mel_power, log_normalize
from golden_int8 import network, round_away
from quartznet_float import QuartzNet
from eval_wer import testset
import stream_policy as policy

SR = policy.SAMPLE_RATE
BLOCK = policy.FEATURE_HOP          # 160 samples: VAD block and feature hop
OUT_HOP = 2*BLOCK                   # 320 samples (20 ms): C1 stride 2
BLANK = 28
VOCAB = " abcdefghijklmnopqrstuvwxyz'"
SYNTHETIC_FRAMES = 16               # 160 ms of VALID edge padding
# Bump when anything that changes cached inference output changes.
# v2: silence-padded windows quantize in float32 (v1 promoted them to float64).
# v3: clips are followed by REPLAY_TAIL of digital silence (paced WAV replay),
#     so phrases end by the VAD rule instead of an end-of-file flush.
INFERENCE_VERSION = 3
REPLAY_TAIL = 0.8                   # s of zeros after each clip (>= VAD_SILENCE)
METRIC_VERSION = 4                 # replay contract; independent of inference cache


# --------------------------------------------------------------------------
# VAD and phrase segmentation
# --------------------------------------------------------------------------

def block_active(x, pos):
    """A 10 ms block is active when its RMS (float64) reaches VAD_RMS."""
    block = x[pos:pos+BLOCK].astype(np.float64)
    return np.sqrt(np.mean(block**2)) >= policy.VAD_RMS


def phrases(x, cap=policy.PHRASE_CAP):
    """Yield (begin, onset, finish) sample indices of each phrase.

    Blocks are the clip-absolute lattice [160k, 160k+160). The first active
    block starts a phrase: onset = end of that block (detection time), begin =
    onset block start minus VAD_LEAD, never before the previous phrase's end.
    The phrase ends at the end of the block that completes VAD_SILENCE of
    consecutive inactive blocks, or of the block at which finish-begin reaches
    the cap (lead-in and trailing silence included). EOF flushes.
    """
    start = onset = None
    boundary = 0
    silent = 0
    for pos in range(0, len(x), BLOCK):
        if block_active(x, pos):
            if start is None:
                start = max(boundary, pos-round(policy.VAD_LEAD*SR))
                onset = min(len(x), pos+BLOCK)
            silent = 0
        elif start is not None:
            silent += 1
        end = min(len(x), pos+BLOCK)
        if start is not None and (silent >= round(policy.VAD_SILENCE*100) or
                                  pos+BLOCK-start >= round(cap*SR)):
            yield start, onset, end
            boundary = end
            start = onset = None
            silent = 0
    if start is not None:
        yield start, onset, len(x)


# --------------------------------------------------------------------------
# Frontend, quantization, MAC accounting
# --------------------------------------------------------------------------

class StreamingFrontend:
    """Causal running-normalized features for one phrase.

    Feature frame j is centred on phrase sample 160*j and is consumed once
    N >= 160*j+256 samples exist, i.e. safe_count = floor((N-256)/160)+1.
    The reference recomputes the retained prefix but only keeps new safe
    frames; no right-edge reflection reaches a consumed frame. C may keep an
    FFT-sized ring plus the previous raw sample for preemphasis instead.
    """
    def __init__(self, prior):
        self.audio = np.empty(0, np.float32)
        self.state = prior
        self.count = 0
        self.frames = np.empty((64, 0), np.float32)

    def push(self, samples):
        self.audio = np.concatenate((self.audio, samples))
        if len(self.audio) <= policy.FUTURE_SAMPLES:
            return self.frames
        safe = (len(self.audio)-policy.FUTURE_SAMPLES)//BLOCK+1
        mel = mel_power(self.audio)[:, self.count:safe]
        if mel.shape[1]:
            new, self.state = log_normalize(mel, 'running', self.state)
            self.frames = np.concatenate((self.frames, new), axis=1)
            self.count = safe
        return self.frames


def quantize(f, q):
    """float32 features / input scale, half-away, saturate; zero-pad T to 16."""
    assert f.dtype == np.float32
    f = np.pad(f, ((0, 0), (0, (-f.shape[1]) % 16)))
    return np.clip(round_away(f/q['input_scale']), -128, 127).astype(np.int8)


def silence_frame(prior):
    """Digital-silence log-mel floor normalized by the fixed dev prior (float32)."""
    std = np.sqrt(np.asarray(prior['m2'], np.float64)/(prior['count']-1))
    return ((np.log(np.float32(2**-24))-np.asarray(prior['mean'], np.float64)) /
            (std+1e-5)).astype(np.float32)


def macs(layers, frames):
    """Executed padded MAC slots for `frames` valid input frames.

    Ordinary/pointwise: O tiles of 32, T tiles of 16, flattened I*K tiles of
    16. Depthwise: O32 x T16 x actual taps. Not a cycle estimate.
    """
    lengths = {'input': math.ceil(frames/16)*16}
    total = 0
    for layer in layers:
        t = lengths[layer['inputs'][0]]
        if layer['op'] == 'conv':
            o, ci, k = layer['w'].shape
            t = (t+2*layer['padding']-layer['dilation']*(k-1)-1)//layer['stride']+1
            if layer['groups'] == o and ci == 1:
                total += math.ceil(o/32)*32*math.ceil(t/16)*16*k
            else:
                total += math.ceil(o/32)*32*math.ceil(t/16)*16*math.ceil(ci*k/16)*16
        lengths[layer['name']] = t
    return total


def window_frames(window, padding):
    """Valid input frames of a full-length window, including synthetic edge."""
    return round(window*100)+(SYNTHETIC_FRAMES if padding != 'zero' else 0)


def window_start(t, window):
    """First feature frame of the window ending at safe count t (even)."""
    s = max(0, t-round(window*100))
    return s-s % 2


def run_window(frames, t, window, padding, q):
    """INT8 argmax ids of the real outputs of the window ending at feature t.

    Returns (first global output frame, ids, MACs). Local output j is global
    output start/2+j. Synthetic frames are VALID network input; their outputs
    are discarded. 'zero' relies on the network's own zero padding/masking.
    """
    s = window_start(t, window)
    f = frames[:, s:t]
    real = t-s
    if padding == 'replicate':
        f = np.concatenate((f, np.repeat(f[:, -1:], SYNTHETIC_FRAMES, axis=1)), axis=1)
    elif padding == 'silence':
        edge = silence_frame(q['running_prior'])[:, None]
        f = np.concatenate((f, np.repeat(edge, SYNTHETIC_FRAMES, axis=1)), axis=1)
    elif padding != 'zero':
        raise ValueError(padding)
    logits = network(quantize(f, q), q['layers'], length=f.shape[1])
    count = (real+1)//2
    return s//2, logits.argmax(0)[:count].astype(np.int8), macs(q['layers'], f.shape[1])


# --------------------------------------------------------------------------
# Reference word timing (float model, whole clip)
# --------------------------------------------------------------------------

def align_words(logits, text):
    """Reference-constrained CTC Viterbi over full-clip float logits.

    Returns per reference word the time (s, 20 ms frames) of the last frame
    assigned to its last character. Raw logits suffice: the per-frame
    log-softmax constant is the same for every path.
    """
    chars = [VOCAB.index(c) for c in text]
    labels = np.full(2*len(chars)+1, BLANK, int)
    labels[1::2] = chars
    score = np.full(len(labels), -np.inf)
    score[0] = 0
    back = np.zeros((logits.shape[1], len(labels)), np.int8)
    skip = (labels != BLANK) & (labels != np.roll(labels, 2))
    skip[:2] = False
    for t, frame in enumerate(logits.T):
        choices = np.stack((score, np.r_[-np.inf, score[:-1]],
                            np.r_[-np.inf, -np.inf, score[:-2]]))
        choices[2, ~skip] = -np.inf
        move = choices.argmax(0)
        score = choices[move, np.arange(len(labels))]+frame[labels]
        back[t] = move
    state = len(labels)-1-int(score[-2] > score[-1])
    if not np.isfinite(score[state]):
        raise ValueError('CTC alignment impossible')
    path = np.empty(logits.shape[1], int)
    for t in range(len(path)-1, -1, -1):
        path[t] = state
        state -= int(back[t, state])
    ends = []
    for i, c in enumerate(text):
        if c != ' ' and (i == len(text)-1 or text[i+1] == ' '):
            ends.append(float(np.flatnonzero(path == 2*i+1)[-1]*.02))
    return ends


# --------------------------------------------------------------------------
# Cached inference
# --------------------------------------------------------------------------

def replay_audio(row):
    """(clip, clip followed by REPLAY_TAIL zeros as injected on the bench)."""
    x, sr = sf.read(row['path'], dtype='float32')
    assert sr == SR
    return x, np.concatenate((x, np.zeros(round(REPLAY_TAIL*SR), np.float32)))


def prepare(row, q, cap):
    """Per clip: reference word ends, phrases, causal features, finals."""
    clip, x = replay_audio(row)
    # Reference timing from the unmodified clip.
    ends = align_words(QuartzNet()(features(clip), length=len(clip)//BLOCK+1), row['text'])
    hop = round(policy.HOP*SR)
    result = []
    for begin, onset, finish in phrases(x, cap):
        audio = x[begin:finish]
        if len(audio) <= policy.FUTURE_SAMPLES:
            continue
        front = StreamingFrontend(q['running_prior'])
        updates = []
        # Every hop of phrase audio (the cache also holds a last short update
        # at the phrase end; replay never serves ticks at or after finish).
        for end in range(hop, len(audio)+hop, hop):
            end = min(end, len(audio))
            front.push(audio[len(front.audio):end])
            updates.append((end/SR, front.count))
        n = len(audio)//BLOCK+1
        final_ids = network(quantize(features(audio)[:, :n], q), q['layers'], length=n).argmax(0)
        result.append(dict(begin=begin/SR, finish=finish/SR, running=front.frames,
                           updates=updates, final=ctc_text(final_ids)))
    return dict(row=row, ends=ends, phrases=result)


def enable_exact_blas():
    """Speed golden convolution sums using exact double integer arithmetic.

    Every integer product and partial sum is below 2**53 (checked bound), so
    double sums are exact. Bias/overflow checks and Q31 stay in golden_int8.
    """
    import golden_int8
    from scipy.linalg.blas import dgemm
    original = golden_int8.conv_acc

    def conv(x, w, b, stride=1, padding=0, dilation=1, groups=1):
        if groups == w.shape[0] and w.shape[1] == 1:
            padded = np.pad(x.astype(np.int64), ((0, 0), (padding, padding)))
            span = dilation*(w.shape[2]-1)+1
            windows = np.lib.stride_tricks.sliding_window_view(padded, span, axis=1)
            windows = windows[:, ::stride, ::dilation]
            acc = np.einsum('ctk,ck->ct', windows, w[:, 0].astype(np.int64))
            return golden_int8.checked(acc+np.asarray(b, np.int64)[:, None])
        if groups != 1:
            return original(x, w, b, stride, padding, dilation, groups)
        bound = float(np.abs(w.astype(np.int64)).sum(axis=(1, 2)).max())*255
        if bound+float(np.abs(b.astype(np.int64)).max()) >= 2**53:
            raise OverflowError('BLAS exactness bound')
        padded = np.pad(x.astype(np.float64), ((0, 0), (padding, padding)))
        o, ci, k = w.shape
        t = (padded.shape[1]-dilation*(k-1)-1)//stride+1
        acc = np.zeros((o, t), np.float64)
        for j in range(k):
            v = padded[:, j*dilation:j*dilation+t*stride:stride]
            acc += dgemm(1, w[:, :, j].astype(np.float64), v)
        return golden_int8.checked(acc.astype(np.int64)+np.asarray(b, np.int64)[:, None])

    golden_int8.conv_acc = conv


def cache_paths(args, row, window, padding):
    tag = f"{row['id']}-cap{args.cap:g}-{args.digest}"
    root = Path(args.cache)
    return root/f'base-{tag}.pkl', root/f"win-{tag}-W{window:g}-{padding}.pkl"


def write_atomic(path, obj):
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_bytes(pickle.dumps(obj))
    tmp.replace(path)


def load_clip(row, window, padding, args):
    """Cached inference for one clip, window size and padding mode."""
    torch.set_num_threads(1)
    basepath, path = cache_paths(args, row, window, padding)
    if path.exists() and not args.recompute:
        out = pickle.loads(path.read_bytes())
    else:
        enable_exact_blas()
        with open(args.model, 'rb') as f:
            q = pickle.load(f)
        if basepath.exists() and not args.recompute:
            base = pickle.loads(basepath.read_bytes())
        else:
            base = prepare(row, q, args.cap)
            write_atomic(basepath, base)
        out = dict(id=row['id'], reference=row['text'], ends=base['ends'], phrases=[])
        for phrase in base['phrases']:
            windows = []
            for time, t in phrase['updates']:
                start, ids, cost = run_window(phrase['running'], t, window, padding, q)
                windows.append(dict(time=time, start=start, count=len(ids), ids=ids, macs=cost))
            out['phrases'].append(dict(begin=phrase['begin'], finish=phrase['finish'],
                                       final=phrase['final'], windows=windows))
        if args.recompute and path.exists():
            assert_same(pickle.loads(path.read_bytes()), out)
            print('recompute matches cache', row['id'], window, padding, flush=True)
        write_atomic(path, out)
    # Onsets come from the (cheap) VAD; cross-check cached phrase bounds.
    _, x = replay_audio(row)
    bounds = [b for b in phrases(x, args.cap) if b[2]-b[0] > policy.FUTURE_SAMPLES]
    assert [(round(p['begin']*SR), round(p['finish']*SR)) for p in out['phrases']] == \
        [(b, e) for b, _, e in bounds], row['id']
    # Replay works in integer samples (clip-absolute; window 'end' is
    # phrase-relative) so tick/onset/queue comparisons are exact.
    for phrase, (begin, onset, finish) in zip(out['phrases'], bounds):
        phrase.update(begin=begin, onset=onset, finish=finish,
                      final_frames=(finish-begin)//BLOCK+1)
        for w in phrase['windows']:
            w['end'] = round(w['time']*SR)
            w['t'] = (w['end']-policy.FUTURE_SAMPLES)//BLOCK+1 if w['end'] > policy.FUTURE_SAMPLES else 0
    return out


def assert_same(cached, fresh):
    assert cached['ends'] == fresh['ends']
    for a, b in zip(cached['phrases'], fresh['phrases'], strict=True):
        assert a['final'] == b['final']
        for u, v in zip(a['windows'], b['windows'], strict=True):
            assert u['start'] == v['start'] and np.array_equal(u['ids'], v['ids']), (u['time'])


# --------------------------------------------------------------------------
# Replay: caption state machine, queueing, stitching
# --------------------------------------------------------------------------

def ctc_text(ids):
    """Greedy CTC: collapse repeats, drop blank 28, strip outer spaces."""
    out, prev = [], -1
    for i in ids:
        if i != prev and i != BLANK:
            out.append(VOCAB[i])
        prev = i
    return ''.join(out).strip()


def ctc_words(ids, frames, begin):
    """Greedy CTC words, each with the time (s) of its last char's last frame.

    frames are phrase output-frame indices; begin is the phrase start sample.
    """
    words, word, last, prev = [], '', None, -1
    for i, f in zip(ids, frames):
        if i == BLANK:
            pass
        elif i == 0:
            if i != prev and word:
                words.append((word, last))
                word = ''
        elif i != prev:
            word += VOCAB[i]
            last = (begin+f*OUT_HOP)/SR
        else:
            last = (begin+f*OUT_HOP)/SR
        prev = i
    if word:
        words.append((word, last))
    return words


def simulate_clip(clip, lookahead, compute, window_macs):
    """Replay one clip; all times are integer samples from clip start.

    compute = service time (samples) of a full-size window. Single engine,
    FIFO. Preview requests use a latest-only slot: when the engine frees,
    every pending tick (captured at or before that instant) is merged into
    the newest one. A final costs compute * final MACs / full-window MACs.
    """
    hold = round(lookahead*50)        # withheld output frames (R)
    timeline = [event(0, None, 'Listening')]
    busy = 0
    ages, update_macs = [], []
    committed_stream, tentative_stream = [], []
    previews, tentative_previews, finals, final_times = [], [], [], []
    revised = tentative_seen = lost = 0
    for p, ph in enumerate(clip['phrases']):
        begin, onset, finish = ph['begin'], ph['onset'], ph['finish']
        # Ticks are begin + k*hop, k >= 1; served only if after detection and
        # strictly before the phrase end (Finalising pre-empts a tick at finish).
        ticks = [w for w in ph['windows'] if begin+w['end'] < finish]
        early = [w for w in ticks if begin+w['end'] <= onset]
        ticks = ticks[len(early):]
        newest = begin+early[-1]['end'] if early else begin   # newest audio already served
        prefix = [w for words in previews for w in words]
        timeline.append(event(onset, p, 'Speaking'))
        ids, frames, emitted = [], [], 0
        committed, tentative, prior_tail = [], [], None
        k = 0
        while k < len(ticks):
            while k+1 < len(ticks) and begin+ticks[k+1]['end'] <= busy:
                k += 1
            w = ticks[k]
            capture = begin+w['end']
            display = max(capture, busy)+compute
            busy = display
            ages.append(display-newest)
            newest = capture
            update_macs.append(w['macs'])
            stop = max(0, w['count']-hold)
            first = max(0, emitted-w['start'])
            # A merged jump can slide the window past `emitted`: those output
            # frames were never committed and are lost (counted, not hidden).
            lost += max(0, w['start']-emitted) if first < stop else 0
            if first < stop:
                ids.extend(w['ids'][first:stop].tolist())
                frames.extend(range(w['start']+first, w['start']+stop))
                emitted = w['start']+stop
            tail = max(stop, first)
            committed = ctc_words(ids, frames, begin)
            tentative = ctc_words(ids+w['ids'][tail:].tolist(),
                                  frames+list(range(w['start']+tail, w['start']+w['count'])), begin)
            # Tail revision: previous tentative-only word positions that change.
            now = [t for t, _ in tentative]
            if prior_tail is not None:
                revised += sum(i >= len(now) or now[i] != word for i, word in prior_tail)
                tentative_seen += len(prior_tail)
            prior_tail = list(enumerate(now))[len(committed):]
            committed_stream.append((display, prefix+committed))
            tentative_stream.append((display, prefix+tentative))
            timeline.append(event(display, p, 'Speaking', committed, tentative, capture=capture/SR,
                                  feature_count=w['t'], window_start=2*w['start'], emitted=emitted))
            k += 1
        previews.append(committed)
        tentative_previews.append(tentative)
        # The preview in flight at the end of speech is still shown first.
        timeline.append(event(max(finish, busy), p, 'Finalising', committed, tentative))
        busy = max(busy, finish)+round(compute*ph['final_macs']/window_macs)
        finals.append(ph['final'])
        final_times.append(busy)
        timeline.append(event(busy, p, 'Committed', committed, final=ph['final']))
        later = clip['phrases'][p+1:]
        if not later or later[0]['onset'] > busy:
            timeline.append(event(busy, None, 'Listening'))
    timeline.sort(key=lambda e: e['time'])     # stable: per-phrase order kept
    last_final = ''
    for item in timeline:
        if item['state'] == 'Committed':
            last_final = item['final']
        item['final'] = last_final
    return dict(timeline=timeline, ages=ages, macs=update_macs, previews=previews,
                tentative_previews=tentative_previews,
                finals=finals, final_times=final_times, committed=committed_stream,
                tentative=tentative_stream, revised=revised, tentative_seen=tentative_seen,
                lost=lost)


def event(time, phrase, state, preview=(), tentative=(), final='', **extra):
    return dict(time=time/SR, phrase=phrase, state=state, preview=words_text(preview),
                tentative=words_text(tentative), final=final, **extra)


def words_text(words):
    return ' '.join(w for w, _ in words)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def align(ref, hyp, ref_times=None, hyp_times=None):
    """Levenshtein word alignment; returns {ref index: hyp index} of matches.

    Ties between equal-edit alignments are broken by the smallest summed
    |hyp time - ref time| (ms) over matched words, so a sparse preview word is
    not attributed to a later repeat of the same reference word.
    """
    big = 10**7
    n, m = len(ref), len(hyp)

    def diag(i, j):
        if ref[i] != hyp[j]:
            return big
        return 0 if ref_times is None else min(big-1, round(abs(hyp_times[j]-ref_times[i])*1000))
    dp = [[j*big for j in range(m+1)]]
    for i in range(n):
        row = [(i+1)*big]
        up = dp[-1]
        for j in range(m):
            row.append(min(up[j+1]+big, row[j]+big, up[j]+diag(i, j)))
        dp.append(row)
    matches = {}
    i, j = n, m
    while i and j:
        d = diag(i-1, j-1)
        if dp[i][j] == dp[i-1][j-1]+d:
            if d < big:
                matches[i-1] = j-1
            i, j = i-1, j-1
        elif dp[i][j] == dp[i-1][j]+big:
            i -= 1
        else:
            j -= 1
    return matches


def word_errors(ref, hyp):
    """Word-level Levenshtein distance (WER numerator)."""
    n, m = len(ref), len(hyp)
    prev = list(range(m+1))
    for i in range(1, n+1):
        row = [i]
        for j in range(1, m+1):
            row.append(min(row[-1]+1, prev[j]+1, prev[j-1]+(ref[i-1] != hyp[j-1])))
        prev = row
    return prev[m]


def stable_onsets(stream, ref, ends):
    """Per reference word: first display time from which it stays correct.

    Each display's hypothesis is aligned to the whole-clip reference. A word's
    onset is the start of its final uninterrupted run of correct matches that
    lasts through the last display; None if it is wrong at the last display.
    """
    correct = []
    for _, words in stream:
        matches = align(ref, [w for w, _ in words], ends, [t for _, t in words])
        correct.append(set(matches))
    onsets = []
    for i in range(len(ref)):
        onset = None
        for (time, _), ok in zip(reversed(stream), reversed(correct)):
            if i not in ok:
                break
            onset = time
        onsets.append(onset)
    return onsets


def clip_metrics(clip, sim):
    ref = clip['reference'].split()
    ends = clip['ends']
    preview_hyp = [w for words in sim['previews'] for w, _ in words]
    final_words, final_owner = [], []
    for p, text in enumerate(sim['finals']):
        final_words += text.split()
        final_owner += [p]*len(text.split())
    in_final = align(ref, final_words)
    out = dict(words=len(ref), preview_errors=word_errors(ref, preview_hyp),
               tentative_errors=word_errors(ref, [w for words in sim['tentative_previews'] for w, _ in words]),
               final_errors=word_errors(ref, final_words),
               ages=[v/SR for v in sim['ages']], macs=sim['macs'], revised=sim['revised'],
               tentative_seen=sim['tentative_seen'], lost=sim['lost'])
    for name, stream in (('committed', sim['committed']), ('tentative', sim['tentative'])):
        onsets = stable_onsets([(t/SR, words) for t, words in stream], ref, ends)
        out[name] = [o-e for o, e in zip(onsets, ends) if o is not None]
        # User view: the preview word if it is stable and the final keeps it,
        # otherwise the final that first shows it; final errors are censored.
        user = []
        for i, end in enumerate(ends):
            if i in in_final:
                shown = sim['final_times'][final_owner[in_final[i]]]/SR
                if onsets[i] is not None:
                    shown = min(shown, onsets[i])
                user.append(shown-end)
        out[name+'_user'] = user
    return out


def percentiles(values):
    if not values:
        return None
    v = np.asarray(values)*1000
    return dict(p50=round(float(np.percentile(v, 50)), 1), p95=round(float(np.percentile(v, 95)), 1),
                negative=round(float(np.mean(v < 0)), 4))


def summarize(per_clip):
    words = sum(c['words'] for c in per_clip)
    gather = lambda key: [v for c in per_clip for v in c[key]]
    return dict(
        words=words,
        preview_wer=sum(c['preview_errors'] for c in per_clip)/words,
        tentative_wer=sum(c['tentative_errors'] for c in per_clip)/words,
        final_wer=sum(c['final_errors'] for c in per_clip)/words,
        gmac_update=float(np.mean(gather('macs')))/1e9,
        updates=len(gather('macs')),
        update_ms=percentiles(gather('ages')),
        word_ms=percentiles(gather('committed')),
        word_censored=words-len(gather('committed')),
        user_word_ms=percentiles(gather('committed_user')),
        user_word_censored=words-len(gather('committed_user')),
        tentative_word_ms=percentiles(gather('tentative')),
        tentative_word_censored=words-len(gather('tentative')),
        tentative_user_word_ms=percentiles(gather('tentative_user')),
        lost_frames=sum(c['lost'] for c in per_clip),
        tail_revision_fraction=sum(c['revised'] for c in per_clip)/max(1, sum(c['tentative_seen'] for c in per_clip)))


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def replay_task(task):
    """Worker: load/compute one clip's inference, replay every (R, c)."""
    row, window, padding, points, args = task
    clip = load_clip(row, window, padding, args)
    layers = load_layers(args.model)
    for ph in clip['phrases']:
        ph['final_macs'] = macs(layers, ph['final_frames'])
    full = macs(layers, window_frames(window, padding))
    results, timelines = [], {}
    for lookahead, compute in points:
        sim = simulate_clip(clip, lookahead, round(compute*SR/1000), full)
        results.append(clip_metrics(clip, sim))
        timelines[(lookahead, compute)] = sim['timeline']
    return results, timelines


def sweep_grid(a):
    if a.sweep:
        windows, paddings, rights = [1.5, 2, 3, 4, 6], ['zero', 'replicate', 'silence'], \
            [0, .16, .24, .32, .48, .64, .8]
    else:
        windows, paddings, rights = [a.window], [a.padding], [a.lookahead]
    return [(w, p, [(r, c) for r in rights for c in a.compute_ms]) for w in windows for p in paddings]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--model', default='data/int8.pkl')
    p.add_argument('--sweep', action='store_true', help='W x padding x R grid')
    p.add_argument('--clips', type=int, default=40)
    p.add_argument('--ids', nargs='+', help='restrict to these utterance ids')
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--window', type=float, default=policy.WINDOW)
    p.add_argument('--lookahead', type=float, default=policy.LOOKAHEAD)
    p.add_argument('--padding', choices=['zero', 'replicate', 'silence'], default=policy.PADDING)
    p.add_argument('--cap', type=float, default=policy.PHRASE_CAP)
    p.add_argument('--compute-ms', type=float, nargs='+', default=[60])
    p.add_argument('--cache', default='data/stream_cache_v1')
    p.add_argument('--recompute', action='store_true', help='ignore cache; assert equality with it')
    p.add_argument('--out', default='data/stream_eval.json')
    p.add_argument('--timelines', help='write per-clip caption timelines (first --compute-ms) here')
    p.add_argument('--finish-study', action='store_true', help='select policies, run sensitivity and export references after a complete sweep')
    a = p.parse_args()
    if a.finish_study:
        finish_study(a)
        return
    if not 0 < a.cap <= 15.9:
        p.error('phrase cap must fit <=800 output steps')
    layers = load_layers(a.model)
    a.digest = hashlib.sha256(Path(a.model).read_bytes()+f'v{INFERENCE_VERSION}-tail{REPLAY_TAIL:g}'.encode()).hexdigest()[:16]
    Path(a.cache).mkdir(parents=True, exist_ok=True)
    rows = testset()[:a.clips] if not a.ids else [r for r in testset() if r['id'] in a.ids]
    metadata = dict(digest=a.digest, clips=len(rows), ids=[r['id'] for r in rows],
                    cap=a.cap, metric_version=METRIC_VERSION)
    reports = []
    output = Path(a.out)
    if output.exists() and not a.recompute and not a.timelines:
        saved = json.loads(output.read_text())
        if all(saved.get(k) == v for k, v in metadata.items()):
            reports = saved['results']
        else:
            # Old replay metrics cannot be combined with this contract. Preserve
            # them for audit, then reuse their expensive inference cache only.
            backup = output.with_suffix('.previous.json')
            if backup.exists():
                raise ValueError(f'incompatible output and backup exists: {backup}')
            output.replace(backup)
    done = {(r['window'], r['padding'], r['lookahead'], r['compute_ms']) for r in reports}
    with ProcessPoolExecutor(a.workers) as pool:
        for window, padding, points in sweep_grid(a):
            points = [point for point in points if (window, padding, *point) not in done]
            if not points:
                continue
            outs = list(pool.map(replay_task, [(r, window, padding, points, a) for r in rows]))
            for n, (lookahead, compute) in enumerate(points):
                report = summarize([o[0][n] for o in outs])
                report.update(window=window, lookahead=lookahead, padding=padding, compute_ms=compute,
                              hop=policy.HOP, full_window_gmac=macs(layers, window_frames(window, padding))/1e9)
                reports.append(report)
            if a.timelines:
                Path(a.timelines).mkdir(parents=True, exist_ok=True)
                key = points[0]
                for row, (_, timelines) in zip(rows, outs):
                    doc = dict(id=row['id'], reference=row['text'], window=window, lookahead=key[0],
                               padding=padding, hop=policy.HOP, compute_ms=key[1],
                               events=timelines[key])
                    (Path(a.timelines)/f"{row['id']}.json").write_text(json.dumps(doc, indent=1), newline='\n')
            temporary = output.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(command=' '.join(['model/stream_sim.py']+sys.argv[1:]),
                                                 **metadata, results=reports), indent=1), newline='\n')
            temporary.replace(output)
            r = reports[-len(points)]
            print(f"W={window} {padding} R={r['lookahead']} c={r['compute_ms']}: preview {r['preview_wer']:.4f} "
                  f"final {r['final_wer']:.4f} word {r['word_ms']} censored {r['word_censored']}", flush=True)


def load_layers(model):
    with open(model, 'rb') as f:
        return pickle.load(f)['layers']


def finish_study(a):
    """Deterministic publication step; requires the complete current sweep.

    This intentionally fails on partial or old metrics rather than publishing
    an operating point selected from an incomplete experiment.
    """
    study = json.loads(Path(a.out).read_text())
    expected = {(w, p, r, c) for w, p, points in sweep_grid(argparse.Namespace(sweep=True, compute_ms=[60]))
                for r, c in points}
    results = study['results']
    actual = {(r['window'], r['padding'], r['lookahead'], r['compute_ms']) for r in results}
    if study.get('metric_version') != METRIC_VERSION or study.get('clips') != 40 or not expected <= actual:
        raise ValueError('publication requires complete 40-clip current-metric c=60 sweep')
    policies = {}
    commands = [study['command']]
    for name, limit in [('core', 1000), ('stretch', 250)]:
        candidates = [r for r in results if r['compute_ms'] == 60 and
                      r['update_ms']['p95'] <= limit and r['final_wer'] <= .10]
        # Best committed preview WER; smaller full window breaks ties.
        best = min(candidates, key=lambda r: (r['preview_wer'], r['full_window_gmac'], r['lookahead']))
        policies[name] = dict(window=best['window'], lookahead=best['lookahead'],
                              padding=best['padding'], hop=policy.HOP,
                              full_window_gmac=best['full_window_gmac'],
                              word_ms=best['word_ms'], word_censored=best['word_censored'])
        cmd = [sys.executable, __file__, '--clips', '40', '--workers', str(a.workers),
               '--window', str(best['window']), '--lookahead', str(best['lookahead']),
               '--padding', best['padding'], '--compute-ms', '60', '110', '300', '900',
               '--out', f'data/stream_{name}_sensitivity.json']
        commands.append(' '.join(cmd))
        subprocess.run(cmd, check=True)
    best = policies['core']
    cmd = [sys.executable, __file__, '--clips', '3', '--workers', str(a.workers),
           '--window', str(best['window']), '--lookahead', str(best['lookahead']),
           '--padding', best['padding'], '--compute-ms', '60',
           '--out', 'data/stream_reference.json', '--timelines', 'data/export/stream_ref']
    commands.append(' '.join(cmd))
    subprocess.run(cmd, check=True)
    source = Path(__file__).with_name('stream_policy.py')
    text = source.read_text()
    text = text[:text.index('\n# SELECTED STUDY POLICIES')] if '\n# SELECTED STUDY POLICIES' in text else text
    import re
    for constant, key in [('WINDOW', 'window'), ('LOOKAHEAD', 'lookahead'), ('PADDING', 'padding')]:
        text = re.sub(rf'^{constant} = .*$', f'{constant} = {best[key]!r}', text, flags=re.M)
    text = text.replace('Provisional buffered operating point;', 'Selected buffered operating point;')
    source.write_text(text+'\n# SELECTED STUDY POLICIES (c=60 ms; measured board acceptance remains separate)\n'
                      +'OPERATING_POINTS = '+repr(policies)+'\n', newline='\n')
    lines = ['# Streaming study', '',
             'Complete 40-clip simulation, current replay contract. These are assumed service times, not board measurements.', '',
             'Selection minimizes committed preview WER subject to final WER <=10% and the stated L_update p95 bound at c=60 ms. '
             'Core and stretch can select the same point because R is absent from L_update. '
             'L_word percentiles condition on observed words; always read the censored column.', '',
             '| W | R | pad | c ms | preview WER % | tentative WER % | final WER % | mean/full GMAC | L_update p95 ms | L_word p50/p95 ms | censored |',
             '|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    def table_row(r):
        word = r['word_ms'] or dict(p50=None, p95=None)
        return (f"|{r['window']}|{r['lookahead']}|{r['padding']}|{r['compute_ms']}|"
                f"{100*r['preview_wer']:.2f}|{100*r['tentative_wer']:.2f}|{100*r['final_wer']:.2f}|"
                f"{r['gmac_update']:.4f}/{r['full_window_gmac']:.4f}|{r['update_ms']['p95']}|"
                f"{word['p50']}/{word['p95']}|{r['word_censored']}|")
    lines += [table_row(r) for r in sorted(results, key=lambda r:(r['window'],r['padding'],r['lookahead'])) if r['compute_ms']==60]
    for name, selected in policies.items():
        lines += ['', f'## {name.capitalize()}', '', repr(selected), '',
                  '| W | R | pad | c ms | preview WER % | tentative WER % | final WER % | mean/full GMAC | L_update p95 ms | L_word p50/p95 ms | censored |',
                  '|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        lines += [table_row(r) for r in json.loads(Path(f'data/stream_{name}_sensitivity.json').read_text())['results']]
    lines += ['', '## Exact commands', '', '```']+commands+['```', '',
              'PL service estimates for a selected full window scale 60-110 ms by full_window_gmac / 3.016018; '
              'CPU estimates scale 300-900 ms by the same factor. The sensitivity rows instead specify c directly. '
              'Final service scales by final/full-window MACs and blocks subsequent previews. '
              'Latest-only merging preserves the oldest unserved audio timestamp; lost stitch frames are recorded in JSON.', '']
    Path('docs/results/streaming.md').write_text('\n'.join(lines), newline='\n')


if __name__ == '__main__':
    main()
