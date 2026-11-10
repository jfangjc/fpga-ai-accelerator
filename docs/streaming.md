# Streaming caption specification (buffered QuartzNet INT8)

This is the contract the board application implements. The executable
reference is `model/stream_sim.py`; every constant comes from
`model/stream_policy.py`, the single source of truth that the C application
mirrors. The measured trade-offs behind the chosen operating points are in
`docs/results/streaming.md`. Run all commands from the repository root.

Notation: samples are 16 kHz. W is the window (s), R the lookahead (s), h the
hop (s), c the service time of one full-size preview window (s). `//` is
floor division. All indices are 0-based, and all intervals are half-open.

## 1. Why buffered inference

QuartzNet's convolutions are centred. An output frame at the right edge of
any window lacks its future context, and its theoretical receptive field is
about 80 s. The phrase-final re-decode (Section 7) is the accurate result.
Previews instead re-run the network every hop on the newest W seconds and
only trust output frames at least R seconds from the right edge. The
proposal's Fig. 3 describes the states: Listening, then Speaking (a preview
every 120 ms), then Finalising (one pass over the whole phrase after 700 ms of
silence), then Committed.

## 2. VAD and phrases (`phrases`)

* The clip is scanned in non-overlapping 160-sample blocks on the absolute
  lattice `[160k, 160k+160)` (the last block may be short). A block is
  **active** when `sqrt(mean(x^2)) >= VAD_RMS`. The input is float in [-1, 1],
  and the mean is computed in float64.
* Listening: the first active block at `pos` starts a phrase.
  `onset = pos+160` (detection happens at the end of the block).
  `begin = max(boundary, pos - VAD_LEAD*16000)`, where `boundary` is the
  previous phrase's `finish` (0 initially). The application therefore keeps
  at least `VAD_LEAD` (200 ms) of raw audio in a ring while Listening.
* Speaking: each inactive block increments `silent`, and an active block
  resets it to 0. The phrase ends at the end of the current block
  (`finish = pos+160`) when `silent >= VAD_SILENCE*100` (70 blocks) **or**
  `pos+160-begin >= PHRASE_CAP*16000` (lead-in and silence included). After a
  cap, the very next active block starts a new phrase with `begin = finish`.
* EOF flushes an open phrase at `len(x)`. Phrases of 256 samples or fewer are
  dropped. The cap of 15 s keeps the final pass at 800 or fewer output steps
  (`PHRASE_CAP <= 15.9`).
* Replay protocol: the bench injects WAV clips followed by digital silence.
  The reference appends `REPLAY_TAIL` = 0.8 s of zeros to every clip, so a
  phrase ends by the VAD rule as it would live. (LibriSpeech clips end about
  0.4 s after the last word, so without the tail, 39 of 40 clips ended in an
  EOF flush. That withheld the last R s of every committed preview and was a
  simulation artifact. Final WER is identical with and without the tail:
  4.2453 %.)

## 3. Feature frames (per phrase)

Phrase audio is `a[n] = x[begin+n]`, `0 <= n < L = finish-begin`. The
frontend restarts at every phrase. It reads the lead-in from the ring.

* Preemphasis: `y[0] = a[0]` and `y[n] = a[n] - 0.97*a[n-1]` (no history from
  before `begin`).
* Frame j is centred on phrase sample `160*j`. It uses a 512-point FFT, a
  320-sample non-periodic Hann window and the Slaney mel bank (`frontend.py`).
  Reflect padding is applied only at the phrase start, because centred frames
  0 and 1 reach before sample 0.
* A frame is final once its last sample exists. After N phrase samples, the
  safe frame count is `t(N) = (N-256)//160 + 1` if `N > 256`, else 0. No
  right-edge reflection ever reaches a consumed frame. At tick k
  (`N = 1920k`), `t = 12k - 1`.
* Running normalisation: Welford state `{count, mean[64], m2[64]}` (double)
  is reset to the dev prior `running_prior.json` at every phrase. For each new
  frame, `z = log(mel + 2^-24)`, `count += 1`, `delta = z-mean`,
  `mean += delta/count`, `m2 += delta*(z-mean)`, and
  `f = (z-mean)/(sqrt(m2/(count-1)) + 1e-5)`, stored as float32. Normalised
  frames are retained, at least W s plus one frame.

