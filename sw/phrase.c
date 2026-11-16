/*
    phrase features and the ctc decoder
    -> every 10 ms phrase_add turns one frame of log mel into 64 int8 features and keeps them for the whole phrase
    -> decode sends up to 6.4 s of those features through the engine and keeps the best of the 29 classes per 20 ms
    -> collapse is the ctc step that merges repeated classes and drops the blanks so what's left is the text
    -> a preview only sends the last 6.4 s and a final sends the whole phrase in 6.4 s windows
*/
#define _GNU_SOURCE
#include "phrase.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char VOCAB[] = " abcdefghijklmnopqrstuvwxyz'"; // class 0 is space then a-z then ' -> class 28 is the ctc blank

// starting stats from running_prior.json so the first frames of a phrase normalise sensibly
static double prior_count, prior_mean[FE_BANDS], prior_m2[FE_BANDS], scale;
// NOTE: the phrase so far
static double count, mean[FE_BANDS], m2[FE_BANDS];   // running mean and variance per band (welford)
static int8_t phrase[FE_BANDS][PHRASE];             // int8 features band by band -> up to 30 s
static unsigned length;                             // input frames in the phrase so far
static uint8_t classes[TEXT];                       // best class per output frame (two input frames each)
static unsigned covered;                            // output frames a decode has given classes to this phrase

// reads running_prior.json which has "count" then "mean" and "m2" with 64 numbers each and "input_scale"
int phrase_prior(const char* path) {
    static char text[1 << 16];
    FILE* f = fopen(path, "r");
    size_t n = f ? fread(text, 1, sizeof text - 1, f) : 0;
    if (f)
        fclose(f);
    text[n] = 0;
    const char* keys[] = {"\"count\"", "\"mean\"", "\"m2\"", "\"input_scale\""};
    double* values[] = {&prior_count, prior_mean, prior_m2, &scale};
    // -> mean and m2 have one number per band and the other two keys have one number each
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

// starts a fresh phrase from the prior stats
void phrase_begin(void) {
    count = prior_count;
    memcpy(mean, prior_mean, sizeof mean);
    memcpy(m2, prior_m2, sizeof m2);
    length = covered = 0;
}

// turns one frame of log mel (float32) into 64 int8 features for the engine
void phrase_add(const float z[FE_BANDS]) {
    count++;
    for (int b = 0; b < FE_BANDS; b++) {
        // -> welford update so the mean and variance follow the speaker without storing old frames
        double d = z[b] - mean[b];
        mean[b] += d / count;
        m2[b] += d * (z[b] - mean[b]);
        // -> normalise then divide by input_scale so the value lands on the int8 grid convert.py used
        double x = (z[b] - mean[b]) / (sqrt(m2[b] / fmax(1, count - 1)) + 1e-5) / scale;
        // -> round halves away from zero then clamp to int8 -> this is the f32 to int8 step
        x = copysign(floor(fabs(x) + .5), x);
        phrase[b][length] = x < -128 ? -128 : x > 127 ? 127 : (int8_t)x;
    }
    length++;
}

// how many input frames the phrase has so far
unsigned phrase_length(void) { return length; }

// fills classes[] for output frames from from / 2 on and leaves the ones before keep alone
static unsigned decode(unsigned from, unsigned keep) {
    static int8_t features[FE_BANDS * ENGINE_FRAMES], logits[ENGINE_CLASSES * ENGINE_FRAMES];
    unsigned filled = from / 2; // output frames decoded so far
    // -> past 6.4 s the windows overlap by 3.2 s and each one keeps from where the last left off to 3/4 of the way in
    for (unsigned start = from;; start += ENGINE_FRAMES / 2) {
        int last = start + ENGINE_FRAMES >= length;
        if (last && length - from > ENGINE_FRAMES)
            start = from + (length - ENGINE_FRAMES - from + 1) / 2 * 2; // the last window ends on the phrase end -> an even start keeps output frames lined up
        unsigned n = last ? length - start : ENGINE_FRAMES, padded = (n + 15) / 16 * 16, valid;
        // -> copy band by band and pad to 16 frames so one 128 bit word is one band over 16 frames
        for (int b = 0; b < FE_BANDS; b++) {
            memcpy(features + b * padded, &phrase[b][start], n);
            memset(features + b * padded + n, 0, padded - n);
        }
        // -> one engine run -> header and features and the program go out in one dma and 29 x out logits come back
        int out = engine_run(features, padded, n, logits, &valid);
        if (out < 0)
            break;
        // -> a middle window is only trusted up to 3/4 of the way in since its end has no audio after it
        unsigned offset = start / 2, end = last ? offset + valid : offset + 3 * ENGINE_FRAMES / 8;
        for (; filled < end; filled++) {
            // -> argmax over the 29 classes is all the decoder needs so no softmax
            unsigned t = filled - offset, best = 0;
            for (unsigned k = 1; k < ENGINE_CLASSES; k++)
                if (logits[k * out + t] > logits[best * out + t])
                    best = k;
            // -> frames before keep stay as earlier previews decoded them
            if (filled >= keep)
                classes[filled] = best;
        }
        if (last)
            break;
    }
    if (filled > covered)
        covered = filled;
    return filled;
}

// ctc over the first n output frames -> merge repeats then drop blanks then trim spaces
static void collapse(unsigned n, char text[TEXT]) {
    size_t k = 0;
    // -> a class only prints when it differs from the frame before so "hhh" is one h
    // -> a blank in between is what lets a real double letter like "ll" through
    // -> k || classes[t] skips spaces before the first letter
    for (unsigned t = 0, prev = 28; t < n; prev = classes[t++])
        if (classes[t] != prev && classes[t] != 28 && (k || classes[t]))
            text[k++] = VOCAB[classes[t]];
    while (k && text[k - 1] == ' ')
        k--;
    text[k] = 0;
}

// final -> decodes the whole phrase in 6.4 s windows
void phrase_decode(char text[TEXT]) { collapse(decode(0, 0), text); }

// preview -> only the last 6.4 s goes through the engine and the text before it comes from earlier previews
void phrase_preview(char text[TEXT]) {
    // -> the first 1.6 s of the window has no words before it so those classes stay as the earlier previews had them
    unsigned from = length > ENGINE_FRAMES ? (length - ENGINE_FRAMES + 1) / 2 * 2 : 0,
             keep = from ? from / 2 + ENGINE_FRAMES / 8 : 0;
    if (keep > covered) // previews got skipped for too long so there's a gap -> decode it all
        from = keep = 0;
    collapse(decode(from, keep), text);
}
