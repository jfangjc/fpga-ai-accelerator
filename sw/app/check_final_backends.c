/* Board-only exact comparison of saved final inputs/logits with PL.
 * Caller holds board.lock. This never changes the frontend or decoder. */
#include "backend.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char **argv) {
    if(argc!=4) return 2;
    char path[4096]; unsigned phrase,t,n,stride,valid;
    char engine[64]; float scale;
    FILE *f=fopen(argv[2],"rb");
    if(!f || fseek(f,32,SEEK_SET) || fread(&scale,4,1,f)!=1) return 1;
    fclose(f);
    struct cap_backend pl;
    if(backend_pl(&pl,argv[3],scale)) return 1;
    snprintf(path,sizeof(path),"%s/finals.tsv",argv[1]);
    FILE *manifest=fopen(path,"r"); if(!manifest) return 1;
    unsigned count=0; size_t bytes=0;
    while(fscanf(manifest,"%u%u%u%u%u%63s",&phrase,&t,&n,&stride,&valid,engine)==6) {
        if(t>1600 || stride>800) return 1;
        int8_t x[64*1600],expected[29*800];
        snprintf(path,sizeof(path),"%s/final-%u.i8",argv[1],phrase);
        f=fopen(path,"rb"); if(!f || fread(x,64,t,f)!=t) return 1; fclose(f);
        snprintf(path,sizeof(path),"%s/final-%u.logits",argv[1],phrase);
        f=fopen(path,"rb"); if(!f || fread(expected,29,stride,f)!=stride) return 1; fclose(f);
        struct cap_result r;
        if(pl.submit(&pl,x,t,n) || pl.poll(&pl,&r,-1)!=1 ||
           r.stride!=stride || r.valid!=valid || memcmp(r.logits,expected,(size_t)29*stride)) {
            fprintf(stderr,"FAIL phrase %u\n",phrase); return 1;
        }
        bytes+=(size_t)29*stride; count++;
        printf("PASS phrase=%u input_frames=%u logit_bytes=%u saved=%s reference=%s\n",
               phrase,t,29*stride,engine,r.engine);
    }
    fclose(manifest); pl.destroy(&pl);
    printf("PASS %u finals, %zu byte-identical logit bytes\n",count,bytes);
    return count?0:1;
}
