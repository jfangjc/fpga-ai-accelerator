// Live captions from the microphone with caption hybrid|cpu MODEL running_prior.json
// live.c finds the phrases, and each is printed here as a preview until it is final
#define _GNU_SOURCE
#include "live.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define WIDTH 79 // characters on a line of an 80-column terminal

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
static void show(long t, const char* text, size_t kept, int final) {
    static size_t printed;
    static int drawn;
    (void)t;
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
    return live_run(model, argv[2], argv[3], &(live_events){.caption = show});
}
