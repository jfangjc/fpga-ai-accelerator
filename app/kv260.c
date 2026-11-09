// Driver for the KV260 overlay. DMA buffers are uncached, so copies use aligned 32-bit words
#define _GNU_SOURCE
#include "kv260.h"
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define PACKET (8 * FE_BANDS) // front end packet of 64 bands of 64 bits

typedef volatile uint32_t reg;

// Registers (resets word 0 front end, word 2 engine) and DMA buffers for samples, frames, commands, replies
static reg *frontend, *frontend_dma, *reset, *engine_dma;
static struct {
    reg* data;
    uint64_t phys;
    size_t size;
} buffer[4];

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static reg* uio(const char* name) {
    for (int i = 0; i < 32; i++) {
        char path[64], found[64] = "";
        snprintf(path, sizeof path, "/sys/class/uio/uio%d/name", i);
        FILE* f = fopen(path, "r");
        if (!f) {
            continue;
        }
        int match = fgets(found, sizeof found, f) && !strcmp(strtok(found, "\n"), name);
        fclose(f);
        if (match) {
            snprintf(path, sizeof path, "/dev/uio%d", i);
            int fd = open(path, O_RDWR | O_SYNC);
            reg* r = fd < 0 ? MAP_FAILED : mmap(NULL, 0x10000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            return r == MAP_FAILED ? NULL : r;
        }
    }
    fprintf(stderr, "No UIO device %s (is the overlay loaded?)\n", name);
    return NULL;
}

static int open_buffer(int n) {
    char path[64];
    snprintf(path, sizeof path, "/sys/class/u-dma-buf/udmabuf%d/phys_addr", n);
    FILE* f = fopen(path, "r");
    int ok = f && fscanf(f, "%lx", &buffer[n].phys) == 1;
    if (f) {
        fclose(f);
    }
    snprintf(path, sizeof path, "/sys/class/u-dma-buf/udmabuf%d/size", n);
    f = fopen(path, "r");
    ok = ok && f && fscanf(f, "%zu", &buffer[n].size) == 1;
    if (f) {
        fclose(f);
    }
    snprintf(path, sizeof path, "/dev/udmabuf%d", n);
    int fd = ok ? open(path, O_RDWR | O_SYNC) : -1;
    void* p = fd < 0 ? MAP_FAILED : mmap(NULL, buffer[n].size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) {
        fprintf(stderr, "No DMA buffer udmabuf%d (load u-dma-buf)\n", n);
        return -1;
    }
    buffer[n].data = p;
    return 0;
}

static void put(reg* dst, const void* src, size_t bytes) {
    const uint8_t* s = src;
    for (size_t i = 0; i < bytes / 4; i++) {
        uint32_t w;
        memcpy(&w, s + 4 * i, 4);
        dst[i] = w;
    }
    if (bytes % 4) {
        uint32_t w = 0;
        memcpy(&w, s + bytes / 4 * 4, bytes % 4);
        dst[bytes / 4] = w;
    }
}

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

static void pulse(int word) {
    reset[word] = 1;
    usleep(100);
    reset[word] = 0;
    usleep(100);
}

// AXI DMA in simple mode
static void dma_reset(reg* dma) {
    dma[0x00 / 4] = 4;
    while (dma[0x00 / 4] & 4) {
    }
}

static void dma_send(reg* dma, uint64_t address, uint32_t bytes) {
    dma[0x00 / 4] = 1;
    dma[0x18 / 4] = (uint32_t)address;
    dma[0x1c / 4] = (uint32_t)(address >> 32);
    dma[0x28 / 4] = bytes;
}

static void dma_receive(reg* dma, uint64_t address, uint32_t bytes) {
    dma[0x30 / 4] = 1;
    dma[0x48 / 4] = (uint32_t)address;
    dma[0x4c / 4] = (uint32_t)(address >> 32);
    dma[0x58 / 4] = bytes;
}

// -1 on timeout
static int dma_wait(reg* dma, int timeout_ms) {
    double end = now() + timeout_ms / 1000.0;
    while (!(dma[0x34 / 4] & 2)) {
        if (now() > end) {
            return -1;
        }
        usleep(50);
    }
    return 0;
}

int kv260_open(void) {
    frontend = uio("frontend");
    frontend_dma = uio("frontend-dma");
    reset = uio("reset");
    engine_dma = uio("engine-dma");
    if (!frontend || !frontend_dma || !reset || !engine_dma) {
        return -1;
    }
    for (int n = 0; n < 4; n++) {
        if (open_buffer(n)) {
            return -1;
        }
    }
    pulse(2);
    dma_reset(engine_dma);
    return 0;
}

void fe_start(unsigned control) {
    pulse(0);
    dma_reset(frontend_dma);
    frontend[0] = control;
}

void fe_control(unsigned control) { frontend[0] = control; }

int fe_inject(const int16_t* pcm, size_t samples) {
    if (2 * samples > buffer[0].size) {
        return -1;
    }
    put(buffer[0].data, pcm, 2 * samples);
    dma_send(frontend_dma, buffer[0].phys, 2 * samples);
    return 0;
}

int fe_frame(uint64_t power[FE_BANDS], int timeout_ms) {
    dma_receive(frontend_dma, buffer[1].phys, PACKET);
    if (dma_wait(frontend_dma, timeout_ms)) {
        return -1;
    }
    get(power, buffer[1].data, PACKET);
    return 0;
}

int fe_button(void) {
    int pressed = frontend[1] & 1;
    if (pressed) {
        frontend[1] = 1;
    }
    return pressed;
}

void engine_write(uint32_t offset, const void* data, size_t bytes) { put(buffer[2].data + offset / 4, data, bytes); }

int engine_send(uint32_t offset, uint32_t bytes) {
    dma_receive(engine_dma, buffer[3].phys, buffer[3].size);
    dma_send(engine_dma, buffer[2].phys + offset, bytes);
    if (dma_wait(engine_dma, 2000)) {
        fputs("Engine timeout\n", stderr);
        pulse(2);
        dma_reset(engine_dma);
        return -1;
    }
    return 0;
}

void engine_read(void* data, uint32_t offset, size_t bytes) { get(data, buffer[3].data + offset / 4, bytes); }
