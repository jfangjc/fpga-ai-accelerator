#define _GNU_SOURCE
#include "phrase.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char VOCAB[] = " abcdefghijklmnopqrstuvwxyz'"; // class 28 is the CTC blank

static double prior_count, prior_mean[FE_BANDS], prior_m2[FE_BANDS], scale;
// Running statistics and INT8 features of the phrase
static double count, mean[FE_BANDS], m2[FE_BANDS];
static int8_t phrase[FE_BANDS][PHRASE];
static unsigned length;

// running_prior.json: "count", then "mean" and "m2" with 64 numbers each, and "input_scale"
int phrase_prior(const char* path) {
    static char text[1 << 16];
    FILE* f = fopen(path, "r");
    size_t n = f ? fread(text, 1, sizeof text - 1, f) : 0;
    if (f)
        fclose(f);
    text[n] = 0;
    const char* keys[] = {"\"count\"", "\"mean\"", "\"m2\"", "\"input_scale\""};
    double* values[] = {&prior_count, prior_mean, prior_m2, &scale};
    for (int k = 0; k < 4; k++) {
        char* p = strstr(text, keys[k]);
        if (!p)
            return -1;
        p = strchr(p, ':') + 1;
        for (int i = 0; i < (k == 1 || k == 2 ? FE_BANDS : 1); i++) {
            p += strspn(p, " \n[,");
            values[k][i] = strtod(p, &p);
        }
    }
    return 0;
}

void phrase_begin(void) {
    count = prior_count;
    memcpy(mean, prior_mean, sizeof mean);
    memcpy(m2, prior_m2, sizeof m2);
    length = 0;
}

void phrase_add(const float z[FE_BANDS]) {
    count++;
    for (int b = 0; b < FE_BANDS; b++) {
        double d = z[b] - mean[b];
        mean[b] += d / count;
        m2[b] += d * (z[b] - mean[b]);
        double x = (z[b] - mean[b]) / (sqrt(m2[b] / fmax(1, count - 1)) + 1e-5) / scale;
        x = copysign(floor(fabs(x) + .5), x);
        phrase[b][length] = x < -128 ? -128 : x > 127 ? 127 : (int8_t)x;
    }
    length++;
}

unsigned phrase_length(void) { return length; }

// Longer than 6.4 s: windows overlapping by 3.2 s
void phrase_decode(unsigned from, char text[TEXT]) {
    static int8_t features[FE_BANDS * ENGINE_FRAMES], logits[ENGINE_CLASSES * ENGINE_FRAMES];
    static uint8_t classes[TEXT];
    unsigned filled = 0; // output frames decoded
    for (unsigned start = from;; start += ENGINE_FRAMES / 2) {
        int last = start + ENGINE_FRAMES >= length;
        if (last && length - from > ENGINE_FRAMES)
            start = from + (length - ENGINE_FRAMES - from + 1) / 2 * 2; // an even offset keeps output frames aligned
        unsigned n = last ? length - start : ENGINE_FRAMES, padded = (n + 15) / 16 * 16, valid;
        for (int b = 0; b < FE_BANDS; b++) {
            memcpy(features + b * padded, &phrase[b][start], n);
            memset(features + b * padded + n, 0, padded - n);
        }
        int out = engine_run(features, padded, n, logits, &valid);
        if (out < 0)
            break;
        unsigned offset = (start - from) / 2, end = last ? offset + valid : offset + 3 * ENGINE_FRAMES / 8;
        for (; filled < end; filled++) {
            unsigned t = filled - offset, best = 0;
            for (unsigned k = 1; k < ENGINE_CLASSES; k++)
                if (logits[k * out + t] > logits[best * out + t])
                    best = k;
            classes[filled] = best;
        }
        if (last)
            break;
    }
    // CTC: collapse repeats, drop blanks, trim spaces
    size_t n = 0;
    for (unsigned t = 0, prev = 28; t < filled; prev = classes[t++])
        if (classes[t] != prev && classes[t] != 28 && (n || classes[t]))
            text[n++] = VOCAB[classes[t]];
    while (n && text[n - 1] == ' ')
        n--;
    text[n] = 0;
}
