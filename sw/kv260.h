// driver for the pl front end and engine -> call kv260_open once per process
#ifndef KV260_H
#define KV260_H
#include <stddef.h>
#include <stdint.h>

// front end control word -> one source plus options
enum {
    FE_MIC = 1,        // enable with the microphone as the source
    FE_INJECT = 3,     // enable with samples from fe_inject as the source
    FE_PACED = 1 << 2, // injected samples go in at 16 khz like the microphone would deliver them
    FE_LED = 1 << 3,   // the led on the mic pmod -> lit while a phrase is open
};
#define FE_BANDS 64        // mel bands per frame
#define ENGINE_FRAMES 640  // longest engine input -> 6.4 s
#define ENGINE_CLASSES 29  // space then a-z then apostrophe then the ctc blank

// setup -> maps the registers and dma buffers
int kv260_open(void);

// front end -> called by sttcap's capture thread and main loop
void fe_start(unsigned control);
void fe_control(unsigned control);
// queues pcm for injection -> -1 if too long
int fe_inject(const int16_t* pcm, size_t samples);
// next frame's band powers -> -1 on timeout
int fe_frame(uint64_t power[FE_BANDS], int timeout_ms);
// 1 if the button was pressed since the last call
int fe_button(void);

// engine -> load once at startup then run once per preview or final window
// loads data/export/quartznet_engine.bin
int engine_load(const char* path);
// features [64][frames] with frames a multiple of 16 -> logits [29][out] and returns out or -1 on error
int engine_run(const int8_t* features, unsigned frames, unsigned valid, int8_t* logits, unsigned* out_valid);

#endif
