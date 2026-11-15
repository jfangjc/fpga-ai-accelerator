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
// Decode frames from `from` to the end
void phrase_decode(unsigned from, char text[TEXT]);

#endif
