"""Board streaming policy. Seconds unless the name specifies another unit.

Selected buffered operating point; see docs/results/streaming.md before
claiming acceptance. The application must mirror these constants.
"""
WINDOW = 3
LOOKAHEAD = 0.48
HOP = 0.12
PADDING = 'silence'
VAD_RMS = 0.01
VAD_SILENCE = 0.7
VAD_LEAD = 0.2
PHRASE_CAP = 15.0
SAMPLE_RATE = 16000
FEATURE_HOP = 160
FUTURE_SAMPLES = 256

# SELECTED STUDY POLICIES (c=60 ms; measured board acceptance remains separate)
OPERATING_POINTS = {'core': {'window': 3, 'lookahead': 0.48, 'padding': 'silence', 'hop': 0.12, 'full_window_gmac': 3.01601792, 'word_ms': {'p50': 600.0, 'p95': 660.0, 'negative': 0.0}, 'word_censored': 56}, 'stretch': {'window': 3, 'lookahead': 0.48, 'padding': 'silence', 'hop': 0.12, 'full_window_gmac': 3.01601792, 'word_ms': {'p50': 600.0, 'p95': 660.0, 'negative': 0.0}, 'word_censored': 56}}
