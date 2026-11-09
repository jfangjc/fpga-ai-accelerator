// Live captions from the microphone
// A capture thread reads the front end, and live_run finds phrases and previews each until it is final
#define _GNU_SOURCE
#include "live.h"
#include "phrase.h"
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define RING 1024 // 10 s of frames
#define PREVIEW 12 // a preview every 120 ms
#define GAIN 100 // the microphone's 20 dB gain

// Frames from the capture thread
static struct {
    uint64_t power[RING][FE_BANDS];
    long count;
    int failed;
    pthread_mutex_t lock;
    pthread_cond_t ready;
} ring = {.lock = PTHREAD_MUTEX_INITIALIZER, .ready = PTHREAD_COND_INITIALIZER};

static volatile sig_atomic_t stopping;

static void stop(int signal) {
    (void)signal;
    stopping = 1;
}

static void* capture(void* unused) {
    (void)unused;
    while (!stopping) {
        uint64_t power[FE_BANDS];
        int failed = fe_frame(power, 1000);
        pthread_mutex_lock(&ring.lock);
        if (failed) {
            ring.failed = 1;
        }
        else {
            memcpy(ring.power[ring.count++ % RING], power, sizeof power);
        }
        pthread_cond_signal(&ring.ready);
        pthread_mutex_unlock(&ring.lock);
        if (failed) {
            break;
        }
    }
    return NULL;
}

// Wait for frame t and count the frames queued after it, 0 once the capture has stopped
static int next_frame(long t, uint64_t power[FE_BANDS], long* waiting) {
    pthread_mutex_lock(&ring.lock);
    while (ring.count <= t && !ring.failed && !stopping) {
        pthread_cond_wait(&ring.ready, &ring.lock);
    }
    int ok = ring.count > t;
    if (ok && ring.count - t > RING) {
        fprintf(stderr, "Fell %ld frames behind\n", ring.count - t);
        ok = 0;
    }
    if (ok) {
        memcpy(power, ring.power[t % RING], sizeof ring.power[0]);
    }
    *waiting = ring.count - t - 1;
    pthread_mutex_unlock(&ring.lock);
    return ok;
}

// Seconds on the monotonic clock
static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

// phrase_decode, timed in ms
static long decode(const runner* model, char text[TEXT], double* ms) {
    double started = now();
    long kept = phrase_decode(model, text);
    *ms = 1000 * (now() - started);
    return kept;
}

int live_run(const runner* model, const char* path, const char* prior, const live_events* on) {
    if (phrase_prior(prior)) {
        fprintf(stderr, "Cannot read %s\n", prior);
        return 1;
    }
    if (kv260_open() || model->load(path)) {
        return 1;
    }
    signal(SIGINT, stop);
    signal(SIGTERM, stop);
    fe_start(FE_MIC);
    pthread_t thread;
    pthread_create(&thread, NULL, capture, NULL);

    static char text[TEXT];
    int state = PHRASE_SILENT, failed = 0;
    long t, waiting;
    double decode_ms = 0;
    uint64_t power[FE_BANDS];
    for (t = 0; next_frame(t, power, &waiting); t++) {
        int speaking = state == PHRASE_SPEECH;
        state = phrase_push(power, GAIN);
        if (!speaking && state == PHRASE_SPEECH && on->status) {
            on->status(1, decode_ms, waiting);
        }
        // The final decode of a phrase, or a preview skipped when behind
        int final = state == PHRASE_END;
        if (final || (state == PHRASE_SPEECH && phrase_length() % PREVIEW == 0 && waiting < PREVIEW)) {
            long kept = decode(model, text, &decode_ms);
            if (kept < 0) {
                failed = 1;
                break;
            }
            if (final || *text) {
                on->caption(t, text, kept, final);
            }
            if (on->status) {
                on->status(!final, decode_ms, waiting);
            }
        }
        // The LED is on during a phrase
        if (speaking != (state == PHRASE_SPEECH)) {
            fe_control(state == PHRASE_SPEECH ? FE_MIC | FE_LED : FE_MIC);
        }
        if (on->frame) {
            on->frame(t, state == PHRASE_SPEECH);
        }
        // The button ends the session
        if (fe_button()) {
            stopping = 1;
        }
    }
    if (state == PHRASE_SPEECH && !failed) { // stopped mid-phrase
        long kept = decode(model, text, &decode_ms);
        if (kept < 0) {
            failed = 1;
        }
        else {
            on->caption(t, text, kept, 1);
        }
    }
    stopping = 1;
    pthread_join(thread, NULL);
    fe_control(0);
    if (ring.failed) {
        fputs("Front end stopped\n", stderr);
        return 1;
    }
    return failed;
}
