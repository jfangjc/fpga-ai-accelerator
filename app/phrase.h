// Phrases of speech in front-end frames, and their decode with a runner
#ifndef PHRASE_H
#define PHRASE_H
#include "runner.h"

#define PHRASE 3000           // longest phrase of 30 s
#define TEXT (PHRASE / 2 + 1) // a character per output frame and the terminator

enum { PHRASE_SILENT, PHRASE_SPEECH, PHRASE_END };

// Read running_prior.json, -1 on error
int phrase_prior(const char* path);
// Add a frame with its power scaled by gain, PHRASE_END when it completes a phrase
int phrase_push(const uint64_t power[FE_BANDS], double gain);
unsigned phrase_length(void);
// dB of the last frame over the noise floor
double phrase_level(void);
// Decode the phrase so far, returning how many leading characters later decodes of it keep, -1 on a runner error
long phrase_decode(const runner* model, char text[TEXT]);

#endif
