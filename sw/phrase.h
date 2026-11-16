// phrase features and the ctc decoder
#ifndef PHRASE_H
#define PHRASE_H
#include "kv260.h"

#define PHRASE 3000              // longest phrase in input frames -> 30 s
#define TEXT (PHRASE / 2 + 1)    // one character per output frame at most plus the terminator

// setup -> called once at startup and returns -1 if running_prior.json can't be read
int phrase_prior(const char* path);
// phrase -> called by the main loop when speech starts and then once per 10 ms frame
void phrase_begin(void);
void phrase_add(const float z[FE_BANDS]);
unsigned phrase_length(void);
// final -> the whole phrase goes through the engine
void phrase_decode(char text[TEXT]);
// preview -> only the last 6.4 s goes through the engine and the text before it is what earlier previews decoded
void phrase_preview(char text[TEXT]);

#endif
