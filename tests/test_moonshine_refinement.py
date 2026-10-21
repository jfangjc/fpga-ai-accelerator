from pathlib import Path
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.capture import Block, BLOCK, RATE
from moonshine.decoding import DecodeJob
from moonshine.live import Engine
from moonshine.transcript import PreviewText, trim_overlap
import test_moonshine_live as live_tests
from test_moonshine_live import FakeStream


class JobTests(unittest.TestCase):
    def job(self, draft=(), limit=10):
        calls = []
        class Cache:
            def __init__(self, length): self.length = length
            def get_seq_length(self): return self.length
            def crop(self, amount): self.length += amount
        def predict(decoder, ids, cache):
            old = cache.length if cache else 0
            calls.append((ids, old))
            return [({1: 4, 4: 5, 5: 2}.get(i, 7)) for i in ids[0]], Cache(old+len(ids[0]))
        return DecodeJob(lambda: object(), predict, str, 1, 2, limit, draft), calls

    def test_revised_draft_crops_cache_before_continuation(self):
        job, calls = self.job([1, 4, 9, 2])
        self.assertIsNone(job.advance())  # Only prepare memory, no tokens yet.
        self.assertFalse(calls)
        self.assertIsNone(job.advance())  # One causal prefill.
        result = job.advance()           # One continuation after disagreement.
        self.assertEqual(result['tokens'], [1, 4, 5, 2])
        self.assertEqual(calls[-1], ([[5]], 2))
        self.assertEqual(job.stats['accepted_draft_tokens'], 1)
        greedy, _ = self.job()
        self.assertEqual(result, greedy.run())

    def test_token_limit_does_not_claim_eos(self):
        job, _ = self.job(limit=1)
        result = job.run()
        self.assertTrue(result['token_limit_reached'])
        self.assertEqual(result['tokens'], [1, 4])


class Deferred(FakeStream):
    def push(self, samples, final=False):
        result = super().push(samples, final)
        if final:
            self.final_job = DecodeJob(lambda: None, lambda d, i, c: ([2], None),
                                       lambda tokens: 'refined phrase', 1, 2, 10)
            result.update(final=False, refinement_pending=True)
        return result


class RefinementTests(unittest.TestCase):
    def test_refinement_is_pending_until_cooperatively_completed(self):
        rows = []
        engine = Engine(Deferred, rows.append, chunk_ms=200, silence_ms=200)
        audio = np.concatenate((np.full(RATE, .03, np.float32), np.zeros(RATE, np.float32)))
        for offset in range(0, len(audio), BLOCK):
            engine.push(Block(audio[offset:offset+BLOCK], time.monotonic_ns()))
        self.assertEqual(engine.finals, 0)
        self.assertEqual(len(engine.refinements), 1)
        self.assertTrue(any(r['type'] == 'final-pending' for r in rows))
        engine.finish()
        final = [r for r in rows if r['type'] == 'final']
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]['text'], 'refined phrase')
        self.assertTrue(final[0]['refined'])
        self.assertEqual(engine.summary()['pending_refinements'], 0)

    def test_refinement_queue_overload_is_explicit(self):
        engine = Engine(Deferred, lambda row: None, chunk_ms=200, max_seconds=.7,
                        pre_roll_ms=40, attack_ms=20, min_speech_ms=20, max_refinements=1)
        with self.assertRaisesRegex(RuntimeError, 'Refinement queue exhausted'):
            for offset in range(0, 2*RATE, BLOCK):
                engine.push(Block(np.full(BLOCK, .03, np.float32), time.monotonic_ns()))

    def test_overlapping_boundaries_cover_every_sample_with_exact_source_ranges(self):
        helper = live_tests.LiveTests()
        engine, streams, rows = helper.engine(chunk_ms=200, max_seconds=.7, overlap_ms=200,
            pre_roll_ms=40, attack_ms=20, min_speech_ms=20)
        audio = np.linspace(.02, .04, 2*RATE, dtype=np.float32)
        helper.feed(engine, audio, (17, 303, 7, 313, 211))
        finals = [r for r in rows if r['type'] == 'final']
        covered = np.zeros(len(audio), np.int32)
        for stream, event in zip(streams, finals):
            start, stop = event['audio_start_sample'], event['audio_end_sample']
            np.testing.assert_array_equal(np.concatenate(stream.audio), audio[start:stop])
            covered[start:stop] += 1
        self.assertTrue((covered > 0).all())
        self.assertTrue((covered > 1).any())
        self.assertTrue(all(b['audio_start_sample'] < a['audio_end_sample']
                            for a, b in zip(finals, finals[1:])))

    def test_natural_pause_ends_long_phrase_before_hard_limit(self):
        helper = live_tests.LiveTests()
        engine, _, rows = helper.engine(max_seconds=2, chunk_ms=250, pause_after_seconds=.8,
                                        pause_ms=250, silence_ms=700)
        helper.feed(engine, np.concatenate((np.full(int(.9*RATE), .03, np.float32),
                                           np.zeros(int(.4*RATE), np.float32))))
        self.assertEqual([r['reason'] for r in rows if r['type'] == 'final'], ['natural-pause'])

    def test_old_overlap_does_not_count_as_new_speech_after_a_forced_boundary(self):
        helper = live_tests.LiveTests()
        engine, _, rows = helper.engine(max_seconds=.7, chunk_ms=200, overlap_ms=200,
            pre_roll_ms=40, attack_ms=20, min_speech_ms=200)
        helper.feed(engine, np.concatenate((np.full(int(.74*RATE), .03, np.float32),
                                           np.zeros(RATE, np.float32))))
        self.assertEqual(sum(r['type'] == 'final' for r in rows), 1)
        self.assertEqual(engine.discarded, 1)


class TranscriptTests(unittest.TestCase):
    def test_overlap_normalizes_punctuation_without_deleting_one_word_repetitions(self):
        self.assertEqual(trim_overlap('We went to the park.', 'The park was quiet.'), ('was quiet.', 2, False))
        self.assertEqual(trim_overlap('it was very', 'very good'), ('very good', 0, True))

    def test_preview_revisions_replace_current_window_and_keep_earlier_context(self):
        text = PreviewText()
        self.assertEqual(text.update(0, 'we went to the bark')[0], 'we went to the bark')
        self.assertEqual(text.update(0, 'we went to the park')[0], 'we went to the park')
        self.assertEqual(text.update(100, 'the park was quiet')[0], 'we went to the park was quiet')
        self.assertEqual(text.update(100, 'the park was crowded')[0], 'we went to the park was crowded')

    def test_repeated_phrases_cannot_trim_more_words_than_the_audio_overlap_allows(self):
        phrase = 'we walked to the quiet park yesterday'
        self.assertEqual(trim_overlap(phrase, phrase+' again', max_words=3), (phrase+' again', 0, True))


if __name__ == '__main__':
    unittest.main()
