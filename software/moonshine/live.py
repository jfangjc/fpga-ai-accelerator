"""Continuous capture with bounded buffering, energy VAD and revisable ASR.

Run the microphone source inside run_moonshine_board.py. Paced WAV replay uses
the same capture queue, endpoint state machine, model and output event protocol.
"""
import argparse
from collections import deque
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

from .capture import BLOCK, RATE, Block, BufferedCapture, Highpass, Microphone, Replay, microphone_device
from .transcript import trim_overlap


def dbfs(samples):
    return float(20*np.log10(max(float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))), 1e-12)))


class Engine:
    """Endpoint decisions use audio time, independent of inference delays."""
    def __init__(self, factory, emit, chunk_ms=2500, threshold=-55, silence_ms=700,
                 pre_roll_ms=300, attack_ms=60, min_speech_ms=200, max_seconds=12, gain=1,
                 overlap_ms=0, pause_after_seconds=None, pause_ms=250, max_refinements=4):
        values = (chunk_ms, silence_ms, pre_roll_ms, attack_ms, min_speech_ms, max_seconds, gain)
        if not all(math.isfinite(x) and x > 0 for x in values) or not -120 < threshold < 0:
            raise ValueError('Invalid live segmentation configuration')
        if max_seconds > 30 or chunk_ms > max_seconds*1000 or pre_roll_ms >= max_seconds*1000:
            raise ValueError('Chunk/pre-roll must fit the utterance limit (at most 30 seconds)')
        self.factory, self.emit = factory, emit
        self.chunk = int(chunk_ms*RATE/1000)
        self.silence_limit, self.attack_limit = int(silence_ms*RATE/1000), int(attack_ms*RATE/1000)
        self.pre_limit, self.min_speech = int(pre_roll_ms*RATE/1000), int(min_speech_ms*RATE/1000)
        if self.pre_limit < self.attack_limit:
            raise ValueError('Pre-roll must include the full speech attack')
        self.max_samples, self.threshold, self.gain = int(max_seconds*RATE), threshold, gain
        if not math.isfinite(overlap_ms) or not 0 <= overlap_ms < max_seconds*1000/2:
            raise ValueError('Boundary overlap must be smaller than half the phrase')
        if pause_after_seconds is not None and (not math.isfinite(pause_after_seconds)
                or not 0 < pause_after_seconds <= max_seconds or not math.isfinite(pause_ms)
                or not 0 < pause_ms <= silence_ms):
            raise ValueError('Invalid natural-pause boundary configuration')
        if max_refinements < 1:
            raise ValueError('Positive refinement queue limit required')
        self.overlap = int(overlap_ms*RATE/1000)
        self.pause_after = int(pause_after_seconds*RATE) if pause_after_seconds else None
        self.pause_limit = int(pause_ms*RATE/1000)
        self.tail, self.tail_samples = deque(), 0
        self.continuation_end = None
        self.refinements, self.max_refinements = deque(), max_refinements
        self.refinement_high_water = 0
        self.previous_final = None
        if self.pre_limit+BLOCK > self.max_samples:
            raise ValueError('Utterance limit must leave room for pre-roll plus a capture block')
        self.pre = deque()
        self.pre_samples = self.attack = self.silence = 0
        self.position = self.utterances = self.finals = self.incomplete = self.discarded = self.clipped = 0
        self.stream = None
        self.pending = []
        self.pending_samples = self.utterance_samples = self.voiced = 0
        self.last_end_ns = 0
        self.max_lag_ms = 0.
        self.max_update_age_ms = 0.
        self.meter_samples = 0
        self.meter_power = 0.
        self.meter_peak = 0.
        self.nonzero = 0

    def _event(self, kind, **data):
        self.emit({'type': kind, **data})

    def _decode(self, final, reason=None):
        audio = np.concatenate(self.pending) if self.pending else np.empty(0, np.float32)
        self.pending, self.pending_samples = [], 0
        audio = audio*self.gain
        self.clipped += int(np.count_nonzero(np.abs(audio) > 1))
        np.clip(audio, -1, 1, out=audio)
        start = time.monotonic()
        event = self.stream.push(audio, final=final)
        lag = max(0., (time.monotonic_ns()-self.last_end_ns)/1e6)
        self.max_lag_ms = max(self.max_lag_ms, lag)
        lookahead_ms = (0 if event.get('provisional_encoder') else
                        getattr(self.stream, 'lookahead_samples', 0)*1000/RATE)
        # Conservative age of the oldest audio in this update, including the
        # encoder's future-context allowance. This is not word-aligned latency.
        update_age = lag+len(audio)*1000/RATE+lookahead_ms
        self.max_update_age_ms = max(self.max_update_age_ms, update_age)
        complete = not event.get('token_limit_reached', False)
        deferred = final and event.get('refinement_pending', False)
        kind = 'final-pending' if deferred else (('final' if complete else 'incomplete') if final else 'partial')
        data = dict(utterance_id=self.utterances,
            audio_start_sample=self.start_sample, audio_end_sample=self.end_sample,
            utterance_seconds=self.utterance_samples/RATE, source_end_ns=self.last_end_ns,
            processing_seconds=time.monotonic()-start, lag_ms=lag, reason=reason,
            update_audio_ms=len(audio)*1000/RATE, encoder_lookahead_ms=lookahead_ms,
            oldest_update_age_ms=update_age,
            profile=dict(self.stream.profile), decode_stats=dict(self.stream.decode_stats), **event)
        if deferred:
            if len(self.refinements) >= self.max_refinements:
                raise RuntimeError('Refinement queue exhausted; final captions were not silently discarded')
            self.refinements.append((self.stream.final_job, data, self.stream))
            self.refinement_high_water = max(self.refinement_high_water, len(self.refinements))
        elif final and complete:
            self._reconcile(data)
        self._event(kind, **data)
        if deferred:
            return None
        return complete

    def _reconcile(self, data):
        raw = data['text']
        previous = self.previous_final
        if previous and data['audio_start_sample'] < previous['audio_end_sample']:
            overlap_seconds = (previous['audio_end_sample']-data['audio_start_sample'])/RATE
            data['text'], removed, uncertain = trim_overlap(previous['raw_text'], raw,
                                                            max(2, math.ceil(overlap_seconds*6)))
            data.update(overlap_words_removed=removed, overlap_uncertain=uncertain)
        data['raw_text'] = raw
        self.previous_final = dict(data)

    def advance_refinement(self, budget_ms=5):
        """Soft budget: one native model call cannot be preempted.

        The live loop invokes this only after draining queued capture blocks.
        Models and FPGA transport are never called concurrently.
        """
        deadline = time.monotonic()+budget_ms/1000
        while self.refinements:
            job, data, stream = self.refinements[0]
            result = job.advance()
            if result is not None:
                self.refinements.popleft()
                data = dict(data)
                data.update(result, final=True, refinement_pending=False, refined=True)
                for key in ('window_text', 'window_start_frame', 'window_frames', 'overlap_uncertain'):
                    data.pop(key, None)
                data['processing_seconds'] += job.processing_seconds
                data['refinement_processing_seconds'] = job.processing_seconds
                data['profile'] = dict(stream.profile)
                data['profile']['decoder_seconds'] += job.processing_seconds
                data['decode_stats'] = {k: stream.decode_stats.get(k, 0)+job.stats[k] for k in job.stats}
                data['lag_ms'] = max(0., (time.monotonic_ns()-data['source_end_ns'])/1e6)
                data['oldest_update_age_ms'] = data['lag_ms']+data['update_audio_ms']+data['encoder_lookahead_ms']
                self.max_lag_ms = max(self.max_lag_ms, data['lag_ms'])
                self.max_update_age_ms = max(self.max_update_age_ms, data['oldest_update_age_ms'])
                complete = not result['token_limit_reached']
                self.finals += int(complete)
                self.incomplete += int(not complete)
                if complete:
                    self._reconcile(data)
                self._event('final' if complete else 'incomplete', **data)
            if time.monotonic() >= deadline:
                break

    def _remember(self, samples, end_ns, end_sample, level):
        if not self.overlap:
            return
        self.tail.append((samples.copy(), end_ns, end_sample, level))
        self.tail_samples += len(samples)
        while self.tail_samples > self.overlap:
            first = self.tail.popleft()
            remove = min(len(first[0]), self.tail_samples-self.overlap)
            self.tail_samples -= remove
            if remove < len(first[0]):
                self.tail.appendleft((first[0][remove:], *first[1:]))

    def _finish(self, reason):
        if self.stream is None:
            return
        if self.voiced >= self.min_speech:
            complete = self._decode(True, reason)
            if complete is True:
                self.finals += 1
            elif complete is False:
                self.incomplete += 1
        else:
            self.discarded += 1
            self._event('discarded', utterance_id=self.utterances, reason='short-speech',
                        audio_start_sample=self.start_sample, audio_end_sample=self.end_sample)
        tail = list(self.tail) if reason == 'max-duration' else []
        cut = self.end_sample
        self.stream = None
        self.pending = []
        self.pending_samples = self.utterance_samples = self.voiced = self.silence = self.attack = 0
        self.pre.clear()
        self.pre_samples = 0
        self.tail.clear()
        self.tail_samples = 0
        self.continuation_end = cut if tail else None
        if tail:
            self.pre.extend(tail)
            self.pre_samples = sum(len(item[0]) for item in tail)

    def _append(self, samples, end_ns, end_sample, level, allow_partial=True):
        room = self.max_samples-self.utterance_samples
        if len(samples) > room:
            split_end_ns = end_ns-(len(samples)-room)*1000000000//RATE
            self._append(samples[:room], split_end_ns, end_sample-len(samples)+room, level, allow_partial)
            # The hard limit starts a new utterance; preserve the remainder in
            # pre-roll rather than losing audio at a block boundary.
            self._accept(samples[room:], end_ns, end_sample, dbfs(samples[room:]), allow_partial)
            return
        self.pending.append(samples.copy())
        self._remember(samples, end_ns, end_sample, level)
        self.pending_samples += len(samples)
        self.utterance_samples += len(samples)
        self.last_end_ns, self.end_sample = end_ns, end_sample
        if level >= self.threshold:
            self.voiced += len(samples)
        self.silence = self.silence+len(samples) if level < self.threshold-5 else 0
        if self.utterance_samples >= self.max_samples:
            self._finish('max-duration')
        elif self.silence >= self.silence_limit:
            self._finish('silence')
        elif self.pause_after and self.utterance_samples >= self.pause_after and self.silence >= self.pause_limit:
            self._finish('natural-pause')
        elif allow_partial and self.pending_samples >= self.chunk and self.voiced >= self.min_speech:
            self._decode(False)

    def _accept(self, samples, end_ns, end_sample, level, allow_partial=True):
        if self.stream is not None:
            self._append(samples, end_ns, end_sample, level, allow_partial)
            return
        self.pre.append((samples.copy(), end_ns, end_sample, level))
        self.pre_samples += len(samples)
        if self.continuation_end is not None and end_sample-self.continuation_end >= self.overlap:
            self.continuation_end = None
        pre_limit = max(self.pre_limit, self.overlap) if self.continuation_end is not None else self.pre_limit
        while self.pre and self.pre_samples-len(self.pre[0][0]) >= pre_limit:
            self.pre_samples -= len(self.pre.popleft()[0])
        self.attack = self.attack+len(samples) if level >= self.threshold else 0
        if self.attack < self.attack_limit:
            return
        pre = list(self.pre)
        self.pre.clear()
        self.pre_samples = 0
        self.utterances += 1
        self.stream = self.factory()
        self.start_sample = pre[0][2]-len(pre[0][0])
        self._event('utterance-start', utterance_id=self.utterances, audio_start_sample=self.start_sample)
        # Pre-roll deliberately includes silence before onset; it cannot itself
        # trigger a silence endpoint while the current attack is still speech.
        self.pending = [item[0] for item in pre]
        self.pending_samples = self.utterance_samples = sum(len(item[0]) for item in pre)
        self.voiced = sum(len(item[0]) for item in pre if item[3] >= self.threshold
                         and (self.continuation_end is None or item[2] > self.continuation_end))
        self.continuation_end = None
        for item in pre:
            self._remember(*item)
        self.last_end_ns, self.end_sample = pre[-1][1:3]
        self.silence = 0
        if allow_partial and self.pending_samples >= self.chunk and self.voiced >= self.min_speech:
            self._decode(False)

    def push(self, block, allow_partial=True):
        """Always consume audio; callers may coalesce stale partial hypotheses.

        Endpoint/final decoding remains mandatory even while catching up.
        """
        samples = np.asarray(block.samples, dtype=np.float32)
        if samples.ndim != 1 or not 0 < len(samples) <= BLOCK or not np.isfinite(samples).all():
            raise ValueError('Expected finite mono capture blocks of at most 20 ms')
        self.position += len(samples)
        self.nonzero += int(np.count_nonzero(samples))
        self.meter_power += float(np.square(samples, dtype=np.float64).sum())
        self.meter_samples += len(samples)
        self.meter_peak = max(self.meter_peak, float(np.max(np.abs(samples))))
        level = dbfs(samples)
        self._accept(samples, block.end_ns, self.position, level, allow_partial)
        if self.meter_samples >= RATE:
            self._event('level', audio_seconds=self.position/RATE,
                rms_dbfs=20*math.log10(max(math.sqrt(self.meter_power/self.meter_samples), 1e-12)),
                peak=self.meter_peak, speech_active=self.stream is not None)
            self.meter_samples, self.meter_power, self.meter_peak = 0, 0., 0.

    def finish(self):
        self._finish('source-eof')
        while self.refinements:
            self.advance_refinement()

    def summary(self):
        return {'samples': self.position, 'audio_seconds': self.position/RATE,
                'utterances': self.utterances, 'finals': self.finals, 'discarded': self.discarded,
                'incomplete': self.incomplete,
                'pending_refinements': len(self.refinements),
                'refinement_queue_high_water': self.refinement_high_water,
                'clipped_samples': self.clipped, 'all_zero_audio': self.nonzero == 0,
                'max_transcript_lag_ms': self.max_lag_ms,
                'max_oldest_update_age_ms': self.max_update_age_ms}


