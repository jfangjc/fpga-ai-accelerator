// Phrase features and decode
#ifndef PHRASE_H
#define PHRASE_H
#include "kv260.h"

#define PHRASE 3000 // longest phrase: 30 s
#define TEXT (PHRASE / 2 + 1)

// Read running_prior.json; -1 on error
int phrase_prior(const char* path);
void phrase_begin(void);
void phrase_add(const float z[FE_BANDS]);
unsigned phrase_length(void);
// The whole phrase
void phrase_decode(char text[TEXT]);
// A preview: only the last 6.4 s goes through the engine, and the text before it is what earlier previews decoded
void phrase_preview(char text[TEXT]);

#endif
