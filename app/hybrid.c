// Runner on the PL engine, sending a header and features in front of the resident program as one command
#include "runner.h"
#include <stdio.h>

#define SLOT 0x10000 // header and features below this offset, the program from it
#define MAX_PROGRAM (31 << 20)

static uint32_t program_bytes;

static int load(const char* path) {
    static uint8_t program[MAX_PROGRAM];
    FILE* f = fopen(path, "rb");
    size_t bytes = f ? fread(program, 1, sizeof program, f) : 0;
    if (f) {
        fclose(f);
    }
    if (!bytes) {
        fprintf(stderr, "Cannot read %s\n", path);
        return -1;
    }
    // The engine would read QNW1 weights as a program and stall
    if (bytes >= 4 && !memcmp(program, "QNW1", 4)) {
        fprintf(stderr, "%s is the cpu model, hybrid needs quartznet_engine.bin\n", path);
        return -1;
    }
    program_bytes = bytes;
    engine_write(SLOT, program, bytes);
    return 0;
}

static int run(const int8_t* features, unsigned frames, unsigned valid, int8_t* logits, unsigned* out_valid) {
    // The header is channels, frames, valid frames and a zero word
    uint32_t start = SLOT - 16 - FE_BANDS * frames, header[4] = {FE_BANDS, frames, valid, 0};
    engine_write(start, header, 16);
    engine_write(start + 16, features, FE_BANDS * frames);
    if (engine_send(start, 16 + FE_BANDS * frames + program_bytes)) {
        return -1;
    }
    // The reply is output frames, valid frames, then each class padded to 16 frames
    uint32_t reply[4];
    engine_read(reply, 0, 16);
    unsigned out = reply[0], stride = (out + 15) / 16 * 16;
    *out_valid = reply[1];
    for (int k = 0; k < ENGINE_CLASSES; k++) {
        engine_read(logits + k * out, 16 + k * stride, out);
    }
    return out;
}

const runner hybrid_runner = {load, run};