## 4. Window per tick (`run_window`)

Ticks occur at phrase samples `N = k*HOP*16000` (k = 1, 2, ...; 1920 samples).
A tick is served only if `begin+N > onset` (strictly after detection) and
`begin+N < finish` (Finalising pre-empts a tick that coincides with the end).

1. `t = t(N)`, `s = max(0, t - round(100*W))`, `s -= s % 2`. The even start
   keeps the global stride-2 output lattice of C1.
2. Input `F = frames[s:t]` (`real = t-s` columns).
3. Edge padding (`PADDING`) appends `SYNTHETIC_FRAMES` = 16 frames
   (160 ms) that are **valid** network input:
   * `zero`: append nothing. The network's own zero padding and valid-length
     masking act at the edge. A normalised zero is the mean feature, not
     silence.
   * `replicate`: 16 copies of the last real frame `F[:, real-1]`.
   * `silence`: 16 copies of the digital-silence floor normalised with the
     **fixed dev prior**:
     `((float)log(2^-24) - prior.mean) / (sqrt(prior.m2/(prior.count-1)) + 1e-5)`,
     computed in double and cast once to float32. This is a constant
     64-vector computed at start-up. Values are about -1.85 to -1.45, so it
     does not saturate.
4. Valid length `n = real + extra` (`extra` = 0 or 16). Quantise in float32,
   `q = clamp(round_half_away(f / input_scale_f32), -128, 127)`. Allocate
   `ceil(n/16)*16` columns, zero beyond n. Run the INT8 network with valid
   length n (each convolution clears inputs beyond its propagated valid
   length; quant_spec.md).
5. Output valid length is `ceil(n/2)`. Keep only the **real** outputs:
   `count = (real+1)//2`. Take the argmax per column (ties: lowest id). Local
   output j is global phrase output frame `g = s/2 + j`. Its centre is phrase
   sample `320*g`, i.e. clip time `(begin + 320g)/16000`.

## 5. Stitching (committed and tentative)

Per phrase, keep `ids[]` (committed argmax ids) and `emitted` (next global
output frame not yet committed), both reset to 0 at Speaking.

```
H     = round(50*R)                     # withheld output frames
stop  = max(0, count - H)               # local, exclusive
first = max(0, emitted - s/2)           # local
if first < stop:
    append ids_window[first:stop]; emitted = s/2 + stop
tail  = max(stop, first)
committed text = ctc(ids)
tentative text = ctc(ids ++ ids_window[tail:count])
```

`ctc` collapses repeats **across the whole id sequence**, then drops blank 28
(vocabulary: space, a-z, apostrophe) and strips outer spaces. Never decode
slices separately and concatenate strings. The committed text only grows. The
tentative tail may be revised by the next update. If a merged jump (Section 6)
moves `s/2` beyond `emitted`, frames `[emitted, s/2)` are lost. The simulator
counts these as `lost_frames`; this happens only under heavy backlog.

## 6. Engine queue, timing and L_update

One inference engine runs requests FIFO. Capture, VAD and the frontend keep
running during inference. Preview requests use a **latest-only slot**: when
the engine becomes free at time b, every tick captured at or before b is
merged into the newest one, so a stale window is never computed. The final of
a phrase is queued at `finish` and takes `c_final = c * MACs(final) /
MACs(full preview window)`.

```
display  = max(capture, engine_free) + c
L_update = display - newest_audio_already_served
```

The newest audio already served is the capture time of the previously served
tick. For the first update of a phrase it is the last tick at or before
`onset`, or `begin` if there is none. Lead-in audio from before detection
cannot be shown before detection and is not counted. With no backlog,
`L_update = h + c` exactly. With `c > h`, latest-only merging gives roughly
`c + (c rounded up to the next tick)`. The proposal defines preview latency
as "the age of the oldest audio in each update, including queueing". The
literal oldest sample *in the window* is always W old, so the only meaningful
reading is the oldest *new* audio, used here. Note that L_update does not
depend on R. It is not the delay before a word is readable; that is L_word.

