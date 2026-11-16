// When a preview's words count as settled
#ifndef SETTLE_H
#define SETTLE_H
#include <stddef.h>

#define SETTLE_MAX 8
#define SETTLE_TEXT 1501 // TEXT in phrase.h

// Forget the phrase so far; call when a new phrase starts
void settle_reset(void);
// One preview of the current phrase. A word settles once the last `agree` previews (2 to SETTLE_MAX) share it, and
// stays settled for the rest of the phrase: later previews only change the words after it. Words are matched by
// position past the settled ones, so one word heard differently later does not stop the rest settling. Writes the
// text to show and returns how many of its leading characters are settled
size_t settle_preview(const char* text, int agree, char shown[SETTLE_TEXT]);

#endif
