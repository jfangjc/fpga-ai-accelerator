// Driver for the PL front end and engine, opened once per process with kv260_open
#ifndef KV260_H
#define KV260_H
#include <stddef.h>
#include <stdint.h>

// Front end control is one source plus options
enum {
    FE_MIC = 1,        // enable, the microphone
    FE_INJECT = 3,     // enable, samples from fe_inject
    FE_PACED = 1 << 2, // injection paced at 16 kHz, as the microphone would deliver it
    FE_LED = 1 << 3,
};
#define FE_BANDS 64
#define ENGINE_FRAMES 640 // longest input of 6.4 s
#define ENGINE_CLASSES 29

int kv260_open(void);

// Reset the front end and start it
void fe_start(unsigned control);
void fe_control(unsigned control);
// Queue PCM for injection, -1 if too long
int fe_inject(const int16_t* pcm, size_t samples);
// Next frame's band powers, -1 on timeout
int fe_frame(uint64_t power[FE_BANDS], int timeout_ms);
// 1 if the button was pressed since the last call
int fe_button(void);

// Copy into the engine's command buffer at a word-aligned offset
void engine_write(uint32_t offset, const void* data, size_t bytes);
// Send bytes of the command buffer from offset and wait for the reply, -1 on timeout
int engine_send(uint32_t offset, uint32_t bytes);
// Copy from the engine's reply buffer at a word-aligned offset
void engine_read(void* data, uint32_t offset, size_t bytes);

#endif