## 7. Final (Finalising, then Committed)

When the phrase ends, recompute features of `a[0:L]` from scratch. Reflect at
both phrase ends and use **utterance** normalisation (per-band mean and
sample std over the whole phrase, plus 1e-5; this includes lead-in and
trailing silence). Keep `n = L//160 + 1` frames, quantise, and run the network
with valid length n. Take the greedy CTC result. The final replaces the
phrase's preview.

## 8. Caption state machine and reference timelines

| State | Entered at | Display |
|---|---|---|
| Listening | start, or Committed when no new phrase has begun | finals so far |
| Speaking | `onset`; one preview event per served tick at `display` | finals + committed preview (+ tentative tail, styled as provisional) |
| Finalising | `max(finish, completion of the preview in flight)` | last preview |
| Committed | final completion (`engine_free` after the final) | the final replaces the preview |

Phrase p+1 may reach Speaking before phrase p is Committed (after a cap, or
when speech resumes right after 700 ms). Its previews queue behind the final.
Events therefore carry the phrase index.

Reference timelines `data/export/stream_ref/<id>.json` (see
`docs/results/streaming.md` for the generating command) are produced at the
core policy with **c = 0**. Every tick is then served and nothing merges, so
the content is independent of the board's speed. Fields: `time` (s from clip
start, display time), `phrase`, `state`, `preview` (committed), `tentative`,
`final`. Speaking events also carry `capture` (tick time), `feature_count`
(t), `window_start` (s, in feature frames) and `emitted`. In an offline
"serve every tick" mode, the C application must reproduce every event's text
and indices exactly. Live, ticks merge and only the times differ.

## 9. Metrics

* **Preview WER**: the word edit distance between the reference and the
  concatenation of each phrase's committed preview as displayed at the phrase
  end (the last served update before Finalising), over all words.
  **Final WER**: the same using the finals.
* **Word end times** come from a reference-constrained CTC Viterbi over the
  float model's full-clip logits (blank between characters; stay, advance
  one, or skip a blank between distinct characters). A word's end is the last
  20 ms frame assigned to its last character. These are model-derived
  estimates of the emission point, not human acoustic endpoints, and they
  can precede the acoustic end by a frame or two.
