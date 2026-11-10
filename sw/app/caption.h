#ifndef CAP_CAPTION_H
#define CAP_CAPTION_H
#include <stdio.h>
#include <string.h>
/* Outstanding phrases are bounded by the completion ring. Completed leading
 * phrases compact into a checked prefix; no storage grows with event count. */
struct caption_model {
    char prefix[65536];
    unsigned base;
    struct { char text[4096], tentative[4096]; int state; } part[32];
};
static int caption_append(char *dst,size_t cap,const char *src) {
    size_t n=strlen(dst),m=strlen(src);
    if(n+m+2>cap) return -1;
    if(n && m) dst[n++]=' ';
    memcpy(dst+n,src,m+1); return 0;
}
/* state: 1 Speaking, 2 Finalising (tentative-committed), 3 Committed. */
static int caption_update(struct caption_model *c,unsigned phrase,int state,
                          const char *text,const char *tentative,int event_only,
                          char *caption,size_t capacity,char *tail,size_t tailcap,
                          char *segments,size_t segcap) {
    if(phrase>=c->base) {
        if(phrase-c->base>=32) return -1;
        unsigned k=phrase%32;
        if(!event_only) {
            if(c->part[k].state==3) return -1;
            strcpy(c->part[k].text,text); strcpy(c->part[k].tentative,tentative);
            /* Capture can announce Finalising before the last preview retires. */
            if(state==3 || c->part[k].state!=2) c->part[k].state=state;
        } else if(state==2 && c->part[k].state!=3) {
            c->part[k].state=2; c->part[k].tentative[0]=0;
        }
    }
    while(c->part[c->base%32].state==3) {
        unsigned k=c->base%32;
        if(caption_append(c->prefix,sizeof(c->prefix),c->part[k].text)) return -1;
        memset(&c->part[k],0,sizeof(c->part[k])); c->base++;
    }
    strcpy(caption,c->prefix); strcpy(tail,c->prefix);
    size_t used=0;
    segments[used++]='[';
    for(unsigned i=0;i<32;i++) {
        unsigned p=c->base+i,k=p%32;
        if(!c->part[k].state) continue;
        if(caption_append(caption,capacity,c->part[k].text) ||
           caption_append(tail,tailcap,c->part[k].state==1?c->part[k].tentative:c->part[k].text)) return -1;
        int n=snprintf(segments+used,segcap-used,
            "%s{\"phrase\":%u,\"state\":\"%s\",\"text\":\"%s\",\"tentative\":\"%s\"}",
            used>1?",":"",p,c->part[k].state==3?"Committed":c->part[k].state==2?"Finalising":"Speaking",
            c->part[k].text,c->part[k].state==1?c->part[k].tentative:"");
        if(n<0 || (size_t)n>=segcap-used) return -1;
        used+=(size_t)n;
    }
    if(used+2>segcap) return -1;
    segments[used++]=']'; segments[used]=0; return 0;
}
#endif
