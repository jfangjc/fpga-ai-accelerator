/*
    driver for the stt-kv260 overlay
    -> the pl blocks show up as uio devices so their registers get mapped straight into this process
    -> the four u-dma-buf buffers are uncached ddr the dmas read and write -> samples then frames then commands then replies
    -> uncached memory only likes aligned 32 bit accesses so every copy in and out goes a word at a time
    -> both dmas are xilinx axi dma ip in simple mode and get polled since there are no interrupts
*/
#define _GNU_SOURCE
#include "kv260.h"
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define SLOT 0x10000          // engine command buffer -> header and features sit below this offset and the program from it
#define PACKET (8 * FE_BANDS) // one front end packet -> 64 bands of 64 bits = 512 bytes
#define MAX_PROGRAM (31 << 20)

typedef volatile uint32_t reg;

// registers (the reset gpio has word 0 for the front end and word 2 for the engine)
static reg *frontend, *frontend_dma, *reset, *engine_dma;
// dma buffers -> 0 samples to inject then 1 front end frames then 2 engine commands then 3 engine replies
static struct {
    reg* data;      // where the buffer is mapped in this process
    uint64_t phys;  // where the dma sees it in ddr
    size_t size;
} buffer[4];
static uint32_t program_bytes;

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

// finds the uio device with this name from the device tree overlay and maps its 64 kb of registers
static reg* uio(const char* name) {
    for (int i = 0; i < 32; i++) {
        char path[64], found[64] = "";
        snprintf(path, sizeof path, "/sys/class/uio/uio%d/name", i);
        FILE* f = fopen(path, "r");
        if (!f)
            continue;
        int match = fgets(found, sizeof found, f) && !strcmp(strtok(found, "\n"), name);
        fclose(f);
        if (match) {
            snprintf(path, sizeof path, "/dev/uio%d", i);
            int fd = open(path, O_RDWR | O_SYNC);
            reg* r = fd < 0 ? MAP_FAILED : mmap(NULL, 0x10000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            return r == MAP_FAILED ? NULL : r;
        }
    }
    fprintf(stderr, "kv260: no UIO device %s (is stt-kv260 loaded?)\n", name);
    return NULL;
}

// maps u-dma-buf n and reads its physical address so the dma can be pointed at it
static int open_buffer(int n) {
    char path[64];
    snprintf(path, sizeof path, "/sys/class/u-dma-buf/udmabuf%d/phys_addr", n);
    FILE* f = fopen(path, "r");
    int ok = f && fscanf(f, "%lx", &buffer[n].phys) == 1;
    if (f)
        fclose(f);
    snprintf(path, sizeof path, "/sys/class/u-dma-buf/udmabuf%d/size", n);
    f = fopen(path, "r");
    ok = ok && f && fscanf(f, "%zu", &buffer[n].size) == 1;
    if (f)
        fclose(f);
    snprintf(path, sizeof path, "/dev/udmabuf%d", n);
    // -> O_SYNC keeps the mapping uncached so the dma and the cpu always see the same bytes
    int fd = ok ? open(path, O_RDWR | O_SYNC) : -1;
    void* p = fd < 0 ? MAP_FAILED : mmap(NULL, buffer[n].size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) {
        fprintf(stderr, "kv260: no DMA buffer udmabuf%d (load u-dma-buf)\n", n);
        return -1;
    }
    buffer[n].data = p;
    return 0;
}

// copies bytes into a dma buffer one aligned 32 bit word at a time
static void put(reg* dst, const void* src, size_t bytes) {
    const uint8_t* s = src;
    for (size_t i = 0; i < bytes / 4; i++) {
        uint32_t w;
        memcpy(&w, s + 4 * i, 4);
        dst[i] = w;
    }
    // -> a leftover 1 to 3 bytes still go out as one whole word padded with zeros
    if (bytes % 4) {
        uint32_t w = 0;
        memcpy(&w, s + bytes / 4 * 4, bytes % 4);
        dst[bytes / 4] = w;
    }
}

// copies bytes out of a dma buffer one aligned 32 bit word at a time
static void get(void* dst, const reg* src, size_t bytes) {
    uint8_t* d = dst;
    for (size_t i = 0; i < bytes / 4; i++) {
        uint32_t w = src[i];
        memcpy(d + 4 * i, &w, 4);
    }
    if (bytes % 4) {
        uint32_t w = src[bytes / 4];
        memcpy(d + bytes / 4 * 4, &w, bytes % 4);
    }
}

// holds one block in reset for 100 us through the reset gpio
static void pulse(int word) {
    reset[word] = 1;
    usleep(100);
    reset[word] = 0;
    usleep(100);
}

// NOTE: axi dma in simple mode (register offsets from the xilinx axi dma map)

// soft resets the dma and waits for the reset bit to clear
static void dma_reset(reg* dma) {
    dma[0x00 / 4] = 4;
    while (dma[0x00 / 4] & 4)
        ;
}

// mm2s -> reads bytes from ddr at address and streams them out to the pl
static void dma_send(reg* dma, uint64_t address, uint32_t bytes) {
    dma[0x00 / 4] = 1;                          // run
    dma[0x18 / 4] = (uint32_t)address;
    dma[0x1c / 4] = (uint32_t)(address >> 32);
    dma[0x28 / 4] = bytes;                      // writing the length is what starts the transfer
}

// s2mm -> arms the dma to write the next stream from the pl into ddr at address
static void dma_receive(reg* dma, uint64_t address, uint32_t bytes) {
    dma[0x30 / 4] = 1;                          // run
    dma[0x48 / 4] = (uint32_t)address;
    dma[0x4c / 4] = (uint32_t)(address >> 32);
    dma[0x58 / 4] = bytes;                      // the most it may write -> the stream's tlast ends it early
}

// polls the s2mm idle bit until the reply has landed -> -1 on timeout
static int dma_wait(reg* dma, int timeout_ms) {
    double end = now() + timeout_ms / 1000.0;
    while (!(dma[0x34 / 4] & 2)) {
        if (now() > end)
            return -1;
        usleep(50);
    }
    return 0;
}

// NOTE: setup

// maps every register block and buffer then resets the engine and its dma
int kv260_open(void) {
    frontend = uio("stt-frontend");
    frontend_dma = uio("stt-frontend-dma");
    reset = uio("stt-reset");
    engine_dma = uio("stt-engine-dma");
    if (!frontend || !frontend_dma || !reset || !engine_dma)
        return -1;
    for (int n = 0; n < 4; n++)
        if (open_buffer(n))
            return -1;
    pulse(2);
    dma_reset(engine_dma);
    return 0;
}

// NOTE: front end

// resets the front end and its dma then starts it with this source
void fe_start(unsigned control) {
    pulse(0);
    dma_reset(frontend_dma);
    frontend[0] = control;
}

// changes the source or the led without a reset
void fe_control(unsigned control) { frontend[0] = control; }

// sends pcm samples to the front end through mm2s in place of the microphone
int fe_inject(const int16_t* pcm, size_t samples) {
    if (2 * samples > buffer[0].size)
        return -1;
    put(buffer[0].data, pcm, 2 * samples);
    dma_send(frontend_dma, buffer[0].phys, 2 * samples);
    return 0;
}

// waits for the next 10 ms frame of 64 mel band powers
int fe_frame(uint64_t power[FE_BANDS], int timeout_ms) {
    // -> arm s2mm for one 512 byte packet then copy it out once it lands in udmabuf1
    dma_receive(frontend_dma, buffer[1].phys, PACKET);
    if (dma_wait(frontend_dma, timeout_ms))
        return -1;
    get(power, buffer[1].data, PACKET);
    return 0;
}

// reports a button press since the last call and clears it
int fe_button(void) {
    int pressed = frontend[1] & 1;
    if (pressed)
        frontend[1] = 1; // write 1 to clear
    return pressed;
}

// NOTE: engine

// copies the 20 mb program (records and params and weights for all 186 layers) into udmabuf2 once at startup
int engine_load(const char* path) {
    static uint8_t program[MAX_PROGRAM];
    FILE* f = fopen(path, "rb");
    size_t bytes = f ? fread(program, 1, sizeof program, f) : 0;
    if (f)
        fclose(f);
    if (!bytes) {
        fprintf(stderr, "kv260: cannot read %s\n", path);
        return -1;
    }
    program_bytes = bytes;
    // -> it lives at SLOT so each run only has to write the header and features just below it
    put(buffer[2].data + SLOT / 4, program, bytes);
    return 0;
}

// one engine run -> int8 features in and 29 class scores per output frame back
int engine_run(const int8_t* features, unsigned frames, unsigned valid, int8_t* logits, unsigned* out_valid) {
    // -> header and features go right below the program so header + features + program is one contiguous command
    uint32_t start = SLOT - 16 - FE_BANDS * frames, header[4] = {FE_BANDS, frames, valid, 0};
    put(buffer[2].data + start / 4, header, 16);
    put(buffer[2].data + start / 4 + 4, features, FE_BANDS * frames);
    // -> arm the reply side first so no logits are lost then one mm2s transfer sends the whole command
    dma_receive(engine_dma, buffer[3].phys, buffer[3].size);
    dma_send(engine_dma, buffer[2].phys + start, 16 + FE_BANDS * frames + program_bytes);
    // -> a stuck engine gets reset so the next run starts clean
    if (dma_wait(engine_dma, 2000)) {
        fprintf(stderr, "kv260: engine timeout\n");
        pulse(2);
        dma_reset(engine_dma);
        return -1;
    }
    // -> the reply is output frames then valid frames then each class padded to a multiple of 16 frames
    reg* r = buffer[3].data;
    unsigned out = r[0], stride = (out + 15) / 16 * 16;
    *out_valid = r[1];
    for (int k = 0; k < ENGINE_CLASSES; k++)
        get(logits + k * out, r + 4 + k * stride / 4, out);
    return out;
}
