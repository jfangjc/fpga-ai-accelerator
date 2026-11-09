// Transcript of a WAV file with transcript hybrid|cpu MODEL running_prior.json clip.wav transcript.txt
// The front end turns the whole file into frames first, then each phrase becomes one line
#include "phrase.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// 16 kHz mono 16-bit samples, or NULL
static int16_t* read_wav(const char* path, size_t* n) {
    FILE* f = fopen(path, "rb");
    if (!f) {
        return NULL;
    }
    uint8_t riff[12], chunk[8], fmt[16] = {0};
    int16_t* pcm = NULL;
    if (fread(riff, 1, 12, f) == 12 && !memcmp(riff, "RIFF", 4) && !memcmp(riff + 8, "WAVE", 4)) {
        while (fread(chunk, 1, 8, f) == 8) {
            uint32_t size;
            memcpy(&size, chunk + 4, 4);
            if (!memcmp(chunk, "fmt ", 4) && size >= 16 && fread(fmt, 1, 16, f) == 16) {
                fseek(f, size - 16 + (size & 1), SEEK_CUR);
            }
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
            else {
                fseek(f, size + (size & 1), SEEK_CUR);
            }
        }
    }
    fclose(f);
    return pcm;
}

int main(int argc, char** argv) {
    const runner* model = argc == 6 ? runner_find(argv[1]) : NULL;
    if (!model) {
        fputs("Usage: transcript hybrid|cpu MODEL PRIOR WAV TXT\n", stderr);
        return 2;
    }
    if (phrase_prior(argv[3])) {
        fprintf(stderr, "Cannot read %s\n", argv[3]);
        return 1;
    }
    size_t n = 0;
    int16_t* pcm = read_wav(argv[4], &n);
    if (!pcm || n < 320) {
        fprintf(stderr, "%s is not a 16 kHz mono 16-bit WAV file\n", argv[4]);
        return 1;
    }
    FILE* out = fopen(argv[5], "w");
    if (!out) {
        fprintf(stderr, "Cannot write %s\n", argv[5]);
        return 1;
    }
    if (kv260_open() || model->load(argv[2])) {
        return 1;
    }

    // A frame per 160 samples after the first 320
    size_t frames = (n - 320) / 160 + 1;
    uint64_t (*power)[FE_BANDS] = malloc(frames * sizeof *power);
    fe_start(FE_INJECT);
    if (fe_inject(pcm, n)) {
        fprintf(stderr, "%s is too long\n", argv[4]);
        return 1;
    }
    for (size_t t = 0; t < frames; t++) {
        if (fe_frame(power[t], 1000)) {
            fprintf(stderr, "Front end frame %zu missing\n", t);
            return 1;
        }
    }
    fe_control(0);

    static char text[TEXT];
    for (size_t t = 0; t < frames; t++) {
        int state = phrase_push(power[t], 1);
        if (state == PHRASE_END || (state == PHRASE_SPEECH && t == frames - 1)) {
            phrase_decode(model, text);
            if (*text) {
                fprintf(out, "%s\n", text);
            }
        }
    }
    fclose(out);
    return 0;
}
