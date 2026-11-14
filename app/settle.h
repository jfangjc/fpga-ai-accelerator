// deciding when a preview's words are settled
#ifndef SETTLE_H
#define SETTLE_H
#include <stddef.h>

#define SETTLE_MAX 8       // most previews that can be asked to agree
#define SETTLE_TEXT 1501   // same as TEXT in phrase.h

// phrase start -> called by the main loop when a new phrase begins
void settle_reset(void);
// preview -> called by emit for every preview
// a word settles once the last agree previews (2 to SETTLE_MAX) share it and then stays settled for the whole phrase
// writes the text to show and returns how many of its leading characters are settled
size_t settle_preview(const char* text, int agree, char shown[SETTLE_TEXT]);

#endif
