#include "stream.h"
#include "policy.h"
#include <string.h>
int cap_vad_step(struct cap_vad *s, int active, size_t pos, size_t end) {
    int onset = 0;
    if (s->state != CAP_SPEAKING && active) {
        size_t lead = (size_t)(POLICY_VAD_LEAD * POLICY_SAMPLE_RATE);
        s->begin = pos > lead ? pos-lead : 0;
        if (s->begin < s->boundary) s->begin = s->boundary;
        s->onset = end;
        s->silent = 0;
        s->state = CAP_SPEAKING;
        onset = 1;
    }
    if (s->state == CAP_SPEAKING) {
        s->silent = active ? 0 : s->silent+1;
        if (s->silent >= (unsigned)(POLICY_VAD_SILENCE*100 + .5) ||
            end-s->begin >= (size_t)(POLICY_PHRASE_CAP*POLICY_SAMPLE_RATE)) {
            s->boundary = end;
            s->state = CAP_FINALISING;
            return -1;
        }
    }
    return onset;
}
void cap_ctc(const uint8_t *ids, size_t n, char *out, size_t capacity) {
    static const char vocab[] = " abcdefghijklmnopqrstuvwxyz'";
    unsigned last = 255;
    size_t k = 0;
    for (size_t i=0; i<n; ++i) {
        unsigned id = ids[i];
        if (id < 28 && id != last && (id || k) && k+1 < capacity)
            out[k++] = vocab[id];
        last = id;
    }
    while (k && out[k-1]==' ') --k;
    if (capacity) out[k] = 0;
}
unsigned cap_word_ends(const uint8_t *ids,const unsigned *frames,unsigned n,unsigned *ends) {
    unsigned words=0,last=0,prev=255; int word=0;
    for(unsigned i=0;i<n;i++) {
        if(ids[i]>0 && ids[i]<28) { word=1; last=frames[i]; }
        else if(ids[i]==0 && prev!=0 && word) { ends[words++]=last; word=0; }
        prev=ids[i];
    }
    if(word) ends[words++]=last;
    return words;
}
int cap_stitch(struct cap_stitch *s, const uint8_t *ids, unsigned count,
               unsigned start, unsigned hold, char *committed, char *tentative, size_t cap) {
    unsigned stop = count > hold ? count-hold : 0;
    unsigned first = s->emitted > start ? s->emitted-start : 0;
    unsigned tail = stop > first ? stop : first;
    if (start > s->emitted) s->lost += start-s->emitted;
    if (first < stop) {
        if (s->count+stop-first > sizeof(s->ids)) return -1;
        memcpy(s->ids+s->count, ids+first, stop-first);
        for(unsigned i=first;i<stop;i++) s->frames[s->count+i-first]=start+i;
        s->count += stop-first;
        s->emitted = start+stop;
    }
    cap_ctc(s->ids, s->count, committed, cap);
    uint8_t all[4096];
    unsigned extra = tail < count ? count-tail : 0;
    if (s->count+extra > sizeof(all)) return -1;
    memcpy(all, s->ids, s->count);
    memcpy(all+s->count, ids+(tail < count ? tail : count), extra);
    cap_ctc(all, s->count+extra, tentative, cap);
    return 0;
}
