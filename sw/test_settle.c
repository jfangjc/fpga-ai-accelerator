// The settle rule on recorded previews: make -C sw test
#include "settle.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static size_t feed(const char* text, int agree, int windowed, char* shown) {
    (void)windowed;
    return settle_preview(text, agree, shown);
}

int main(void) {
    char shown[SETTLE_TEXT];
    // Phrase 55 from the board: with 3 agreeing previews nothing settles until "it process" has been seen 3 times,
    // and once settled it survives the later "ir prurcess" and "it prucess"
    settle_reset();
    assert(feed("it process is worse", 3, 0, shown) == 0 && !strcmp(shown, "it process is worse"));
    assert(feed("it process es worse", 3, 0, shown) == 0);
    size_t n = feed("it process es worse", 3, 0, shown);
    assert(n == strlen("it process") && !strcmp(shown, "it process es worse"));
    // "ir prurcess" no longer matches the settled words, but the words after them are still matched by position: the
    // three previews agree on "es worse", which settles behind "it process"
    n = feed("ir prurcess es worse", 3, 0, shown);
    assert(n == strlen("it process es worse") && !strcmp(shown, "it process es worse"));
    n = feed("it prucess is worse", 3, 0, shown);
    assert(n == strlen("it process es worse") && !strcmp(shown, "it process es worse"));
    // Three identical previews settle everything, including the last word
    settle_reset();
    feed("so as you can", 3, 0, shown);
    feed("so as you can", 3, 0, shown);
    assert(feed("so as you can", 3, 0, shown) == strlen("so as you can"));
    // Growing speech: settled words extend as agreement reaches further
    assert(feed("so as you can see", 3, 0, shown) == strlen("so as you can"));
    feed("so as you can see right", 3, 0, shown);
    assert(feed("so as you can see right now", 3, 0, shown) == strlen("so as you can see"));
    // A new phrase starts empty
    settle_reset();
    assert(feed("fpga", 3, 0, shown) == 0 && !strcmp(shown, "fpga"));
    // Two agreeing previews are the minimum
    settle_reset();
    feed("one two three", 1, 0, shown);
    assert(feed("one two four", 1, 0, shown) == strlen("one two"));
    // Phrase 193 from the board: an unsure word early on ("d how" against "how") no longer stops everything after it
    // settling in a long phrase
    settle_reset();
    feed("d how", 3, 0, shown);
    feed("d how", 3, 0, shown);
    assert(feed("d how d you", 3, 0, shown) == strlen("d how"));
    feed("how d you do it oh my gosh", 3, 0, shown);
    feed("how d you do it oh my gosh", 3, 0, shown);
    n = feed("how d you do it oh my gosh three", 3, 0, shown);
    assert(n == strlen("d how you do it oh my gosh") && !strcmp(shown, "d how you do it oh my gosh three"));
    // Settled text keeps growing through a long phrase
    char text[SETTLE_TEXT] = "", grown[SETTLE_TEXT];
    settle_reset();
    for (int w = 0; w < 60; w++) {
        strcat(text, w ? " word" : "word");
        for (int k = 0; k < 3; k++)
            n = feed(text, 3, 0, shown);
    }
    strcpy(grown, text);
    assert(n == strlen(grown));
    puts("PASS settle: phrase 55 keeps its settled words, growth by appending, reset, minimum 2, unsure word, long phrase");
    return 0;
}
