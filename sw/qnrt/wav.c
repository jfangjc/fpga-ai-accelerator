#define _POSIX_C_SOURCE 200809L
#include "wav.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* PCM16 mono RIFF; convert FLAC to PCM16 WAV with the supplied preparation
 * tool. */
float *qn_wav(const char *p, size_t *n) {
  FILE *f = fopen(p, "rb");
  if (!f)
    return NULL;
  unsigned char h[12];
  if (fread(h, 1, 12, f) != 12 || memcmp(h, "RIFF", 4) ||
      memcmp(h + 8, "WAVE", 4)) {
    fclose(f);
    return NULL;
  }
  int fmt = 0;
  float *x = NULL;
  while (fread(h, 1, 8, f) == 8) {
    unsigned z = (unsigned)h[4] | (unsigned)h[5] << 8 | (unsigned)h[6] << 16 |
                 (unsigned)h[7] << 24;
    if (!memcmp(h, "fmt ", 4)) {
      unsigned char b[16];
      if (z < 16 || fread(b, 1, 16, f) != 16)
        break;
      fmt = b[0] == 1 && b[1] == 0 && b[2] == 1 && b[3] == 0 && b[4] == 128 &&
            b[5] == 62 && b[6] == 0 && b[7] == 0 && b[12] == 2 && b[13] == 0 &&
            b[14] == 16 && b[15] == 0;
      fseek(f, z - 16, SEEK_CUR);
    } else if (!memcmp(h, "data", 4) && fmt) {
      if (z & 1)
        break;
      *n = z / 2;
      x = malloc(*n * sizeof(float));
      if (!x)
        break;
      for (size_t j = 0; j < *n; j++) {
        int a = fgetc(f), b = fgetc(f);
        if (a < 0 || b < 0) {
          free(x);
          x = NULL;
          break;
        }
        x[j] = (int16_t)(a | (b << 8)) / 32768.f;
      }
      break;
    } else
      fseek(f, z, SEEK_CUR);
    if (z & 1)
      fseek(f, 1, SEEK_CUR);
  }
  fclose(f);
  return x;
}
