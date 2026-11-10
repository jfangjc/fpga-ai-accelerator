#include "caption.h"
#include <assert.h>
int main(void) {
    static struct caption_model c;
    char caption[69632],tail[69632],segments[140000];
#define UPDATE(p,s,t,b,event) assert(!caption_update(&c,p,s,t,b,event,caption,sizeof(caption),tail,sizeof(tail),segments,sizeof(segments)))
    UPDATE(0,1,"old preview","old preview tail",0);
    UPDATE(0,2,"","",1);
    UPDATE(1,1,"live","live tail",0);
    assert(!strcmp(caption,"old preview live"));
    assert(strstr(segments,"Finalising"));
    /* A shorter following phrase can finish first. */
    UPDATE(1,3,"new final","",0);
    assert(!strcmp(caption,"old preview new final"));
    assert(c.base==0);
    UPDATE(0,3,"corrected final","",0);
    assert(!strcmp(caption,"corrected final new final"));
    assert(c.base==2 && !strcmp(segments,"[]"));
    UPDATE(2,1,"next","next tentative",0);
    assert(!strcmp(caption,"corrected final new final next"));
    UPDATE(1,1,"","",1); /* stale lifecycle must not erase current text */
    assert(!strcmp(caption,"corrected final new final next"));
    puts("PASS late final replacement, phrase order, snapshot and lifecycle isolation");
}
