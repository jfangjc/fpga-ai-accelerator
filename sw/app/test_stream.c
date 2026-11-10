#include "stream.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
int main(void) {
    char a[128], b[128];
    uint8_t ids[] = {0,1,1,28,1,0,2,2,28};
    cap_ctc(ids, sizeof(ids), a, sizeof(a));
    assert(!strcmp(a,"aa b"));
    struct cap_stitch st = {0};
    assert(!cap_stitch(&st, ids, 5, 0, 2, a,b,sizeof(a)));
    assert(!strcmp(a,"a") && !strcmp(b,"aa"));
    assert(!cap_stitch(&st, ids+2, 7, 2, 0, a,b,sizeof(a)));
    assert(!strcmp(a,"aa b") && st.emitted==9);
    unsigned ends[16];
    assert(cap_word_ends(st.ids,st.frames,st.count,ends)==2);
    assert(ends[0]==4 && ends[1]==7);
    /* Repeated characters extend the acoustic end; blanks do not. */
    uint8_t repeated[]={1,1,28,0,0,2,28};
    unsigned frames[]={10,11,12,13,14,20,21};
    assert(cap_word_ends(repeated,frames,7,ends)==2);
    assert(ends[0]==11 && ends[1]==20);
    struct cap_vad v = {0};
    assert(cap_vad_step(&v,1,6400,6560)==1);
    assert(v.begin==3200 && v.onset==6560);
    for (unsigned i=0;i<69;i++) assert(!cap_vad_step(&v,0,6560+160*i,6720+160*i));
    assert(cap_vad_step(&v,0,17600,17760)==-1);
    assert(cap_vad_step(&v,1,17760,17920)==1 && v.begin==17760);
    puts("PASS CTC, cross-window stitching, VAD silence and phrase boundary");
}
