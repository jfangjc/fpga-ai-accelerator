// Phrases found by their energy, as INT8 features normalised with running statistics, decoded with a runner
#define _GNU_SOURCE
#include "phrase.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LEAD 20 // a phrase starts 0.2 s before the first speech frame
#define SILENCE 70 // and ends after 0.7 s without speech
#define HISTORY 32 // recent frames, enough for the lead
#define BLANK 28 // the CTC blank class, after the 28 characters
static const char VOCAB[] = " abcdefghijklmnopqrstuvwxyz'";

static double prior_count, prior_mean[FE_BANDS], prior_m2[FE_BANDS], scale;
// Log mel of recent frames, and whether a phrase is open
static float history[HISTORY][FE_BANDS];
static long frames;
static int open, silent;
// Running statistics and INT8 features of the phrase
static double count, mean[FE_BANDS], m2[FE_BANDS];
static int8_t phrase[FE_BANDS][PHRASE];
static unsigned length;
// Classes of the phrase. Every window but the last never changes, so decoding resumes after them
static uint8_t classes[TEXT];
static unsigned kept_start, kept_classes;

// running_prior.json holds "count", then "mean" and "m2" with 64 numbers each, and "input_scale"
int phrase_prior(const char* path) {
    static char text[1 << 16];
    FILE* f = fopen(path, "r");
    size_t n = f ? fread(text, 1, sizeof text - 1, f) : 0;
    if (f) {
        fclose(f);
    }
    text[n] = 0;
    const char* keys[] = {"\"count\"", "\"mean\"", "\"m2\"", "\"input_scale\""};
    double* values[] = {&prior_count, prior_mean, prior_m2, &scale};
    for (int k = 0; k < 4; k++) {
        char* p = strstr(text, keys[k]);
        if (!p) {
            return -1;
        }
        p = strchr(p, ':') + 1;
        for (int i = 0; i < (k == 1 || k == 2 ? FE_BANDS : 1); i++) {
            p += strspn(p, " \n[,");
            values[k][i] = strtod(p, &p);
        }
    }
    return 0;
}

static void begin(void) {
    count = prior_count;
    memcpy(mean, prior_mean, sizeof mean);
    memcpy(m2, prior_m2, sizeof m2);
    length = kept_start = kept_classes = 0;
}

static void add(const float z[FE_BANDS]) {
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

// Speech is energy 8 dB above the noise floor of the last 5 s
static int speech(double e, long t) {
    static double block, floors[10] = {INFINITY, INFINITY, INFINITY, INFINITY, INFINITY,
                                       INFINITY, INFINITY, INFINITY, INFINITY, INFINITY};
    block = t % 50 == 0 || e < block ? e : block;
    if (t % 50 == 49) {
        memmove(floors, floors + 1, 9 * sizeof(double));
        floors[9] = block;
    }
    double lowest = block;
    for (int i = 0; i < 10; i++) {
        lowest = fmin(lowest, floors[i]);
    }
    return t > 30 && e >= 2e-6 && e >= lowest * 6.31; // the microphone settles in the first 0.3 s
}

int phrase_push(const uint64_t power[FE_BANDS], double gain) {
    long t = frames++;
    double energy = 0; // bands 1-63, as band 0 holds the microphone's DC
    for (int b = 0; b < FE_BANDS; b++) {
        double p = ldexp((double)power[b], -42);
        energy += b ? p : 0;
        history[t % HISTORY][b] = logf((float)(p * gain) + 0x1p-24f);
    }
    int voiced = speech(energy, t);
    if (!open && !voiced) {
        return PHRASE_SILENT;
    }
    if (!open) {
        open = 1;
        silent = 0;
        begin();
        for (long k = t - LEAD < 0 ? 0 : t - LEAD; k < t; k++) {
            add(history[k % HISTORY]);
        }
    }
    add(history[t % HISTORY]);
    silent = voiced ? 0 : silent + 1;
    if (silent == SILENCE || length == PHRASE) {
        open = 0;
        return PHRASE_END;
    }
    return PHRASE_SPEECH;
}

unsigned phrase_length(void) { return length; }

// CTC of the first count classes collapses repeats, drops blanks and trims spaces
static size_t ctc(unsigned count, char text[TEXT]) {
    size_t n = 0;
    for (unsigned t = 0, prev = BLANK; t < count; prev = classes[t++]) {
        if (classes[t] != prev && classes[t] != BLANK && (n || classes[t])) {
            text[n++] = VOCAB[classes[t]];
        }
    }
    while (n && text[n - 1] == ' ') {
        n--;
    }
    text[n] = 0;
    return n;
}

// A phrase longer than 6.4 s runs in windows that overlap by 3.2 s
size_t phrase_decode(const runner* model, char text[TEXT]) {
    static int8_t features[FE_BANDS * ENGINE_FRAMES], logits[ENGINE_CLASSES * ENGINE_FRAMES];
    unsigned filled = kept_classes; // output frames decoded
    for (unsigned start = kept_start;; start += ENGINE_FRAMES / 2) {
        int last = start + ENGINE_FRAMES >= length;
        if (last && length > ENGINE_FRAMES) {
            start = (length - ENGINE_FRAMES + 1) / 2 * 2; // an even offset keeps output frames aligned
        }
        unsigned n = last ? length - start : ENGINE_FRAMES, padded = (n + 15) / 16 * 16, valid;
        for (int b = 0; b < FE_BANDS; b++) {
            memcpy(features + b * padded, &phrase[b][start], n);
            memset(features + b * padded + n, 0, padded - n);
        }
        int out = model->run(features, padded, n, logits, &valid);
        if (out < 0) {
            break;
        }
        unsigned offset = start / 2, end = last ? offset + valid : offset + 3 * ENGINE_FRAMES / 8;
        for (; filled < end; filled++) {
            unsigned t = filled - offset, best = 0;
            for (unsigned k = 1; k < ENGINE_CLASSES; k++) {
                if (logits[k * out + t] > logits[best * out + t]) {
                    best = k;
                }
            }
            classes[filled] = best;
        }
        if (last) {
            break;
        }
        kept_start = start + ENGINE_FRAMES / 2;
        kept_classes = filled;
    }
    // The text of the kept classes starts every later text of the phrase
    size_t kept = ctc(kept_classes, text);
    ctc(filled, text);
    return kept;
}
