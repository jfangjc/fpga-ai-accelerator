// Live captions from the microphone with caption hybrid|cpu MODEL running_prior.json
// A capture thread reads the front end, and the main loop finds phrases and previews each until it is final
#define _GNU_SOURCE
#include "phrase.h"
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define RING 1024 // 10 s of frames
#define PREVIEW 12 // a preview every 120 ms
#define GAIN 100 // the microphone's 20 dB gain
#define WIDTH 79 // characters on a line of an 80-column terminal

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

// Characters of text that fit on one line, broken at the last space that fits
static size_t fit(const char* text) {
    size_t n = strlen(text), cut = WIDTH;
    if (n <= WIDTH) {
        return n;
    }
    while (cut && text[cut] != ' ') {
        cut--;
    }
    return cut ? cut : WIDTH;
}

// Kept text goes out as full lines, and the rest is a preview until the final, wrapped over lines in a terminal
static void show(const char* text, size_t kept, int final) {
    static size_t printed;
    static int drawn;
    if (drawn > 1) {
        printf("\033[%dA", drawn - 1);
    }
    printf("\r\033[J");
    drawn = 0;
    size_t n = strlen(text);
    while (printed < n) {
        size_t cut = fit(text + printed), used = cut + (text[printed + cut] == ' ');
        if (!final && (n - printed <= WIDTH || printed + used > kept)) {
            break;
        }
        printf("%.*s\n", (int)cut, text + printed);
        printed += used;
    }
    for (size_t p = printed; p < n && !final; drawn++) {
        size_t cut = isatty(STDOUT_FILENO) ? fit(text + p) : n - p;
        printf("%s%.*s", drawn ? "\n" : "", (int)cut, text + p);
        p += cut + (text[p + cut] == ' ');
    }
    if (final) {
        printed = 0;
    }
    fflush(stdout);
}

int main(int argc, char** argv) {
    const runner* model = argc == 4 ? runner_find(argv[1]) : NULL;
    if (!model) {
        fputs("Usage: caption hybrid|cpu MODEL PRIOR\n", stderr);
        return 2;
    }
    if (phrase_prior(argv[3])) {
        fprintf(stderr, "Cannot read %s\n", argv[3]);
        return 1;
    }
    if (kv260_open() || model->load(argv[2])) {
        return 1;
    }
    signal(SIGINT, stop);
    signal(SIGTERM, stop);
    fe_start(FE_MIC);
    pthread_t thread;
    pthread_create(&thread, NULL, capture, NULL);

    static char text[TEXT];
    int state = PHRASE_SILENT, failed = 0;
    long waiting;
    uint64_t power[FE_BANDS];
    for (long t = 0; next_frame(t, power, &waiting); t++) {
        int speaking = state == PHRASE_SPEECH;
        state = phrase_push(power, GAIN);
        if (state == PHRASE_END) {
            long kept = phrase_decode(model, text);
            if (kept < 0) {
                failed = 1;
                break;
            }
            show(text, kept, 1);
        }
        else if (state == PHRASE_SPEECH && phrase_length() % PREVIEW == 0 && waiting < PREVIEW) {
            // A preview, skipped when behind
            long kept = phrase_decode(model, text);
            if (kept < 0) {
                failed = 1;
                break;
            }
            if (*text) {
                show(text, kept, 0);
            }
        }
        // The LED is on during a phrase
        if (speaking != (state == PHRASE_SPEECH)) {
            fe_control(state == PHRASE_SPEECH ? FE_MIC | FE_LED : FE_MIC);
        }
        // The button ends the session
        if (fe_button()) {
            stopping = 1;
        }
    }
    if (state == PHRASE_SPEECH && !failed) { // stopped mid-phrase
        long kept = phrase_decode(model, text);
        if (kept < 0) {
            failed = 1;
        }
        else {
            show(text, kept, 1);
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
