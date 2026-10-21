from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.accuracy import signal_report, word_errors


class AccuracyTests(unittest.TestCase):
    def test_word_error_rate_counts_substitution_deletion_and_insertion(self):
        score = word_errors('one two three', 'ONE four!')
        self.assertEqual(score['reference_words'], 3)
        self.assertEqual(score['errors'], 2)
        self.assertEqual(score['substitutions'], 1)
        self.assertEqual(score['deletions'], 1)
        self.assertAlmostEqual(score['wer'], 2/3)
        score = word_errors('hello', 'hello there friend')
        self.assertEqual(score['insertions'], 2)
        self.assertEqual(score['wer'], 2)
        self.assertIsNone(word_errors('', 'hello')['wer'])

    def test_signal_report_separates_dc_from_ac_and_counts_clipping(self):
        audio = np.full(16000, -.094, np.float32)
        report = signal_report(audio)
        self.assertAlmostEqual(report['dc_offset'], -.094, places=6)
        self.assertLess(report['ac_rms_dbfs'], -100)
        self.assertEqual(report['clipped_fraction'], 0)
        audio[:160] = 1
        self.assertEqual(signal_report(audio)['clipped_fraction'], .01)


if __name__ == '__main__':
    unittest.main()