* **L_word** (preview only): for each reference word, the display time from
  which it is shown correctly **and stays correct through the end of the
  clip's preview stream**, minus its end time. At every display, the
  hypothesis (earlier phrases' previews plus the current one, each word
  timestamped by its last character's frame) is aligned to the full
  reference. Ties between equal-edit alignments are broken by the smallest
  summed |hyp time - ref time|. The "censored" count is the words that are
  wrong in the final preview. Those are never shown correctly by the
  preview, so the count is roughly preview WER times words.
  Percentiles cover the uncensored words only, so they must always be
  quoted together with the count.
* **User L_word** (no censoring except final errors): a word counts as shown
  at the earlier of its stable preview onset and its phrase's final
  completion, provided the final contains it. Only words that the final gets
  wrong are excluded. This is the honest user-facing latency.
* **Tentative variants**: the same metrics computed on committed text plus
  the tentative tail.
* **Tail revision fraction**: of the word positions shown as tentative-only
  at one update, the fraction that differ or vanish at the next.
* **GMAC/update**: the mean over served updates of executed padded MAC slots
  (T16 / O32 / flattened I*K 16; depthwise O32 x T16 x taps). The mean
  includes the shorter start-up windows. `full_window_gmac` is the
  steady-state value. These figures are not cycle estimates.

### Metric sanity checks (from the inherited sweep)

The earlier sweep reported a **negative L_word p50 (-50 ms) at R = 0** with
759 of 848 words censored. Three effects caused it:

1. At R = 0 every frame is committed from the window's right edge, where
   there is no future context. The committed preview is mostly blanks (about
   90 % WER, mostly deletions), so few words survive. The censoring itself
   was correct: those words are never shown correctly.
2. The old per-display Levenshtein backtrace broke ties arbitrarily (it
   preferred the diagonal from the end). For a sparse hypothesis it attributed
   an early "the" to a later "the" in the reference, which produced negative
   latencies. The current alignment breaks ties by time.
3. With EOF-flushed phrases, the last R s were never committed, which added
   about one censored word per clip at large R.

Since the fix, negative values are rare. The fraction is reported as
`negative`, and the remainder reflects real alignment-model disagreement. The
censored count tracks preview errors, as expected. Preview WER is computed
on the committed preview at the phrase end, not on the tentative tail and not
on the final.

## 10. Inference cache and validation

`data/stream_cache_v1/{base,win}-<id>-cap<cap>-<digest>[-W<W>-<padding>].pkl`.
The digest covers the model blob, `INFERENCE_VERSION` and `REPLAY_TAIL`.
Bump `INFERENCE_VERSION` whenever inference semantics change. Changes to the
replay or metrics code do not invalidate the cache. `--recompute` ignores the
cache and asserts that every window's ids equal the cached ids. Exact BLAS:
ordinary convolutions are summed in double, which is exact because the
products and partial sums stay below 2^53 (the bound is checked). Bias,
overflow checks, Q31 rounding, residuals and masking remain those of
`golden_int8`. A version-1 bug, where silence padding promoted the window to
float64 before quantisation and so violated the float32 contract, was fixed
in version 2.


## Resume and publication contract

The current inference identity is v3 with 800 ms of digital silence appended to
paced clip replay. Legacy hashed window caches and the partial 104-row sweep
used EOF phrase termination and older replay metrics. They are preserved for
audit, but cannot be mixed with current results. New versioned base/window
caches live alongside them. This necessary inference change prevents a direct
resume of those legacy rows. Compatible current runs skip completed
(W, padding, R, c) rows and reuse cached clips. Each completed W/padding group
atomically replaces the JSON. An interrupted group replays from cached clips.
Only a single writer may target an output JSON.

The complete grid is W={1.5,2,3,4,6}, R={0,.16,.24,.32,.48,.64,.8},
padding={zero,replicate,silence}, at c=60 ms (105 configurations). Every row
also reports tentative-tail WER and stable-word percentiles. Committed preview
WER uses the last served stitched preview of each phrase, concatenated across
phrases; it never substitutes the final re-decode or tentative suffix.
A withheld suffix can remain absent at phrase end; its words are censored.

A negative conditional L_word percentile is possible: Viterbi word ends are
last-character model frames, whereas the preview may already spell that word.
At R=0 the small surviving set also strongly selects easy, early predictions.
Do not clamp negatives or interpret conditional p50 as median latency of all
reference words. Alignment tie-breaking uses decoded acoustic frame times,
not display times, to disambiguate repeated words. CTC word timing, character
collapse across slices, edit-distance matching, censor reset after a revision,
and queue arithmetic have focused synthetic checks.

With no backlog regular L_update=h+c: 180 ms at c=60 and 230 ms at c=110.
When c>h, latest-only merging gives roughly c to 2c+h steady-state age;
first updates, phrase boundaries and serial final passes change percentiles.
R delays word evidence but does not increase this update metric. It is thus
possible for core and stretch to choose exactly the same best-WER point at
c=60 while words arrive much later than 250 ms.

After a complete sweep, publish selected policies, the complete table,
40-clip c-sensitivity, and three core timelines with:

```
.venv/bin/python model/stream_sim.py --finish-study --out data/stream_sweep_v4.json --workers 8
```

Publication rejects incomplete/old sweeps. Selection minimizes committed
preview WER subject to final WER <=10% and measured simulated L_update p95
<=1000 (core) or <=250 ms (stretch), at c=60 ms; full-window GMAC breaks ties.
The resulting OPERATING_POINTS in stream_policy.py carries each W/R/pad,
GMAC and honest conditional L_word/censor counts; shared constants specify
hop, VAD, lead-in, and cap. Until that publication succeeds, defaults remain
provisional. This LibriSpeech study does not establish acceptance on the
proposal's VoxPopuli and own-speech datasets or board latency.
