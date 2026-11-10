#ifndef CAP_STREAM_H
#define CAP_STREAM_H
#include <stddef.h>
#include <stdint.h>
enum cap_state { CAP_LISTENING, CAP_SPEAKING, CAP_FINALISING, CAP_COMMITTED };
struct cap_vad {
    enum cap_state state;
    size_t begin, onset, boundary;
    unsigned silent;
};
/* Returns +1 at onset, -1 at finish, zero otherwise. pos/end absolute samples. */
int cap_vad_step(struct cap_vad *, int active, size_t pos, size_t end);
void cap_ctc(const uint8_t *, size_t, char *, size_t);
struct cap_stitch { uint8_t ids[2048]; unsigned frames[2048]; unsigned count, emitted, lost; };
/* Last-character output frame for each whitespace-separated CTC word. */
unsigned cap_word_ends(const uint8_t *, const unsigned *, unsigned, unsigned *);
int cap_stitch(struct cap_stitch *, const uint8_t *, unsigned count,
               unsigned start, unsigned hold, char *, char *, size_t);
#endif
