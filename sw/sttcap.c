// Live captions: sttcap quartznet_engine.bin running_prior.json [--wav clip.wav [--paced]] [--gain DB] [--port PORT]
//                [--display]
#define _GNU_SOURCE
#include "display.h"
#include "kv260.h"
#include "phrase.h"
#include <math.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define RING 1024  // captured frames waiting to be processed: 10 s
#define LEAD 20    // a phrase starts 0.2 s before the first speech frame
#define SILENCE 70 // and ends after 0.7 s without speech
#define PREVIEW 12 // a preview every 120 ms
#define CLIENTS 8

// Captured frames
static struct {
    uint64_t power[RING][FE_BANDS];
    long count, total; // frames captured, and frames expected (-1 for the microphone)
    int failed;
    pthread_mutex_t lock;
    pthread_cond_t ready;
} ring = {.lock = PTHREAD_MUTEX_INITIALIZER, .ready = PTHREAD_COND_INITIALIZER};

static volatile sig_atomic_t stopping;
static double gain = 1;
static float history[RING][FE_BANDS]; // log mel of recent frames
static int server = -1, clients[CLIENTS], display;
static char captions[4096]; // finals so far, for the screen

static void stop(int signal) {
    (void)signal;
    stopping = 1;
}

static void* capture(void* unused) {
    (void)unused;
    while (!stopping && ring.count != ring.total) {
        uint64_t power[FE_BANDS];
        int failed = fe_frame(power, 1000);
        pthread_mutex_lock(&ring.lock);
        if (failed)
            ring.failed = 1;
        else
            memcpy(ring.power[ring.count++ % RING], power, sizeof power);
        pthread_cond_signal(&ring.ready);
        pthread_mutex_unlock(&ring.lock);
        if (failed)
            break;
    }
    return NULL;
}

// Wait for frame t; 0 once the capture has ended without it
static int next_frame(long t, uint64_t power[FE_BANDS], long* waiting) {
    pthread_mutex_lock(&ring.lock);
    while (ring.count <= t && !ring.failed && !stopping && ring.count != ring.total)
        pthread_cond_wait(&ring.ready, &ring.lock);
    int ok = ring.count > t;
    if (ok && ring.count - t > RING) {
        fprintf(stderr, "sttcap: fell %ld frames behind\n", ring.count - t);
        ok = 0;
    }
    if (ok)
        memcpy(power, ring.power[t % RING], 8 * FE_BANDS);
    *waiting = ring.count - t - 1;
    pthread_mutex_unlock(&ring.lock);
    return ok;
}

// Send a line to the TCP clients
static void broadcast(const char* line) {
    int fd;
    while (server >= 0 && (fd = accept4(server, NULL, NULL, SOCK_NONBLOCK)) >= 0) {
        for (int i = 0; i < CLIENTS && fd >= 0; i++)
            if (!clients[i]) {
                clients[i] = fd;
                fd = -1;
            }
        if (fd >= 0) // no room
            close(fd);
    }
    for (int i = 0; i < CLIENTS; i++)
        if (clients[i] && send(clients[i], line, strlen(line), MSG_NOSIGNAL) < 0) {
            close(clients[i]);
            clients[i] = 0;
        }
}

// A preview's words shared with the previous preview are stable, the rest tentative
static void emit(unsigned id, long t, int final, const char* text, const char* previous) {
    size_t stable = 0;
    for (size_t i = 0; !final && text[i] && text[i] == previous[i]; i++)
        if (text[i + 1] == ' ' || (!text[i + 1] && !previous[i + 1]))
            stable = i + 1;
    char line[2 * TEXT + 128];
    if (final)
        snprintf(line, sizeof line, "{\"t\":%.2f,\"phrase\":%u,\"final\":true,\"text\":\"%s\"}\n", t / 100.0, id, text);
    else
        snprintf(line, sizeof line,
                 "{\"t\":%.2f,\"phrase\":%u,\"final\":false,\"text\":\"%.*s\",\"tentative\":\"%s\"}\n", t / 100.0, id,
                 (int)stable, text, text + stable + (text[stable] == ' '));
    fputs(line, stdout);
    fflush(stdout);
    broadcast(line);
    if (final && *text) {
        size_t n = strlen(captions), m = strlen(text);
        if (n + m + 2 > sizeof captions) { // keep the newest half
            memmove(captions, captions + n / 2, n - n / 2 + 1);
            n = strlen(captions);
        }
        snprintf(captions + n, sizeof captions - n, "%s%s", n ? " " : "", text);
    }
    display_show(captions, final ? "" : text);
}

