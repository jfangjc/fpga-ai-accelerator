// Live captions on the monitor: screen hybrid|cpu MODEL running_prior.json [--no-status] [--clear-after SEC]
//                               [--theme night|butter]
// live.c finds and decodes the phrases as for caption, and each caption, status and frame comes here to be drawn
#define _GNU_SOURCE
#include "display.h"
#include "live.h"
#include "phrase.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int status = 1, theme = DISPLAY_BUTTER;
static double clear_after = 5; // seconds of silence before the screen is cleared; 0 never
static char captions[4096];    // finals before the live phrase, for the screen
static const char* label;      // the runner, for the status strip
static long caption_at;        // the frame of the last caption

// Status strip: the speaking dot, then the last decode's time on the engine and the caption delay
static void show_status(int speaking, double decode_ms, long waiting) {
    char line[96];
    if (!status)
        return;
    if (decode_ms > 0)
        snprintf(line, sizeof line, "%s  decode %.0f ms  delay %.0f ms", label, decode_ms, decode_ms + 10.0 * waiting);
    else
        snprintf(line, sizeof line, "%s", label);
    display_status(speaking, line);
}

// A preview's words shared with the previous preview are stable, the rest tentative
static void emit(long t, const char* text, size_t kept, int final) {
    static char previous[TEXT];
    size_t stable = 0;
    (void)kept;
    for (size_t i = 0; !final && text[i] && text[i] == previous[i]; i++)
        if (text[i + 1] == ' ' || (!text[i + 1] && !previous[i + 1]))
            stable = i + 1;
    // The screen: earlier finals dim, then this phrase large, its stable words settled and the rest tentative. A
    // final stays on the live line until the next phrase moves it up
    display_show(captions, text, final ? strlen(text) : stable);
    if (final && *text) {
        size_t n = strlen(captions), m = strlen(text);
        if (n + m + 2 > sizeof captions) { // keep the newest half
            memmove(captions, captions + n / 2, n - n / 2 + 1);
            n = strlen(captions);
        }
        snprintf(captions + n, sizeof captions - n, "%s%s", n ? " " : "", text);
    }
    caption_at = t;
    strcpy(previous, final ? "" : text);
}

static void frame(long t, int speaking) {
    static double loudest;
    // The meter: the loudest of every 50 ms, 4 dB a bar; two bars is about the speech threshold
    loudest = fmax(loudest, phrase_level());
    if (t % 5 == 4) {
        display_level(loudest / 28);
        loudest = 0;
    }
    // After clear_after seconds of silence the next phrase starts on an empty screen
    if (clear_after > 0 && !speaking && *captions && t - caption_at >= clear_after * 100) {
        captions[0] = 0;
        display_show("", "", 0);
    }
}

int main(int argc, char** argv) {
    for (int i = 4; i < argc; i++) {
        if (!strcmp(argv[i], "--no-status"))
            status = 0;
        else if (!strcmp(argv[i], "--clear-after") && i + 1 < argc)
            clear_after = atof(argv[++i]);
        else if (!strcmp(argv[i], "--theme") && i + 1 < argc && !strcmp(argv[i + 1], "night")) {
            theme = DISPLAY_NIGHT;
            i++;
        }
        else if (!strcmp(argv[i], "--theme") && i + 1 < argc && !strcmp(argv[i + 1], "butter"))
            i++;
        else
            argc = 0;
    }
    const runner* model = argc >= 4 ? runner_find(argv[1]) : NULL;
    if (!model) {
        fprintf(stderr, "usage: screen hybrid|cpu MODEL running_prior.json [--no-status] [--clear-after SEC] "
                        "[--theme night|butter]\n");
        return 2;
    }
    if (display_open(status, theme)) {
        fprintf(stderr, "screen: no framebuffer; captions are not drawn\n");
        return 1;
    }
    label = model == &hybrid_runner ? "fpga engine" : "cpu int8";
    show_status(0, 0, 0);
    return live_run(model, argv[2], argv[3], &(live_events){.caption = emit, .status = show_status, .frame = frame});
}
