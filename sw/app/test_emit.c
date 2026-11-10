/* Exercise the actual emitter with a deliberately unfinished earlier final. */
#define main sttcap_entry
#include "main.c"
#undef main
#include <assert.h>
int main(void) {
    event_out=tmpfile(); assert(event_out);
    epoch=now(); clock_origin=epoch; clock_period=1.0/16000;
    result_count=4; dispatch_done=1;
    for(unsigned i=0;i<4;i++) {
        struct completion *c=&results[i];
        c->job=(struct job){.phrase=i/2,.final=i%2,.real=80,.n=80,
            .sample=(i+1)*16000,.anchor=(i/2)*32000,.begin=(i/2)*32000};
        c->ready=i!=1;
        c->result=(struct cap_result){.stride=40,.valid=40,.engine="test"};
        memset(c->logits,-10,29*40);
        memset(c->logits+(i+1)*40,10,40);
    }
    pthread_t emitter;
    assert(!pthread_create(&emitter,NULL,emit_results,NULL));
    pthread_mutex_lock(&rlock);
    while(!results[3].emitted) pthread_cond_wait(&rspace,&rlock);
    results[1].ready=1;
    pthread_cond_broadcast(&rready); pthread_mutex_unlock(&rlock);
    pthread_join(emitter,NULL);
    assert(!failed && result_count==0);
    rewind(event_out);
    char line[32768];
    unsigned phrases[]={0,0,1,1,1,0},i=0;
    const char *states[]={"Speaking","Finalising","Speaking","Finalising","Committed","Committed"};
    while(fgets(line,sizeof(line),event_out)) {
        assert(i<6);
        const char *p=strstr(line,"\"phrase\":"); assert(p);
        assert(strtoul(p+9,NULL,10)==phrases[i]);
        assert(strstr(line,states[i]));
        if(i==2) assert(strstr(line,"\"preview\":\"c\""));
        if(i==4) assert(strstr(line,"\"final\":\"d\""));
        if(i==5) assert(strstr(line,"\"final\":\"b\""));
        i++;
    }
    assert(i==6);
    fclose(event_out);
    puts("PASS emitter bypasses pending final and preserves independent phrase stitching");
}
