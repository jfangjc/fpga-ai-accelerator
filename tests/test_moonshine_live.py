import io
from pathlib import Path
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.capture import BLOCK, RATE, HEADER, Block, BufferedCapture, Highpass, packets
from moonshine.live import Engine


class FakeStream:
    def __init__(self):
        self.audio = []
        self.closed = False
        self.profile = {'decoder_seconds': 0.}
        self.decode_stats = {'autoregressive_tokens': 0}

    def push(self, samples, final=False):
        if self.closed:
            raise AssertionError('Reused finalized utterance')
        self.audio.append(samples.copy())
        self.closed = final
        return {'text': 'speech', 'tokens': [1, 2], 'final': final, 'token_limit_reached': False}


class LiveTests(unittest.TestCase):
    def engine(self, **kwargs):
        streams, events = [], []
        def factory():
            stream = FakeStream()
            streams.append(stream)
            return stream
        return Engine(factory, events.append, **kwargs), streams, events

    def feed(self, engine, audio, lengths=(BLOCK,)):
        offset, index = 0, 0
        origin = time.monotonic_ns()
        while offset < len(audio):
            stop = min(len(audio), offset+lengths[index % len(lengths)])
            engine.push(Block(audio[offset:stop], origin+stop*1000000000//RATE))
            offset, index = stop, index+1
        engine.finish()

    def test_silence_does_not_create_model_or_hallucinated_text(self):
        engine, streams, events = self.engine()
        self.feed(engine, np.zeros(RATE, np.float32))
        self.assertFalse(streams)
        self.assertFalse(any(e['type'] in ('partial', 'final') for e in events))
        self.assertTrue(engine.summary()['all_zero_audio'])

    def test_two_utterances_keep_exact_audio_and_fresh_stream_state(self):
        engine, streams, events = self.engine(chunk_ms=200, threshold=-55, silence_ms=200,
            pre_roll_ms=100, attack_ms=40, min_speech_ms=100, max_seconds=2)
        z = lambda n: np.zeros(int(n*RATE), np.float32)
        tone = np.full(int(.6*RATE), .03, np.float32)
        audio = np.concatenate((z(.5), tone, z(1), tone, z(1)))
        self.feed(engine, audio)
        finals = [e for e in events if e['type'] == 'final']
        self.assertEqual(len(streams), 2)
        self.assertEqual(len(finals), 2)
        self.assertTrue(any(e['type'] == 'partial' for e in events))
        for stream, final in zip(streams, finals):
            self.assertTrue(stream.closed)
            self.assertEqual(final['reason'], 'silence')
            np.testing.assert_array_equal(np.concatenate(stream.audio),
                audio[final['audio_start_sample']:final['audio_end_sample']])
        self.assertLess(finals[0]['audio_end_sample'], finals[1]['audio_start_sample'])

    def test_hard_boundaries_preserve_audio_with_irregular_source_blocks(self):
        engine, streams, events = self.engine(chunk_ms=200, silence_ms=200,
            pre_roll_ms=40, attack_ms=20, min_speech_ms=20, max_seconds=.7)
        audio = np.full(int(1.7*RATE), .03, np.float32)
        self.feed(engine, audio, (17, 303, 7, 313, 211))
        finals = [e for e in events if e['type'] == 'final']
        self.assertEqual([e['reason'] for e in finals], ['max-duration', 'max-duration', 'source-eof'])
        self.assertTrue(all(sum(len(x) for x in stream.audio) <= int(.7*RATE) for stream in streams))
        np.testing.assert_array_equal(np.concatenate([np.concatenate(s.audio) for s in streams]), audio)

    def test_click_is_discarded_without_running_decoder(self):
        engine, streams, events = self.engine(silence_ms=200)
        audio = np.concatenate((np.full(4*BLOCK, .03, np.float32), np.zeros(RATE, np.float32)))
        self.feed(engine, audio)
        self.assertEqual(engine.discarded, 1)
        self.assertTrue(all(not s.audio for s in streams))
        self.assertFalse(any(e['type'] == 'final' for e in events))

    def test_coalescing_preserves_audio_and_mandatory_hard_endpoints(self):
        engine, streams, events = self.engine(chunk_ms=200, silence_ms=200,
            pre_roll_ms=40, attack_ms=20, min_speech_ms=20, max_seconds=.7)
        audio = np.full(2*RATE, .03, np.float32)
        for offset in range(0, len(audio), BLOCK):
            engine.push(Block(audio[offset:offset+BLOCK], time.monotonic_ns()), allow_partial=False)
        engine.finish()
        self.assertFalse(any(e['type'] == 'partial' for e in events))
        self.assertEqual([e['reason'] for e in events if e['type'] == 'final'],
                         ['max-duration', 'max-duration', 'source-eof'])
        np.testing.assert_array_equal(np.concatenate([np.concatenate(s.audio) for s in streams]), audio)

    def test_coalescing_resumes_at_newest_audio_and_keeps_silence_endpoint(self):
        engine, streams, events = self.engine(chunk_ms=200, silence_ms=200,
            pre_roll_ms=40, attack_ms=20, min_speech_ms=20)
        audio = np.concatenate((np.full(RATE, .03, np.float32), np.zeros(RATE//2, np.float32)))
        for offset in range(0, len(audio), BLOCK):
            engine.push(Block(audio[offset:offset+BLOCK], time.monotonic_ns()),
                        allow_partial=offset == RATE-BLOCK)
        engine.finish()
        updates = [e for e in events if e['type'] in ('partial', 'final')]
        self.assertEqual([e['type'] for e in updates], ['partial', 'final'])
        self.assertEqual(updates[0]['audio_end_sample'], RATE)
        self.assertEqual(updates[-1]['reason'], 'silence')
        np.testing.assert_array_equal(np.concatenate(streams[0].audio), audio[:RATE+RATE//5])
        self.assertGreaterEqual(updates[0]['oldest_update_age_ms'], 1000)

    def test_invalid_audio_and_configuration_fail(self):
        engine, _, _ = self.engine()
        for data in (np.empty(0), np.full(20, np.nan), np.ones(BLOCK+1), np.ones((2, 3))):
            with self.assertRaises(ValueError):
                engine.push(Block(data, 0))
        with self.assertRaises(ValueError):
            self.engine(chunk_ms=3000, max_seconds=2)

    def test_provisional_preview_omits_future_wait_but_final_keeps_lookahead(self):
        class Provisional(FakeStream):
            lookahead_samples = int(.24*RATE)

            def push(self, samples, final=False):
                return {**super().push(samples, final), 'provisional_encoder': not final}

        rows = []
        engine = Engine(Provisional, rows.append, chunk_ms=200, silence_ms=200)
        self.feed(engine, np.concatenate((np.full(RATE, .03, np.float32), np.zeros(RATE, np.float32))))
        partials = [r for r in rows if r['type'] == 'partial']
        self.assertTrue(partials)
        for row in partials:
            self.assertEqual(row['encoder_lookahead_ms'], 0)
            self.assertAlmostEqual(row['oldest_update_age_ms'], row['lag_ms']+row['update_audio_ms'])
        final = next(r for r in rows if r['type'] == 'final')
        self.assertEqual(final['encoder_lookahead_ms'], 240)

    def test_generation_limit_never_commits_incomplete_text_as_final(self):
        class Incomplete(FakeStream):
            def push(self, samples, final=False):
                event = super().push(samples, final)
                event['token_limit_reached'] = True
                return event
        events = []
        engine = Engine(Incomplete, events.append, chunk_ms=500, min_speech_ms=20)
        self.feed(engine, np.full(RATE, .03, np.float32))
        self.assertFalse(any(e['type'] == 'final' for e in events))
        self.assertEqual(engine.summary()['finals'], 0)
        self.assertEqual(engine.summary()['incomplete'], 1)
        self.assertEqual(events[-1]['type'], 'incomplete')


class CaptureTests(unittest.TestCase):
    def test_dc_offset_has_no_startup_transient_or_false_speech(self):
        filt = Highpass(30)
        engine = Engine(lambda: self.fail('DC offset triggered speech'), lambda e: None)
        for index in range(160):
            audio = filt.process(np.full(BLOCK, -.094, np.float32))
            np.testing.assert_array_equal(audio, np.zeros(BLOCK))
            engine.push(Block(audio, (index+1)*20000000))
        engine.finish()
        self.assertEqual(engine.utterances, 0)

    def test_dc_filter_matches_scalar_recurrence_across_irregular_blocks(self):
        x = (-.094+.01*np.sin(2*np.pi*1000*np.arange(8192)/RATE)).astype(np.float32)
        filt = Highpass(30)
        blocks, position = [], 0
        sizes = (1, 17, 320, 73, 256)
        while position < len(x):
            size = sizes[len(blocks) % len(sizes)]
            blocks.append(filt.process(x[position:position+size]))
            position += size
        actual = np.concatenate(blocks)
        previous_x, previous_y = float(x[0]), 0.
        expected = []
        for value in x:
            previous_y = filt.a*(previous_y+float(value)-previous_x)
            previous_x = float(value)
            expected.append(previous_y)
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=2e-9)
        self.assertLess(abs(float(np.mean(actual[-4096:]))), 1e-5)
        self.assertGreater(float(np.std(actual[-4096:])), .0069)
        for hz in (0, 201, float('nan')):
            with self.assertRaises(ValueError):
                Highpass(hz)

    def packet(self, sequence=0, values=(-8388608, 0, 8388607), dropped=7, startup=7):
        data = np.asarray(values, dtype='<i4')
        return HEADER.pack(0x31434d56, sequence, 123456, len(data), 2000, dropped, startup, 0, 0)+data.tobytes()

    def test_signed_pcm24_and_packet_continuity(self):
        blocks = list(packets(io.BytesIO(self.packet()+self.packet(sequence=1))))
        self.assertEqual(len(blocks), 2)
        np.testing.assert_array_equal(blocks[0].samples, np.array([-1, 0, 8388607/8388608], np.float32))

    def test_truncation_overflow_bad_samples_and_sequence_fail(self):
        cases = (self.packet()[:-1], self.packet()[:8], self.packet(sequence=4),
                 self.packet(dropped=8), self.packet(values=(8388608,)))
        for data in cases:
            with self.subTest(length=len(data)):
                with self.assertRaises(RuntimeError):
                    list(packets(io.BytesIO(data)))

    def test_capture_overrun_is_explicit_and_reader_stops(self):
        class Source:
            closed = False
            def __iter__(self):
                for i in range(100):
                    yield Block(np.zeros(BLOCK, np.float32), i)
            def close(self):
                self.closed = True
        source = Source()
        with BufferedCapture(source, seconds=.1) as captured:
            self.assertTrue(captured.done.wait(2))
            with self.assertRaisesRegex(RuntimeError, 'queue exhausted'):
                list(captured)
        self.assertTrue(source.closed)
        self.assertFalse(captured.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
