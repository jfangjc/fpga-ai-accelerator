// Live captions from the microphone, shared by the programs that show them
#ifndef LIVE_H
#define LIVE_H
#include "runner.h"

// Callbacks from live_run, status and frame may be NULL
typedef struct {
    // The phrase so far at frame t, whose first kept characters stay in later decodes, until final
    void (*caption)(long t, const char* text, size_t kept, int final);
    // Whether a phrase is open, the last decode's time and the frames queued behind it
    void (*status)(int speaking, double decode_ms, long waiting);
    // After each frame t
    void (*frame)(long t, int speaking);
} live_events;

// Caption the microphone until Ctrl-C or the button, 0 on success
int live_run(const runner* model, const char* path, const char* prior, const live_events* on);

#endif