def main():
    import signal
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--wav', type=Path, help='Paced replay instead of the physical microphone')
    p.add_argument('--unpaced', action='store_true', help='Replay test only; not a live latency benchmark')
    p.add_argument('--capture-helper', type=Path, default=Path('build/native/voice_capture'))
    p.add_argument('--capture-seconds', type=float, default=0, help='Microphone duration; zero means continuous')
    p.add_argument('--mic-input', choices=('primary', 'secondary'), help='Selectable capture image only')
    p.add_argument('--mic-channel', choices=('left', 'right'), help='Selectable capture image only')
    p.add_argument('--buffer-seconds', type=float, default=10)
    p.add_argument('--size', choices=('small', 'tiny'), default='small')
    p.add_argument('--backend', choices=('cpu-int8', 'oracle', 'fpga'), default='fpga')
    p.add_argument('--library', default='build/native/libqnc_moonshine.so')
    p.add_argument('--native-projections', action='store_true', help='FPGA: exact native input quantization and output restoration; requires rebuilt library')
    p.add_argument('--ort-decoder', type=Path, required=True)
    p.add_argument('--ort-encoder', type=Path)
    p.add_argument('--threads', type=int)
    p.add_argument('--native-threads', type=int, default=4)
    p.add_argument('--decoder-step-threads', type=int, help='Separate native thread count for one-token decoder calls')
    p.add_argument('--decoder-generic-step', action='store_true', help='Use the original graph for one-token decoder calls even when specialized artifacts exist')
    p.add_argument('--low-latency', action='store_true', help='Experimental Tiny: coalesce queued partials, 250 ms updates, 3 s context, 350 ms endpoint; shorter context can reduce accuracy')
    p.add_argument('--accuracy', action='store_true', help='Tiny: overlapping previews, natural pause boundaries and cooperative full-phrase final refinement')
    p.add_argument('--provisional-preview', action='store_true', help='Experimental Tiny accuracy mode: unfinished encoder tail, 250 ms updates, one-second revisable previews')
    p.add_argument('--preview-seconds', type=float)
    p.add_argument('--preview-overlap-seconds', type=float)
    p.add_argument('--overlap-ms', type=float, help='Audio context retained across forced phrase boundaries')
    p.add_argument('--pause-after-seconds', type=float, default=6)
    p.add_argument('--pause-ms', type=float, default=250)
    p.add_argument('--pre-roll-ms', type=float, default=300)
    p.add_argument('--attack-ms', type=float, default=60)
    p.add_argument('--min-speech-ms', type=float, default=200)
    p.add_argument('--encoder-precision', choices=('int8', 'float'), default='int8')
    p.add_argument('--encoder-scheduling', choices=('sleep', 'run-spin'), default='sleep')
    p.add_argument('--decoder-precision', choices=('int8', 'float'), default='int8')
    p.add_argument('--chunk-ms', type=int)
    p.add_argument('--vad-dbfs', type=float, default=-55)
    p.add_argument('--silence-ms', type=int)
    p.add_argument('--max-seconds', type=float)
    p.add_argument('--gain', type=float, default=1)
    p.add_argument('--highpass-hz', type=float, help='DC blocker: default 30 Hz for microphone, 0 for WAV replay')
    p.add_argument('--out', type=Path, help='New JSONL event log; audio is not stored')
    p.add_argument('--display', choices=('json', 'text'), default='json')
    a = p.parse_args()
    if a.provisional_preview:
        a.accuracy = True
    if a.preview_seconds is None:
        a.preview_seconds = 1 if a.provisional_preview else 2
    if a.preview_overlap_seconds is None:
        a.preview_overlap_seconds = .5 if a.provisional_preview else 1
    if a.accuracy:
        a.low_latency = True
    if a.low_latency and a.size != 'tiny':
        p.error('--low-latency is currently qualified only for Tiny')
    if a.low_latency and not a.ort_encoder:
        p.error('--low-latency requires --ort-encoder with Tiny artifacts')
    a.silence_ms = a.silence_ms if a.silence_ms is not None else (350 if a.low_latency and not a.accuracy else 700)
    a.max_seconds = a.max_seconds if a.max_seconds is not None else (3 if a.low_latency and not a.accuracy else 12)
    a.overlap_ms = a.overlap_ms if a.overlap_ms is not None else (1000 if a.accuracy else 0)
    if a.accuracy and (not math.isfinite(a.preview_seconds) or not math.isfinite(a.preview_overlap_seconds)
            or not 0 <= a.preview_overlap_seconds < a.preview_seconds <= a.max_seconds):
        p.error('Preview overlap/context must be finite, positive, and fit the phrase')
    if a.unpaced and not a.wav:
        p.error('--unpaced requires --wav')
    if a.backend == 'oracle' and not a.wav:
        p.error('The correctness oracle supports replay checks only')
    if a.capture_seconds < 0 or not math.isfinite(a.capture_seconds):
        p.error('Capture duration must be finite and nonnegative')
    if a.native_threads < 1 or (a.threads is not None and a.threads < 1):
        p.error('Thread counts must be positive')
    if a.decoder_step_threads is not None and a.decoder_step_threads < 1:
        p.error('Decoder step thread count must be positive')
    if a.native_projections and a.backend != 'fpga':
        p.error('--native-projections requires the FPGA backend')
    highpass_hz = a.highpass_hz if a.highpass_hz is not None else (0 if a.wav else 30)
    if not math.isfinite(highpass_hz) or not 0 <= highpass_hz <= 200:
        p.error('High-pass cutoff must be between 0 and 200 Hz')
    highpass = Highpass(highpass_hz) if highpass_hz else None
    chunk = a.chunk_ms if a.chunk_ms is not None else (250 if a.provisional_preview else (400 if a.accuracy else (250 if a.low_latency else (2500 if a.size == 'small' else 1000))))
    selection = None
    if a.mic_input is not None or a.mic_channel is not None:
        selection = 2*int(a.mic_input == 'secondary')+int(a.mic_channel == 'right')
    # Validate segmentation before allocating models or starting capture.
    segmentation = dict(chunk_ms=chunk, threshold=a.vad_dbfs, silence_ms=a.silence_ms,
        max_seconds=a.max_seconds, gain=a.gain, overlap_ms=a.overlap_ms,
        pause_after_seconds=a.pause_after_seconds if a.accuracy else None, pause_ms=a.pause_ms,
        pre_roll_ms=a.pre_roll_ms, attack_ms=a.attack_ms, min_speech_ms=a.min_speech_ms)
    Engine(None, lambda e: None, **segmentation)
    log = a.out.open('x', encoding='utf-8') if a.out else None
    device, captured, engine, decoder, encoder, model = None, None, None, None, None, None
    status, raw_nonzero, raw_clipped = 0, 0, 0
    health_count, health_sum, health_power, health_peak, health_clips = 0, 0., 0., 0., 0
    def emit(event):
        event['emitted_monotonic_ns'] = time.monotonic_ns()
        line = json.dumps(event)
        if log:
            log.write(line+'\n')
            log.flush()
        if a.display == 'json':
            print(line, flush=True)
        elif event['type'] == 'ready':
            print('Listening for speech. Press Ctrl+C to stop.', flush=True)
        elif event['type'] in ('partial', 'final', 'incomplete'):
            print('['+event['type']+' '+str(event['utterance_id'])+'] '+event['text'], flush=True)
        elif event['type'] == 'error':
            print('Error: '+event['message'], file=sys.stderr, flush=True)
        elif event['type'] == 'summary':
            print('Stopped. Completed utterances: '+str(event.get('finals', 0)), flush=True)
    try:
        if not a.wav:
            import subprocess
            subprocess.run(['modprobe', 'uio_pdrv_genirq', 'of_id=generic-uio'], check=True)
            microphone_device(selection)
        from .matrix import BulkDevice
        from .model import load
        from .ort_decoder import Runtime
        from .ort_decoder import digest
        from .streaming import Stream
        start = time.monotonic()
        device = BulkDevice(a.library, native_projections=a.native_projections) if a.backend == 'fpga' else None
        model, processor, metadata = load(a.size, a.backend, device=device,
                                          threads=a.threads or (4 if a.size == 'small' else 2))
        decoder = Runtime(a.ort_decoder, metadata, threads=a.native_threads, scheduling='run-spin', precision=a.decoder_precision,
                          step_threads=a.decoder_step_threads, single_token=not a.decoder_generic_step)
        encoder = None
        if a.ort_encoder:
            from .ort_encoder import Runtime as EncoderRuntime
            encoder = EncoderRuntime(a.ort_encoder, metadata, threads=a.native_threads, precision=a.encoder_precision,
                                     scheduling=a.encoder_scheduling)
        def factory():
            return Stream(model, processor, max_seconds=a.max_seconds, max_tokens=256,
                          decoder_runtime=decoder, encoder_runtime=encoder,
                          preview_seconds=a.preview_seconds if a.accuracy else None,
                          preview_overlap_seconds=a.preview_overlap_seconds, defer_final=a.accuracy,
                          provisional_preview=a.provisional_preview)
        engine = Engine(factory, emit, **segmentation)
        source = Replay(a.wav, paced=not a.unpaced) if a.wav else Microphone(a.capture_helper, a.capture_seconds, selection)
        emit({'type': 'ready', 'model': metadata, 'decoder_runtime': decoder.metadata,
              'encoder_runtime': encoder.metadata if encoder else {'engine': 'torch'},
              'source': 'replay' if a.wav else 'sph0645', 'paced': not a.unpaced,
              'source_audio_sha256': digest(a.wav) if a.wav else None,
              'capture_helper_sha256': digest(a.capture_helper) if not a.wav else None,
              'mic_selection': source.selection if not a.wav else None,
              'initialization_seconds': time.monotonic()-start, 'sample_rate': RATE,
              'chunk_ms': chunk, 'vad_dbfs': a.vad_dbfs, 'silence_ms': a.silence_ms,
              'coalesce_partials': a.low_latency,
              'accuracy_mode': a.accuracy, 'segmentation': segmentation,
              'provisional_preview': a.provisional_preview,
              'native_projections': a.native_projections,
              'inference_library_sha256': digest(a.library) if device else None,
              'preview_seconds': a.preview_seconds if a.accuracy else None,
              'preview_overlap_seconds': a.preview_overlap_seconds if a.accuracy else None,
              'max_seconds': a.max_seconds, 'gain': a.gain, 'highpass_hz': highpass_hz})
        with BufferedCapture(source, seconds=a.buffer_seconds) as captured:
            for block in captured:
                raw_nonzero += int(np.count_nonzero(block.samples))
                health_count += len(block.samples)
                health_sum += float(np.sum(block.samples, dtype=np.float64))
                health_power += float(np.square(block.samples, dtype=np.float64).sum())
                health_peak = max(health_peak, float(np.max(np.abs(block.samples))))
                clips = int(np.count_nonzero(np.abs(block.samples) >= .999))
                health_clips += clips
                raw_clipped += clips
                if highpass:
                    block = Block(highpass.process(block.samples), block.end_ns)
                engine.push(block, allow_partial=not a.low_latency or captured.queue.empty())
                if a.accuracy and captured.queue.empty():
                    engine.advance_refinement()
                if health_count >= RATE:
                    dc = health_sum/health_count
                    emit({'type': 'audio-health', 'audio_seconds': engine.position/RATE,
                          'raw_dc_offset': dc, 'raw_peak': health_peak,
                          'raw_ac_rms_dbfs': 10*math.log10(max(health_power/health_count-dc*dc, 1e-24)),
                          'raw_clipped_fraction': health_clips/health_count})
                    health_count, health_sum, health_power, health_peak, health_clips = 0, 0., 0., 0., 0
                if not a.wav and engine.position >= 3*RATE and raw_nonzero == 0:
                    raise RuntimeError('Microphone produced only digital zeros for three seconds: check channel, power and wiring')
        engine.finish()
        if not engine.position:
            raise RuntimeError('Capture produced no audio')
        if not a.wav and raw_nonzero == 0:
            raise RuntimeError('Microphone produced only digital zeros: check channel, power and wiring')
    except KeyboardInterrupt:
        status = 130
        emit({'type': 'stopped', 'reason': 'interrupt', 'unfinished_utterance_cancelled': True})
    except Exception as error:
        status = 1
        emit({'type': 'error', 'message': str(error), 'unfinished_utterance_cancelled': True})
    finally:
        try:
            emit({'type': 'summary', 'exit_code': status, **(engine.summary() if engine else {}),
                  'raw_clipped_samples': raw_clipped,
                  'queue_high_water_ms': captured.high_water_blocks*20 if captured else 0,
                  'decoder_profile': dict(decoder.profile) if decoder else None,
                  'encoder_runtime_profile': dict(encoder.profile) if encoder else None,
                  'projection_profiles': {name: {'shape': [m.projection.m, m.projection.k],
                      'calls': m.projection.calls, **m.projection.profile}
                      for name, m in model.named_modules() if hasattr(m, 'projection')
                      and hasattr(m.projection, 'profile')} if model else {},
                  'physical_timings': device.timings() if device else None,
                  'physical': device.stats() if device else None})
        finally:
            if device:
                device.close()
            if log:
                log.close()
    raise SystemExit(status)


if __name__ == '__main__':
    main()
