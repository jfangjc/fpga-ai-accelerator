/*
    deciding when a preview's words are settled
    -> the last few previews are kept and a word settles once the last agree previews all have it in the same place
    -> settled words never change for the rest of the phrase so the screen stops flickering on words already heard
    -> words get matched by position after the settled ones so one word heard differently later doesn't stop the rest
*/
#include "settle.h"
#include <stdio.h>
#include <string.h>

static char recent[SETTLE_MAX][SETTLE_TEXT], settled[SETTLE_TEXT];   // newest preview first and the words locked in so far
static int seen;                                                       // previews this phrase -> capped at agree

// forgets the phrase so far
void settle_reset(void) {
    seen = 0;
    settled[0] = 0;
}

// the longest whole word prefix that n texts share without trailing spaces
static size_t agreed(const char* const* s, int n) {
    size_t best = 0;
    for (size_t i = 0;; i++) {
        // -> stop at the first character the texts disagree on
        char c = s[0][i];
        for (int k = 1; k < n; k++)
            if (s[k][i] != c)
                goto done;
        if (!c) {
            best = i;
            break;
        }
        // -> only count up to here if a word ends here in every text so half a word never settles
        int edge = 1;
        for (int k = 0; k < n; k++)
            if (s[k][i + 1] && s[k][i + 1] != ' ')
                edge = 0;
        if (edge)
            best = i + 1;
    }
done:
    while (best && s[0][best - 1] == ' ')
        best--;
    return best;
}

// skips past the first words words of s and the spaces after them
static const char* skip_words(const char* s, size_t words) {
    while (*s == ' ')
        s++;
    for (; words && *s; words--) {
        while (*s && *s != ' ')
            s++;
        while (*s == ' ')
            s++;
    }
    return s;
}

// counts the words in s
static size_t count_words(const char* s) {
    size_t n = 0;
    for (const char* p = skip_words(s, 0); *p; p = skip_words(p, 1))
        n++;
    return n;
}

// takes one preview and writes what to show -> returns how many leading characters of it are settled
size_t settle_preview(const char* text, int agree, char shown[SETTLE_TEXT]) {
    agree = agree < 2 ? 2 : agree > SETTLE_MAX ? SETTLE_MAX : agree;
    // -> shift the older previews along and keep this one at the front
    memmove(recent[1], recent[0], sizeof recent[0] * (SETTLE_MAX - 1));
    strncpy(recent[0], text, SETTLE_TEXT - 1);
    recent[0][SETTLE_TEXT - 1] = 0;
    if (seen < agree)
        seen++;
    size_t have = strlen(settled), words = count_words(settled);
    // -> nothing can settle until there are agree previews to compare
    if (seen == agree) {
        // -> look past the settled words by count so a settled word a preview now hears differently doesn't hold back the ones after it
        const char* after[SETTLE_MAX];
        for (int k = 0; k < agree; k++)
            after[k] = skip_words(recent[k], words);
        size_t n = agreed(after, agree);
        // -> settled words only ever grow by appending
        if (n && have + 1 + n < SETTLE_TEXT) {
            snprintf(settled + have, SETTLE_TEXT - have, "%s%.*s", have ? " " : "", (int)n, after[0]);
            have = strlen(settled);
        }
    }
    // -> show the settled words then whatever this preview has after the same number of words
    const char* tail = skip_words(text, count_words(settled));
    snprintf(shown, SETTLE_TEXT, "%s%s%s", settled, have && *tail ? " " : "", tail);
    return have;
}
