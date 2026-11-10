#ifndef QN_WAV_H
#define QN_WAV_H
#include <stddef.h>
/* PCM16 mono 16 kHz RIFF to float in [-1,1); NULL on any other format.
 * Convert FLAC with tools/prepare.py. */
float *qn_wav(const char *path, size_t *samples);
#endif