// Speech: energy 8 dB above the noise floor of the last 5 s
static int speech(double e, long t) {
    static double block, floors[10] = {INFINITY, INFINITY, INFINITY, INFINITY, INFINITY,
                                       INFINITY, INFINITY, INFINITY, INFINITY, INFINITY};
    double floor = INFINITY;
    block = t % 50 == 0 || e < block ? e : block;
    if (t % 50 == 49) {
        memmove(floors, floors + 1, 9 * sizeof(double));
        floors[9] = block;
    }
    for (int i = 0; i < 10; i++)
        floor = fmin(floor, floors[i]);
    floor = fmin(floor, block);
    return t > 30 && e >= 2e-6 && e >= floor * 6.31; // the microphone settles in the first 0.3 s
}

int main(int argc, char** argv) {
    const char* wav = NULL;
    int port = 0, paced = 0;
    for (int i = 3; i < argc; i++) {
        if (!strcmp(argv[i], "--wav") && i + 1 < argc)
            wav = argv[++i];
        else if (!strcmp(argv[i], "--paced"))
            paced = 1;
        else if (!strcmp(argv[i], "--gain") && i + 1 < argc)
            gain = pow(10, atof(argv[++i]) / 10);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc)
            port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--display"))
            display = 1;
        else
            argc = 0;
    }
    if (argc < 3) {
        fprintf(stderr, "usage: sttcap quartznet_engine.bin running_prior.json [--wav clip.wav [--paced]] "
                        "[--gain DB] [--port PORT] [--display]\n");
        return 2;
    }
    if (phrase_prior(argv[2])) {
        fprintf(stderr, "sttcap: cannot read %s\n", argv[2]);
        return 1;
    }
    if (kv260_open() || engine_load(argv[1]))
        return 1;
    signal(SIGINT, stop);
    signal(SIGTERM, stop);
    if (port) {
        server = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
        struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons(port)};
        setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &(int){1}, sizeof(int));
        if (bind(server, (struct sockaddr*)&address, sizeof address) || listen(server, CLIENTS)) {
            perror("sttcap: TCP port");
            return 1;
        }
    }
    if (display)
        display_open();

    ring.total = -1;
    if (wav) {
        // 16 kHz mono 16-bit WAV
        FILE* f = fopen(wav, "rb");
        static int16_t pcm[1 << 23];
        size_t n = f && fseek(f, 44, SEEK_SET) == 0 ? fread(pcm, 2, sizeof pcm / 2, f) : 0;
        if (f)
            fclose(f);
        if (n < 320) {
            fprintf(stderr, "sttcap: cannot read %s\n", wav);
            return 1;
        }
        ring.total = (n - 320) / 160 + 1;
        fe_start(FE_INJECT | (paced ? FE_PACED : 0));
        fe_inject(pcm, n);
    }
    else
        fe_start(FE_MIC);
    pthread_t thread;
    pthread_create(&thread, NULL, capture, NULL);

    unsigned id = 0;
    int in_phrase = 0, silent = 0;
    long waiting;
    static char text[TEXT], previous[TEXT];
    uint64_t power[FE_BANDS];
    for (long t = 0; next_frame(t, power, &waiting); t++) {
        double energy = 0; // bands 1-63: band 0 holds the microphone's DC
        for (int b = 1; b < FE_BANDS; b++)
            energy += ldexp((double)power[b], -42);
        for (int b = 0; b < FE_BANDS; b++)
            history[t % RING][b] = logf((float)(ldexp((double)power[b], -42) * gain) + 0x1p-24f);
        int voiced = speech(energy, t);
        if (!in_phrase && voiced) {
            in_phrase = 1;
            silent = 0;
            phrase_begin();
            for (long k = t - LEAD < 0 ? 0 : t - LEAD; k < t; k++)
                phrase_add(history[k % RING]);
            previous[0] = 0;
            if (!wav)
                fe_control(FE_MIC | FE_LED);
        }
        if (in_phrase) {
            phrase_add(history[t % RING]);
            silent = voiced ? 0 : silent + 1;
            if (silent == SILENCE || phrase_length() == PHRASE || ring.total == t + 1 || stopping) {
                phrase_decode(0, text);
                emit(id++, t, 1, text, "");
                in_phrase = 0;
                if (!wav)
                    fe_control(FE_MIC);
            }
            else if (phrase_length() % PREVIEW == 0 && waiting < PREVIEW) { // the last 6.4 s; skipped when behind
                phrase_decode(phrase_length() > ENGINE_FRAMES ? phrase_length() - ENGINE_FRAMES : 0, text);
                if (*text)
                    emit(id, t, 0, text, previous);
                strcpy(previous, text);
            }
        }
        // The button ends the session
        if (!wav && fe_button())
            stopping = 1;
    }
    if (in_phrase) { // stopped mid-phrase
        phrase_decode(0, text);
        emit(id, ring.count, 1, text, "");
    }
    stopping = 1;
    pthread_join(thread, NULL);
    fe_control(0);
    if (ring.failed) {
        fprintf(stderr, "sttcap: front end stopped\n");
        return 1;
    }
    return 0;
}
