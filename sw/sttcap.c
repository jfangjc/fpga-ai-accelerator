/*
    live captions on the kv260
    usage -> sttcap quartznet_engine.bin running_prior.json [--wav clip.wav [--paced]] [--gain DB] [--port PORT]
             [--display [--status] [--clear-after SEC] [--theme night|butter]] [--settle N]
    -> a capture thread waits on the front end dma and drops each 10 ms frame of 64 mel band powers into a ring
    -> the main loop takes the log and decides if it's speech then feeds the phrase one frame at a time
    -> while someone talks a preview runs every 120 ms and once they stop for 0.7 s the whole phrase gets a final
    -> emit sends every caption to the log and the tcp clients and the screen
*/
#define _GNU_SOURCE
#include "display.h"
#include "kv260.h"
#include "phrase.h"
#include "settle.h"
#include <math.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define RING 1024  // captured frames that can wait to be processed -> 10 s
#define LEAD 20    // a phrase starts 0.2 s before the first speech frame so the first sound isn't cut off
#define SILENCE 70 // and ends after 0.7 s without speech
#define PREVIEW 12 // a preview every 120 ms
#define CLIENTS 8  // tcp clients at once

// frames the capture thread has handed over -> shared with the main loop under the lock
static struct {
    uint64_t power[RING][FE_BANDS];
    long count, total; // frames captured and frames expected (-1 for the microphone)
    int failed;
    pthread_mutex_t lock;
    pthread_cond_t ready;
} ring = {.lock = PTHREAD_MUTEX_INITIALIZER, .ready = PTHREAD_COND_INITIALIZER};

static volatile sig_atomic_t stopping;
static double gain = 1;
static float history[RING][FE_BANDS]; // log mel of recent frames so a phrase can start LEAD frames back
static int server = -1, clients[CLIENTS], display, status, theme = DISPLAY_NIGHT;
static double clear_after;  // seconds of silence before the screen clears -> 0 means never
static int settle;          // previews that must agree before a word settles -> 0 means just the previous preview
_Static_assert(SETTLE_TEXT == TEXT, "settle.h buffers match phrase.h");
static char captions[4096]; // finals before the live phrase for the history lines

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

// status strip -> the speaking dot then the last decode's engine time and the caption delay
static void show_status(int speaking, double decode_ms, long waiting) {
    char line[96];
    if (!status)
        return;
    if (decode_ms > 0)
        snprintf(line, sizeof line, "fpga engine  decode %.0f ms  delay %.0f ms", decode_ms,
                 decode_ms + 10.0 * waiting);
    else
        snprintf(line, sizeof line, "fpga engine");
    display_status(speaking, line);
}

// ctrl-c or systemd stopping us -> finish the phrase and leave cleanly
static void stop(int signal) {
    (void)signal;
    stopping = 1;
}

