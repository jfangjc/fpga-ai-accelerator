// Transcribe a WAV file of up to 6.4 s: transcribe quartznet_engine.bin input_scale clip.wav [dump.bin]
#include "kv260.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char VOCAB[] = " abcdefghijklmnopqrstuvwxyz'"; // class 28 is the CTC blank

// 16 kHz mono 16-bit samples, or NULL
static int16_t* read_wav(const char* path, size_t* n) {
    FILE* f = fopen(path, "rb");
    if (!f)
        return NULL;
    uint8_t riff[12], chunk[8], fmt[16] = {0};
    int16_t* pcm = NULL;
    if (fread(riff, 1, 12, f) == 12 && !memcmp(riff, "RIFF", 4) && !memcmp(riff + 8, "WAVE", 4))
        while (fread(chunk, 1, 8, f) == 8) {
            uint32_t size;
            memcpy(&size, chunk + 4, 4);
            if (!memcmp(chunk, "fmt ", 4) && size >= 16 && fread(fmt, 1, 16, f) == 16)
                fseek(f, size - 16 + (size & 1), SEEK_CUR);
            else if (!memcmp(chunk, "data", 4)) {
                uint16_t format, channels, bits;
                uint32_t rate;
                memcpy(&format, fmt, 2);
                memcpy(&channels, fmt + 2, 2);
                memcpy(&rate, fmt + 4, 4);
                memcpy(&bits, fmt + 14, 2);
                if (format == 1 && channels == 1 && rate == 16000 && bits == 16) {
                    pcm = malloc(size);
                    *n = fread(pcm, 2, size / 2, f);
                }
                break;
            }
            else
                fseek(f, size + (size & 1), SEEK_CUR);
        }
    fclose(f);
    return pcm;
}

int main(int argc, char** argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: transcribe quartznet_engine.bin input_scale clip.wav [dump.bin]\n");
        return 2;
    }
    size_t n;
    int16_t* pcm = read_wav(argv[3], &n);
    if (!pcm || n < 320) {
        fprintf(stderr, "need a 16 kHz mono 16-bit WAV file\n");
        return 1;
    }
    // A frame per 160 samples after the first 320, padded to 16 frames
    unsigned frames = (n - 320) / 160 + 1, padded = (frames + 15) / 16 * 16;
    if (padded > ENGINE_FRAMES) {
        fprintf(stderr, "clip longer than %.1f s\n", ENGINE_FRAMES / 100.0);
        return 1;
    }
    if (kv260_open() || engine_load(argv[1]))
        return 1;

    float* z = malloc(sizeof(float) * FE_BANDS * frames);
    fe_start(FE_INJECT);
    fe_inject(pcm, n);
    for (unsigned t = 0; t < frames; t++) {
        uint64_t power[FE_BANDS];
        if (fe_frame(power, 1000)) {
            fprintf(stderr, "front end frame %u missing\n", t);
            return 1;
        }
        for (int b = 0; b < FE_BANDS; b++)
            z[b * frames + t] = logf((float)(power[b] * 0x1p-42) + 0x1p-24f);
    }
    fe_control(0);

    // Normalise each band over the clip
    float scale = strtof(argv[2], NULL);
    int8_t* x = calloc(FE_BANDS, padded);
    for (int b = 0; b < FE_BANDS; b++) {
        const float* band = z + b * frames;
        float mean = 0, var = 0;
        for (unsigned t = 0; t < frames; t++)
            mean += band[t];
        mean /= frames;
        for (unsigned t = 0; t < frames; t++)
            var += (band[t] - mean) * (band[t] - mean);
        float std = sqrtf(var / (frames - 1)) + 1e-5f;
        for (unsigned t = 0; t < frames; t++) {
            float q = (band[t] - mean) / std / scale;
            q = copysignf(floorf(fabsf(q) + .5f), q);
            x[b * padded + t] = q < -128 ? -128 : q > 127 ? 127 : (int8_t)q;
        }
    }

    int8_t* logits = malloc(ENGINE_CLASSES * padded);
    unsigned valid;
    int out = engine_run(x, padded, frames, logits, &valid);
    if (out < 0)
        return 1;

    // CTC greedy decode
    char text[ENGINE_FRAMES + 1];
    size_t length = 0;
    for (unsigned t = 0, prev = 28; t < valid; t++) {
        unsigned best = 0;
        for (unsigned k = 1; k < ENGINE_CLASSES; k++)
            if (logits[k * out + t] > logits[best * out + t])
                best = k;
        if (best != prev && best != 28 && (length || best))
            text[length++] = VOCAB[best];
        prev = best;
    }
    while (length && text[length - 1] == ' ')
        length--;
    printf("%.*s\n", (int)length, text);

    if (argc > 4) {
        FILE* f = fopen(argv[4], "wb");
        uint32_t sizes[4] = {padded, frames, (uint32_t)out, valid};
        fwrite(sizes, 4, 4, f);
        fwrite(x, 1, FE_BANDS * padded, f);
        fwrite(logits, 1, ENGINE_CLASSES * out, f);
        fclose(f);
    }
    return 0;
}