// capture thread -> waits for each front end packet and puts it in the ring
static void* capture(void* unused) {
    (void)unused;
    while (!stopping && ring.count != ring.total) {
        uint64_t power[FE_BANDS];
        // -> blocks on the front end dma for up to 1 s so the main loop never waits on hardware itself
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

// waits for frame t -> 0 once the capture has ended without it
static int next_frame(long t, uint64_t power[FE_BANDS], long* waiting) {
    pthread_mutex_lock(&ring.lock);
    while (ring.count <= t && !ring.failed && !stopping && ring.count != ring.total)
        pthread_cond_wait(&ring.ready, &ring.lock);
    int ok = ring.count > t;
    // -> more than 10 s behind means the ring already wrote over this frame
    if (ok && ring.count - t > RING) {
        fprintf(stderr, "sttcap: fell %ld frames behind\n", ring.count - t);
        ok = 0;
    }
    if (ok)
        memcpy(power, ring.power[t % RING], 8 * FE_BANDS);
    *waiting = ring.count - t - 1; // frames still queued -> this is how far behind live we are
    pthread_mutex_unlock(&ring.lock);
    return ok;
}

// sends a line to every tcp client
static void broadcast(const char* line) {
    int fd;
    // -> pick up anyone new first and the socket is nonblocking so this never waits
    while (server >= 0 && (fd = accept4(server, NULL, NULL, SOCK_NONBLOCK)) >= 0) {
        for (int i = 0; i < CLIENTS && fd >= 0; i++)
            if (!clients[i]) {
                clients[i] = fd;
                fd = -1;
            }
        if (fd >= 0) // no room
            close(fd);
    }
    // -> a client that went away gets dropped
    for (int i = 0; i < CLIENTS; i++)
        if (clients[i] && send(clients[i], line, strlen(line), MSG_NOSIGNAL) < 0) {
            close(clients[i]);
            clients[i] = 0;
        }
}

// one caption out to the log and the tcp clients and the screen
static void emit(unsigned id, long t, int final, const char* text, const char* previous) {
    static char shown[TEXT];
    size_t stable = 0;
    // -> with --settle N a word settles once N previews in a row agree on it and then stays settled (settle.c)
    if (!final && settle) {
        stable = settle_preview(text, settle, shown);
        text = shown;
    }
    // -> with --settle 0 the words this preview shares with the previous one count as stable
    else
        for (size_t i = 0; !final && text[i] && text[i] == previous[i]; i++)
            if (text[i + 1] == ' ' || (!text[i + 1] && !previous[i + 1]))
                stable = i + 1;
    // -> a final is one json line and a preview splits into its stable text and the tentative rest
    char line[2 * TEXT + 128];
    if (final)
        snprintf(line, sizeof line, "{\"t\":%.2f,\"phrase\":%u,\"final\":true,\"text\":\"%s\"}\n", t / 100.0, id, text);
    else
        snprintf(line, sizeof line,
                 "{\"t\":%.2f,\"phrase\":%u,\"final\":false,\"text\":\"%.*s\",\"tentative\":\"%s\"}\n", t / 100.0, id,
                 (int)stable, text, text + stable + (text[stable] == ' '));
    // -> stdout is session.jsonl since the service redirects it there
    fputs(line, stdout);
    fflush(stdout);
    broadcast(line);
    // -> the screen gets earlier finals dim then this phrase large with its stable words solid and the rest tentative
    // -> a final counts as all stable and stays on the live line until the next phrase moves it up
    display_show(captions, text, final ? strlen(text) : stable);
    if (final && *text) {
        size_t n = strlen(captions), m = strlen(text);
        if (n + m + 2 > sizeof captions) { // full so keep the newest half
            memmove(captions, captions + n / 2, n - n / 2 + 1);
            n = strlen(captions);
        }
        snprintf(captions + n, sizeof captions - n, "%s%s", n ? " " : "", text);
    }
}

// speech is energy 8 db above the noise floor of the last 5 s -> also hands back how far above the floor for the meter
static int speech(double e, long t, double* above) {
    static double block, floors[10] = {INFINITY, INFINITY, INFINITY, INFINITY, INFINITY,
                                       INFINITY, INFINITY, INFINITY, INFINITY, INFINITY};
    double floor = INFINITY;
    // -> the quietest frame of each 0.5 s block goes into a list of the last 10 blocks
    block = t % 50 == 0 || e < block ? e : block;
    if (t % 50 == 49) {
        memmove(floors, floors + 1, 9 * sizeof(double));
        floors[9] = block;
    }
    // -> the floor is the quietest of those so a long sentence doesn't drag the floor up
    for (int i = 0; i < 10; i++)
        floor = fmin(floor, floors[i]);
    floor = fmin(floor, block);
    *above = e > 0 && floor > 0 && isfinite(floor) ? 10 * log10(e / floor) : 0;
    return t > 30 && e >= 2e-6 && e >= floor * 6.31; // 6.31 is 8 db and the microphone settles in the first 0.3 s
}

int main(int argc, char** argv) {
    const char* wav = NULL;
    int port = 0, paced = 0;
    // -> anything unknown sets argc to 0 so the usage line gets printed
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
        else if (!strcmp(argv[i], "--status"))
            status = 1;
        else if (!strcmp(argv[i], "--clear-after") && i + 1 < argc)
            clear_after = atof(argv[++i]);
        else if (!strcmp(argv[i], "--theme") && i + 1 < argc && !strcmp(argv[i + 1], "butter")) {
            theme = DISPLAY_BUTTER;
            i++;
        }
        else if (!strcmp(argv[i], "--theme") && i + 1 < argc && !strcmp(argv[i + 1], "night"))
            i++;
        else if (!strcmp(argv[i], "--settle") && i + 1 < argc)
            settle = atoi(argv[++i]);
        else
            argc = 0;
    }
    if (argc < 3) {
        fprintf(stderr, "usage: sttcap quartznet_engine.bin running_prior.json [--wav clip.wav [--paced]] "
                        "[--gain DB] [--port PORT] [--display [--status] [--clear-after SEC] [--theme night|butter]] [--settle N]\n");
        return 2;
    }
    if (phrase_prior(argv[2])) {
        fprintf(stderr, "sttcap: cannot read %s\n", argv[2]);
        return 1;
    }
    // -> map the pl registers and dma buffers then copy the 20 mb program into ddr once for every run to reuse
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
    // -> no monitor isn't fatal since the log and tcp still get every caption
    if (display && display_open(status, theme))
        fprintf(stderr, "sttcap: no framebuffer; captions are not drawn\n");
    show_status(0, 0, 0);

    ring.total = -1;
    if (wav) {
        // -> 16 khz mono 16 bit wav pushed through the same front end instead of the microphone
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
    long waiting, shown = 0; // the frame of the last caption
    double decode_ms = 0, started, above, loudest = 0;
    static char text[TEXT], previous[TEXT];
    uint64_t power[FE_BANDS];
    // NOTE: main loop (one pass per 10 ms frame)
    for (long t = 0; next_frame(t, power, &waiting); t++) {
        double energy = 0; // bands 1-63 only since band 0 holds the microphone's dc
        for (int b = 1; b < FE_BANDS; b++)
            energy += ldexp((double)power[b], -42);
        // -> the log mel of every frame is kept so a phrase can start LEAD frames back
        for (int b = 0; b < FE_BANDS; b++)
            history[t % RING][b] = logf((float)(ldexp((double)power[b], -42) * gain) + 0x1p-24f);
        int voiced = speech(energy, t, &above);
        // -> the meter shows the loudest of every 50 ms at 4 db a bar so two bars is about the speech threshold
        loudest = fmax(loudest, above);
        if (t % 5 == 4) {
            display_level(loudest / 28);
            loudest = 0;
        }
        // -> speech starts a phrase and the LEAD frames before it go in first
        if (!in_phrase && voiced) {
            in_phrase = 1;
            silent = 0;
            phrase_begin();
            for (long k = t - LEAD < 0 ? 0 : t - LEAD; k < t; k++)
                phrase_add(history[k % RING]);
            previous[0] = 0;
            settle_reset();
            if (!wav)
                fe_control(FE_MIC | FE_LED);
            show_status(1, decode_ms, waiting);
        }
        if (in_phrase) {
            phrase_add(history[t % RING]);
            silent = voiced ? 0 : silent + 1;
            // -> final after 0.7 s of silence or at 30 s or when the clip or the service ends
            if (silent == SILENCE || phrase_length() == PHRASE || ring.total == t + 1 || stopping) {
                started = now();
                phrase_decode(text);
                decode_ms = 1000 * (now() - started);
                emit(id++, t, 1, text, "");
                shown = t;
                in_phrase = 0;
                if (!wav)
                    fe_control(FE_MIC);
                show_status(0, decode_ms, waiting);
            }
            // -> preview every 12 frames but skipped when 120 ms or more is still queued so we catch up
            else if (phrase_length() % PREVIEW == 0 && waiting < PREVIEW) {
                started = now();
                phrase_preview(text);
                decode_ms = 1000 * (now() - started);
                if (*text) {
                    emit(id, t, 0, text, previous);
                    shown = t;
                }
                strcpy(previous, text);
                show_status(1, decode_ms, waiting);
            }
        }
        // -> after clear_after seconds of silence the next phrase starts on an empty screen and the log keeps everything
        if (clear_after > 0 && !in_phrase && *captions && t - shown >= clear_after * 100) {
            captions[0] = 0;
            display_show("", "", 0);
        }
        // -> the button ends the session
        if (!wav && fe_button())
            stopping = 1;
    }
    if (in_phrase) { // stopped mid phrase so give it a final anyway
        phrase_decode(text);
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
