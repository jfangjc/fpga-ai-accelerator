#define _GNU_SOURCE
#include "backend.h"
#include "stream.h"
#include "display.h"
#include "policy.h"
#include "clock.h"
#include "caption.h"
#include "../frontend_c/frontend.h"
#include "../qnrt/wav.h"
#ifndef CPU_ONLY
#include "../fedrv/stream.h"
#include "../fedrv/features.h"
#endif
#include <arpa/inet.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#ifdef __GLIBC__
#include <malloc.h>
#endif
#include <string.h>
#include <poll.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

/* Bounded FIFO: adjacent previews for the same phrase replace one another.
 * Finals are never replaced. The producer blocks when eight finals accumulate. */
struct job { int final; unsigned phrase,t,n,start,real; size_t sample,anchor; int8_t *x; size_t begin; double queued; };
static struct job queue[8];
static unsigned head, count, merged;
static int active_final;
static unsigned active_frames;
static int done, failed, bulk;
static volatile sig_atomic_t stopping;
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ready=PTHREAD_COND_INITIALIZER, space=PTHREAD_COND_INITIALIZER;
static struct cap_backend backend;
static struct cap_backend final_backend;
static const char *final_mode="auto";
static unsigned final_threads=3;
static int separate_finals;
static const char *final_blob;
static int final_init_error;
/* Ready results may bypass pending finals. Inference never holds this mutex
 * while running either engine or writing output. Slots own copied logits. */
#define RESULTS 32u
struct completion {
    struct job job;
    struct cap_result result;
    int8_t logits[29*800];
    double started, finished;
    unsigned depth, skips;
    int ready, error, emitted, announced;
};
static struct completion results[RESULTS];
static unsigned result_head,result_count;
static unsigned final_queue[8],final_head,final_count;
static int dispatch_done;
static pthread_mutex_t rlock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t rready=PTHREAD_COND_INITIALIZER, rspace=PTHREAD_COND_INITIALIZER;
/* Capture owns the float workspace; quantised jobs own one pool slot until
 * inference (and any requested dump) finishes. No large per-job allocation. */
#define INPUTS 48u
static int8_t inputs[INPUTS][64*1600];
static unsigned char input_busy[INPUTS];
static float feature_work[64*1600];
/* Whole-phrase front-end scratch (16 s maximum) and the packed copy used by
 * utterance normalisation. Static and pre-faulted, so finals of new maximum
 * length or allocator fragmentation cannot extend the heap mid-session. */
static _Alignas(64) unsigned char fe_scratch[1536*1024];
static float tight_work[64*1600];
static int phrase_features(const float *pcm,size_t n,qnf_state *st,float *out) {
    if(qnf_features_scratch(n)>sizeof(fe_scratch)) return -1;
    return qnf_features_ws(pcm,n,0,st,out,fe_scratch);
}
static float warm_pcm[255840];
static pthread_mutex_t ilock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ispace=PTHREAD_COND_INITIALIZER;
static int8_t *input_get(void) {
    pthread_mutex_lock(&ilock);
    for(;;) {
        for(unsigned i=0;i<INPUTS;i++) if(!input_busy[i]) {
            input_busy[i]=1; pthread_mutex_unlock(&ilock); return inputs[i];
        }
        pthread_cond_wait(&ispace,&ilock);
    }
}
static void input_put(int8_t *p) {
    pthread_mutex_lock(&ilock);
    input_busy[((uintptr_t)p-(uintptr_t)inputs)/(64*1600)]=0;
    pthread_cond_signal(&ispace); pthread_mutex_unlock(&ilock);
}
static qnf_state prior;
static float *audio;
static size_t samples;
static double epoch;
static _Atomic double capture_epoch;
static pthread_mutex_t clock_lock=PTHREAD_MUTEX_INITIALIZER;
static double clock_origin, clock_period=1.0/16000;
static double initial_sample_rate=16000;
static int clock_fitted, clock_calibrated;
static double capture_time(size_t sample,double *period,int *fitted) {
    pthread_mutex_lock(&clock_lock);
    double t=clock_origin+sample*clock_period;
    *period=clock_period; *fitted=clock_fitted;
    pthread_mutex_unlock(&clock_lock);
    return t;
}
static FILE *session;
static int listener=-1;
static int events[2];
static FILE *event_out;
static void state_event(const char *state,unsigned phrase) {
    flockfile(event_out);
    fprintf(event_out,"{\"inference_time\":0,\"phrase\":%u,\"state\":\"%s\",\"event_only\":true,\"preview\":\"\",\"tentative\":\"\",\"final\":\"\",\"compute_ms\":0,\"merged\":0,\"lost_frames\":0,\"bulk\":%s}\n",phrase,state,bulk?"true":"false");
    fflush(event_out);
    funlockfile(event_out);
}
/* Stage records travel with log lines and are streamed by the writer. */
struct stage { unsigned phrase; char state; double inference,pop,render,fb,tcp,log,out,ppm; };
static size_t stage_count;
static const char *stages_path;
static FILE *stages_file;
/* File output (JSONL log, stdout copy, PPM evidence) belongs to a nice-10,
 * lowest best-effort I/O priority writer thread. The output thread never
 * waits for it: log lines go to a bounded queue (overflow drops and counts),
 * PPM snapshots to one latest-wins slot. Nothing is fsynced. */
#define LOG_SLOTS 64u
#define LOG_BYTES 262144u
static struct { char line[LOG_BYTES]; size_t id; struct stage stage; } logpool[LOG_SLOTS];
static unsigned logq[LOG_SLOTS], free_slots[LOG_SLOTS], free_count;
static unsigned log_head, log_count, log_dropped, ppm_coalesced;
static int writer_stop, ppm_pending;
static size_t ppm_stage;
static uint8_t ppm_image[CAP_PIXEL_BYTES];
static const char *ppm_path="caption.ppm";
static double ppm_every=1.0;
static int realtime;
static pthread_mutex_t wlock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t wready=PTHREAD_COND_INITIALIZER;
static void log_line(const char *line,size_t id,const struct stage *stage,int snapshot) {
    pthread_mutex_lock(&wlock);
    if(!free_count || strlen(line)>=LOG_BYTES) log_dropped++;
    else {
        unsigned slot=free_slots[--free_count];
        unsigned k=(log_head+log_count++)%LOG_SLOTS;
        logq[k]=slot;
        strcpy(logpool[slot].line,line); logpool[slot].id=id; logpool[slot].stage=*stage;
    }
    if(snapshot) {
        if(ppm_pending) ppm_coalesced++;
        memcpy(ppm_image,cap_pixels(),CAP_PIXEL_BYTES); ppm_pending=1; ppm_stage=id;
    }
    pthread_cond_signal(&wready); pthread_mutex_unlock(&wlock);
}
static void ppm_snapshot(size_t stage) {
    pthread_mutex_lock(&wlock);
    if(ppm_pending) ppm_coalesced++;
    memcpy(ppm_image,cap_pixels(),CAP_PIXEL_BYTES); ppm_pending=1; ppm_stage=stage;
    pthread_cond_signal(&wready); pthread_mutex_unlock(&wlock);
}
static void set_realtime(int priority) {
    struct sched_param sp={.sched_priority=priority};
    if(realtime && pthread_setschedparam(pthread_self(),SCHED_FIFO,&sp))
        fprintf(stderr,"SCHED_FIFO %d unavailable; continuing\n",priority);
}
static int pl_source, mic_source;
/* Microphone VAD: summed linear-mel energy of bands 1..63 (band 0 carries the
 * MEMS microphone's DC offset) against a minimum-statistics noise floor over
 * the previous 5 s, plus an absolute minimum. --mic-gain scales mic band power
 * before the log (exactly a digital gain ahead of the FFT) towards the
 * LibriSpeech level assumed by the running-normalisation prior. */
static double mel_threshold=2e-6, vad_snr_db=8, mic_gain_db=20;
static const char *vad_path, *dump_dir;
/* --dump DIR: each final's exact INT8 input and logits, for host checks
 * against golden_int8 (finals.tsv: phrase T valid stride out_valid backend). */
static int dump_final(unsigned phrase,const int8_t *x,unsigned t,unsigned n,const struct cap_result *r) {
    char path[4096]; FILE *f;
    snprintf(path,sizeof(path),"%s/final-%u.i8",dump_dir,phrase);
    if(!(f=fopen(path,"wb")) || fwrite(x,64,t,f)!=t || fclose(f)) return -1;
    snprintf(path,sizeof(path),"%s/final-%u.logits",dump_dir,phrase);
    if(!(f=fopen(path,"wb")) || fwrite(r->logits,29,r->stride,f)!=r->stride || fclose(f)) return -1;
    snprintf(path,sizeof(path),"%s/finals.tsv",dump_dir);
    if(!(f=fopen(path,"a"))) return -1;
    fprintf(f,"%u\t%u\t%u\t%u\t%u\t%s\n",phrase,t,n,r->stride,r->valid,r->engine?r->engine:"cpu");
    return fclose(f);
}
static void pin_io(void) {
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(0,&set);
    (void)pthread_setaffinity_np(pthread_self(),sizeof(set),&set);
}
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }
static void fault_zero(void *buffer,size_t bytes) {
    volatile unsigned char *p=buffer;
    for(size_t i=0;i<bytes;i+=4096) p[i]=0;
    if(bytes) p[bytes-1]=0;
}
static int warm_features(void) {
    /* Run after persistent capture/backend allocations, in the capture arena,
     * before starting the paced hardware; touches the full static scratch path. */
    return phrase_features(warm_pcm,255840,&prior,feature_work)<0;
}
static void stop(int sig) { (void)sig; stopping=1; }
static void pace(size_t n) {
    while (!bulk && !stopping && now()<epoch+n/16000.0) {
        struct timespec ts={0,1000000}; nanosleep(&ts,NULL);
    }
}
static int push(struct job j) {
    j.queued=now()-epoch;
    pthread_mutex_lock(&lock);
    if (!bulk && count && !j.final) {
        unsigned last=(head+count-1)%8;
        if (!queue[last].final && queue[last].phrase==j.phrase) {
            fprintf(stderr,"merge_event time=%.6f phrase=%u capture=%.6f blocked_final=%d blocked_frames=%u\n",
                now()-epoch,j.phrase,j.sample/16000.0,active_final,active_frames);
            input_put(queue[last].x); queue[last]=j; merged++;
            pthread_mutex_unlock(&lock); return 0;
        }
    }
    while (count==8 && !stopping) pthread_cond_wait(&space,&lock);
    if (stopping) { pthread_mutex_unlock(&lock); input_put(j.x); return -1; }
    queue[(head+count++)%8]=j;
    pthread_cond_signal(&ready); pthread_mutex_unlock(&lock); return 0;
}
static int preview(float *frames, unsigned t, unsigned phrase, size_t sample, size_t anchor, size_t begin) {
    unsigned s=t>POLICY_WINDOW*100 ? t-POLICY_WINDOW*100 : 0;
    s-=s%2;
    unsigned real=t-s,n=real+16,T=(n+15)/16*16;
    if(T>1600) return -1;
    float *f=feature_work; memset(f,0,(size_t)64*T*sizeof(float));
    int8_t *q=input_get();
    if (!f || !q) { input_put(q); return -1; }
    for (unsigned b=0;b<64;b++) {
        for (unsigned j=0;j<real;j++) f[b*T+j]=frames[(s+j)*64+b];
        float z=(float)logf(0x1p-24f);
        float silence=(float)(((double)z-prior.mean[b])/(sqrt(prior.m2[b]/(prior.count-1))+1e-5));
        for (unsigned j=real;j<n;j++) f[b*T+j]=silence;
    }
    qnf_quantize(f,(size_t)64*T,backend.scale,q);
    return push((struct job){0,phrase,T,n,s,real,sample,anchor,q,begin,0});
}
static int final_job(size_t begin, size_t end, unsigned phrase) {
    size_t n=end-begin;
    if(n<=256) return 0;
    unsigned valid=n/160+1,T=(valid+15)/16*16;
    if(T>1600) return -1;
    float *f=feature_work; memset(f,0,(size_t)64*T*sizeof(float)); int8_t *q=input_get();
    if(!f || !q) { input_put(q); return -1; }
    qnf_state st=prior;
    if(phrase_features(audio+begin,n,&st,f)<0) { input_put(q); return -1; }
    qnf_quantize(f,(size_t)64*T,backend.scale,q);
    return push((struct job){1,phrase,T,valid,0,valid,end,end,q,begin,0});
}
#ifndef CPU_ONLY
struct mic_vad { double block_min, mins[10]; unsigned frames, filled; };
static int mic_vad_step(struct mic_vad *m,const uint64_t power[64],double *energy,double *floor) {
    double e=0; for(unsigned b=1;b<64;b++) e+=ldexp((double)power[b],-42);
    if(!m->frames) m->block_min=INFINITY;
    if(e<m->block_min) m->block_min=e;
    if(++m->frames%50==0) {
        memmove(m->mins,m->mins+1,9*sizeof(double)); m->mins[9]=m->block_min;
        if(m->filled<10) m->filled++;
        m->block_min=INFINITY;
    }
    double f=m->block_min; for(unsigned i=10-m->filled;i<10;i++) if(m->mins[i]<f) f=m->mins[i];
    *energy=e; *floor=f;
    /* Ignore the first 0.3 s: the microphone settles after BCLK starts. */
    return m->frames>30 && e>=mel_threshold && e>=f*pow(10,vad_snr_db/10);
}
struct injection { size_t pos; };
struct clock_watch { struct fe *device; _Atomic int stop; };
static void *watch_clock(void *arg) {
    struct clock_watch *w=arg;
    struct cap_clock fit;
    double anchor=atomic_load(&capture_epoch), delay=NAN;
    cap_clock_init(&fit,anchor);
    fit.period=1/initial_sample_rate;
    double before=now();
    unsigned previous=fe_hw_frames(w->device), reported=0;
    while(!atomic_load(&w->stop)) {
        double a=now(); unsigned frames=fe_hw_frames(w->device); double b=now();
        /* Observe hardware edges independently of consumer queues. */
        if(frames==previous+1 && b-before<.002) {
            double edge=(before+b)*.5, sample=319.0+(frames-1)*160.0;
            if(!isfinite(delay) && isfinite(anchor)) delay=edge-anchor-sample/initial_sample_rate;
            if(isfinite(delay)) {
                int fitted=cap_clock_add(&fit,sample,edge-delay);
                if(fitted) {
                    pthread_mutex_lock(&clock_lock);
                    clock_origin=fit.origin; clock_period=fit.period; clock_fitted=1;
                    pthread_mutex_unlock(&clock_lock);
                }
                if(frames/100!=reported) {
                    reported=frames/100;
                    fprintf(stderr,"capture_clock mono=%.9f frames=%u bracket_us=%.3f sample_rate=%.9f ppm=%.3f fitted=%d\n",
                        edge,frames,(b-before)*1e6,1/fit.period,(1/fit.period/16000-1)*1e6,fitted);
                }
            }
        }
        previous=frames; before=a;
        struct timespec pause={0,500000}; nanosleep(&pause,NULL);
    }
    return NULL;
}
static size_t fill(void *ctx,int32_t *dst,size_t max) {
    struct injection *in=ctx;
    size_t n=samples-in->pos; if(n>max) n=max;
    if(stopping) return 0;
    for(size_t i=0;i<n;i++) dst[i]=(int32_t)lrintf(audio[in->pos+i]*131072.0f);
    in->pos+=n; return n;
}
/* PL frames are continuous, not phrase-centred. This path is explicitly
 * experimental until phrase-start reflection/alignment is reconciled. */
static void *capture_pl(void *unused) {
    (void)unused;
    pin_io(); set_realtime(50);
    struct fe device;
    struct fe_stream *stream=calloc(1,sizeof(*stream));
    float *history=calloc(2048*64,sizeof(float));
    float *frames=calloc(1600*64,sizeof(float));
    float *logs=calloc(1600*64,sizeof(float));
    struct injection in={0}; int opened=0,started=0,watching=0;
    struct clock_watch watch={.device=&device}; pthread_t clock_thread;
    struct cap_vad v={0}; unsigned phrase=0,t=0,next=12;
    FILE *vad=vad_path?fopen(vad_path,"w"):NULL;
    /* Large buffer: the capture thread writes this log; avoid per-4 KiB I/O. */
    if(vad) setvbuf(vad,NULL,_IOFBF,1<<20);
    struct fe_norm_state norm={0};
    struct mic_vad mic={0};
    if(!stream || !history || !frames || !logs || warm_features() || fe_open(&device)) { failed=1; goto finish; }
    opened=1;
    struct fe_config cfg={.source=pl_source==1?FE_SRC_INJECT:(enum fe_source)mic_source,
         .paced=!bulk,.irq_frames=1,.gain=16384,.button_irq=1};
    if(fe_reset(&device) || fe_configure(&device,&cfg) ||
       fe_stream_start(stream,&device,pl_source==1?fill:NULL,&in)) { failed=1; goto finish; }
    started=1;
    /* During the first FFT exactly 320 samples have left the input FIFO.
     * Bracket acceptance using the same probe as latency_i1.c. Failure to
     * catch this interval invalidates latency; never fall back to scheduling. */
    if(pl_source==1 && !bulk) {
        uint64_t deadline=plio_now_ns()+100000000ULL;
        while(plio_now_ns()<deadline && !fe_hw_frames(&device)) {
            uint64_t a=plio_now_ns();
            unsigned occupancy=fe_status(&device)>>4;
            uint64_t b=plio_now_ns();
            if(occupancy>=4 && occupancy<1024 && !fe_hw_frames(&device)) {
                double first=((double)a+(double)b)*0.5e-9-(319.5+occupancy)/initial_sample_rate;
                atomic_store(&capture_epoch,first);
                pthread_mutex_lock(&clock_lock); clock_origin=first; pthread_mutex_unlock(&clock_lock);
                break;
            }
        }
    }
    if(pl_source==1 && !bulk && isfinite(atomic_load(&capture_epoch))) {
        if(pthread_create(&clock_thread,NULL,watch_clock,&watch)) { failed=1; goto finish; }
        watching=1;
    }
    while(!stopping) {
        struct fe_frame frame; int rc=fe_stream_next(stream,&frame,100);
        if(rc==-2) continue;
        if(rc<=0) { if(rc<0) failed=1; break; }
        unsigned seq=frame.sequence;
        float *z=history+(seq%2048)*64;
        if(pl_source==2) {
            double g=pow(10,mic_gain_db/10);
            for(unsigned b=0;b<64;b++) z[b]=logf((float)((double)frame.power[b]*0x1p-42*g)+0x1p-24f);
        } else fe_log_frame(frame.power,z);
        double energy=0; for(unsigned b=0;b<64;b++) energy+=ldexp((double)frame.power[b],-42);
        size_t pos=(size_t)seq*160,end=pos+160;
        if(vad && audio && end<=samples) {
            double rms=0; for(size_t k=pos;k<end;k++) rms+=(double)audio[k]*audio[k];
            fprintf(vad,"%u,%.12g,%d\n",seq,energy,rms/160>=POLICY_VAD_RMS*POLICY_VAD_RMS);
        }
        /* Replay has the original PCM: use the specified non-overlapping
         * waveform blocks, without fitting a mel threshold on test data. */
        int active=0;
        if(pl_source==2) {
            double floor; active=mic_vad_step(&mic,frame.power,&energy,&floor);
            if(vad) fprintf(vad,"%u,%.12g,%.12g,%d\n",seq,energy,floor,active);
        }
        else if(audio && end<=samples) {
            double square=0;
            for(size_t k=pos;k<end;k++) square+=(double)audio[k]*audio[k];
            active=square/160>=POLICY_VAD_RMS*POLICY_VAD_RMS;
        }
        int change=cap_vad_step(&v,active,pos,end);
        if(fe_ui_poll(&device,v.state==CAP_SPEAKING)) { stopping=1; break; }
        if(change==1) {
            state_event("Speaking",phrase);
            norm.count=(long)prior.count;
            memcpy(norm.mean,prior.mean,sizeof(norm.mean)); memcpy(norm.m2,prior.m2,sizeof(norm.m2));
            t=0; next=12;
            unsigned first=(unsigned)(v.begin/160);
            for(unsigned k=first;k<seq;k++) {
                memcpy(logs+t*64,history+(k%2048)*64,64*sizeof(float));
                memcpy(frames+t*64,logs+t*64,64*sizeof(float));
                fe_normalize_running(frames+t*64,1,1,&norm); t++;
            }
        }
        if(v.state==CAP_SPEAKING || change==-1) {
            if(t>=1600) { failed=1; break; }
            memcpy(logs+t*64,z,64*sizeof(float)); memcpy(frames+t*64,z,64*sizeof(float));
            fe_normalize_running(frames+t*64,1,1,&norm); t++;
            if(change!=-1 && t>=next) {
                if(end>v.onset && preview(frames,t,phrase,end,v.begin+((v.onset-v.begin)/1920)*1920,v.begin)) { failed=1; break; }
                next+=12;
            }
        }
        if(change==-1) {
            /* Replay finals restart preemphasis at phrase begin and reflect
             * at both ends before whole-phrase utterance normalisation.
             * Continuous PL mel cannot reconstruct either boundary. */
            if(audio) {
                if(final_job(v.begin,end,phrase++)) { failed=1; break; }
                continue;
            }
            unsigned T=(t+15)/16*16;
            if(T>1600) { failed=1; break; }
            float *f=feature_work; memset(f,0,(size_t)64*T*sizeof(float)); int8_t *q=input_get();
            if(!f || !q) { input_put(q); failed=1; break; }
            for(unsigned b=0;b<64;b++) for(unsigned k=0;k<t;k++) f[b*T+k]=logs[k*64+b];
            /* Normalizer expects tightly packed band-major valid frames. */
            float *tight=tight_work;
            for(unsigned b=0;b<64;b++) memcpy(tight+b*t,f+b*T,t*sizeof(float));
            if(fe_normalize_utterance(tight,t)) failed=1;
            for(unsigned b=0;b<64;b++) memcpy(f+b*T,tight+b*t,t*sizeof(float));
            fe_quantize(f,(size_t)64*T,backend.scale,q);
            if(push((struct job){1,phrase++,T,t,0,t,end,end,q,v.begin,0})) break;
        }
    }
    if(audio && v.state==CAP_SPEAKING && !stopping && !failed &&
       final_job(v.begin,samples,phrase)) failed=1;
finish:
    if(watching) { atomic_store(&watch.stop,1); pthread_join(clock_thread,NULL); }
    if(vad) fclose(vad);
    if(started) {
        fe_stream_stop(stream);
        int error=fe_stream_join(stream);
        unsigned long long dropped=(unsigned long long)atomic_load(&stream->dropped);
        fprintf(stderr,"capture_stats frames=%llu irqs=%llu sequence_errors=%llu ring_overruns=%llu tx_underruns=%llu mic_overruns=%u consumer_dropped=%llu status=0x%x error=%d\n",
            (unsigned long long)device.stats.frames,(unsigned long long)device.stats.irqs,
            (unsigned long long)device.stats.sequence_errors,(unsigned long long)device.stats.ring_overruns,
            (unsigned long long)device.stats.tx_underruns,device.stats.mic_overruns,dropped,device.stats.status,error);
        if(error || dropped) failed=1;
    }
    if(opened) { fe_led(&device,0); fe_close(&device); }
    free(stream); free(history); free(frames); free(logs);
    pthread_mutex_lock(&lock); done=1; pthread_cond_signal(&ready); pthread_mutex_unlock(&lock);
    return NULL;
}
#endif
static void *capture(void *unused) {
    (void)unused;
    pin_io(); set_realtime(50);
    struct cap_vad v={0}; unsigned phrase=0,t=0; size_t next=0,consumed=0;
    qnf_stream *front=qnf_stream_new(&prior);
    float *frames=calloc(1600*64,sizeof(float));
    if(!front || !frames || warm_features()) { failed=1; goto finish; }
    for(size_t pos=0;pos<samples && !stopping;pos+=160) {
        size_t end=pos+160<samples?pos+160:samples; pace(end);
        double energy=0;
        for(size_t i=pos;i<end;i++) energy+=(double)audio[i]*audio[i];
        int change=cap_vad_step(&v,energy/(end-pos)>=POLICY_VAD_RMS*POLICY_VAD_RMS,pos,end);
        if(change==1) { state_event("Speaking",phrase); qnf_stream_reset(front,&prior); t=0; consumed=v.begin; next=v.begin+(size_t)(POLICY_HOP*16000); }
        if(v.state==CAP_SPEAKING) {
            int got=qnf_stream_push(front,audio+consumed,end-consumed,frames+t*64,1600-t);
            if(got<0) { failed=1; break; } t+=(unsigned)got; consumed=end;
            if(end>=next) {
                size_t anchor=v.begin+((v.onset-v.begin)/1920)*1920;
                if(next>v.onset && preview(frames,t,phrase,next,anchor,v.begin)) { failed=1; break; }
                next+=(size_t)(POLICY_HOP*16000);
            }
        }
        if(change==-1) {
            if(final_job(v.begin,end,phrase++)) { failed=1; break; }
        }
    }
    if(v.state==CAP_SPEAKING && !stopping && final_job(v.begin,samples,phrase)) failed=1;
finish:
    qnf_stream_free(front); free(frames);
    pthread_mutex_lock(&lock); done=1; pthread_cond_signal(&ready); pthread_mutex_unlock(&lock);
    return NULL;
}
static void *writer(void *unused) {
    (void)unused;
    pin_io();
    pid_t tid=(pid_t)syscall(SYS_gettid);
    (void)setpriority(PRIO_PROCESS,(id_t)tid,10);
    (void)syscall(SYS_ioprio_set,1,tid,(2<<13)|7); /* IOPRIO_WHO_PROCESS, best-effort 7 */
    static uint8_t image[CAP_PIXEL_BYTES];
    unsigned batch[LOG_SLOTS];
    pthread_mutex_lock(&wlock);
    for(;;) {
        while(!log_count && !ppm_pending && !writer_stop) pthread_cond_wait(&wready,&wlock);
        if(!log_count && !ppm_pending) break;
        unsigned n=log_count;
        for(unsigned i=0;i<n;i++) {
            batch[i]=logq[(log_head+i)%LOG_SLOTS];
        }
        log_head=(log_head+n)%LOG_SLOTS; log_count=0;
        int snap=ppm_pending; size_t snap_stage=ppm_stage;
        if(snap) memcpy(image,ppm_image,CAP_PIXEL_BYTES);
        ppm_pending=0;
        pthread_mutex_unlock(&wlock);
        if(session && n) {
            for(unsigned i=0;i<n;i++) fputs(logpool[batch[i]].line,session);
            fflush(session);
            for(unsigned i=0;i<n;i++) logpool[batch[i]].stage.log=now()-epoch;
        }
        for(unsigned i=0;i<n;i++) fputs(logpool[batch[i]].line,stdout);
        if(n) {
            fflush(stdout);
            for(unsigned i=0;i<n;i++) logpool[batch[i]].stage.out=now()-epoch;
        }
        if(snap && cap_ppm(ppm_path,image)==0)
            for(unsigned i=0;i<n;i++) if(logpool[batch[i]].id==snap_stage) logpool[batch[i]].stage.ppm=now()-epoch;
        if(stages_file) {
            for(unsigned i=0;i<n;i++) {
                struct stage *g=&logpool[batch[i]].stage;
                fprintf(stages_file,"%u,%c,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",g->phrase,g->state,g->inference,g->pop,g->render,g->fb,g->tcp,g->log,g->out,g->ppm);
            }
            fflush(stages_file);
            if(ferror(stages_file)) failed=1;
        }
        pthread_mutex_lock(&wlock);
        for(unsigned i=0;i<n;i++) free_slots[free_count++]=batch[i];
    }
    pthread_mutex_unlock(&wlock);
    return NULL;
}
/* TCP: up to four clients, accepted as soon as they connect (the listener is
 * polled with the event pipe). A new client first receives the latest event
 * line marked "snapshot":true, i.e. the current caption state. Sends are
 * nonblocking; a short write disconnects the client rather than emit
 * truncated JSON. */
#define CLIENTS 4
static int clients[CLIENTS]={-1,-1,-1,-1};
static void send_client(int k,const char *s,size_t n) {
    if(send(clients[k],s,n,MSG_NOSIGNAL|MSG_DONTWAIT)!=(ssize_t)n) { close(clients[k]); clients[k]=-1; }
}
static void accept_clients(const char *last,size_t last_len) {
    static char snap[262160];
    int fd;
    while((fd=accept4(listener,NULL,NULL,SOCK_NONBLOCK|SOCK_CLOEXEC))>=0) {
        int k=0; while(k<CLIENTS && clients[k]>=0) k++;
        if(k==CLIENTS) { close(fd); continue; }
        clients[k]=fd;
        if(last_len>1) {
            int m=snprintf(snap,sizeof(snap),"{\"snapshot\":true,%s",last+1);
            if(m>0 && (size_t)m<sizeof(snap)) send_client(k,snap,(size_t)m);
        }
    }
}
static void *output(void *unused) {
    (void)unused;
    pin_io(); set_realtime(40);
    int have_fb=cap_fb_open()==0;
    static char inbuf[524288], line[262144], rendered[262144], last[262144];
    static struct caption_model captions;
    static char caption[69632],tail[69632],segments[140000];
    static char visible[69632];
    static unsigned char styles[69632];
    /* These bounded buffers grow in used length as the transcript grows.
     * Fault their full fixed capacity before the first event so residency
     * does not masquerade as per-event allocation growth. */
    fault_zero(inbuf,sizeof(inbuf)); fault_zero(line,sizeof(line));
    fault_zero(rendered,sizeof(rendered)); fault_zero(last,sizeof(last));
    fault_zero(&captions,sizeof(captions)); fault_zero(caption,sizeof(caption));
    fault_zero(tail,sizeof(tail)); fault_zero(segments,sizeof(segments));
    fault_zero(visible,sizeof(visible)); fault_zero(styles,sizeof(styles));
    size_t have=0, last_len=0;
    double last_ppm=-1e9; int drawn=0;
    struct pollfd fds[2]={{events[0],POLLIN,0},{listener,POLLIN,0}};
    for(;;) {
        char *newline=memchr(inbuf,'\n',have);
        if(!newline) {
            if(have==sizeof(inbuf)) { failed=1; break; }
            if(poll(fds,listener>=0?2:1,-1)<0) { if(errno==EINTR) continue; failed=1; break; }
            if(listener>=0 && (fds[1].revents&POLLIN)) accept_clients(last,last_len);
            if(fds[0].revents) {
                ssize_t r=read(events[0],inbuf+have,sizeof(inbuf)-have);
                if(r<0 && errno==EINTR) continue;
                if(r<=0) break;
                have+=(size_t)r;
            }
            continue;
        }
        size_t length=(size_t)(newline-inbuf)+1;
        if(length>=sizeof(line)) { failed=1; break; }
        memcpy(line,inbuf,length); line[length]=0;
        memmove(inbuf,inbuf+length,have-length); have-=length;
        size_t g=stage_count++;
        struct stage scratch, *st=&scratch;
        memset(st,0,sizeof(*st)); st->pop=now()-epoch;
        char a[4096]="",b[4096]="";
        const char *p=strstr(line,"\"final\":\"");
        if(p) { p+=9; size_t n=strcspn(p,"\""); if(n<sizeof(a)) memcpy(a,p,n); }
        if(!a[0]) {
            p=strstr(line,"\"preview\":\"");
            if(p) { p+=11; size_t n=strcspn(p,"\""); if(n<sizeof(a)) memcpy(a,p,n); }
            p=strstr(line,"\"tentative\":\"");
            if(p) { p+=13; size_t n=strcspn(p,"\""); if(n<sizeof(b)) memcpy(b,p,n); }
        }
        int is_final=strstr(line,"\"state\":\"Committed\"")!=NULL;
        int event_only=strstr(line,"\"event_only\":true")!=NULL;
        const char *phrase_field=strstr(line,"\"phrase\":");
        unsigned phrase=phrase_field?(unsigned)strtoul(phrase_field+9,NULL,10):0;
        int state=is_final?3:strstr(line,"\"state\":\"Finalising\"")?2:1;
        if(caption_update(&captions,phrase,state,a,b,event_only,caption,sizeof(caption),
                          tail,sizeof(tail),segments,sizeof(segments))) { failed=1; stopping=1; continue; }
        /* L_update is stamped at framebuffer completion; without a usable
         * framebuffer, immediately before the (nonblocking) TCP send. */
        int shown=0;
        if(!event_only || state==2) {
            strcpy(visible,captions.prefix);
            memset(styles,0,strlen(visible));
            for(unsigned i=0;i<32;i++) {
                unsigned k=(captions.base+i)%32;
                if(!captions.part[k].state) continue;
                size_t before=strlen(visible);
                const char *text=captions.part[k].text;
                if(captions.part[k].state==1 &&
                   !strncmp(text,captions.part[k].tentative,strlen(text)))
                    text=captions.part[k].tentative;
                if(caption_append(visible,sizeof(visible),text)) { failed=1; stopping=1; break; }
                size_t end=strlen(visible);
                memset(styles+before,captions.part[k].state==2,end-before);
                if(captions.part[k].state==1) {
                    size_t stable=before+(before && text[0]?1:0)+strlen(captions.part[k].text);
                    if(stable<end) memset(styles+stable,1,end-stable);
                }
            }
            cap_render_styled(visible,styles); drawn=1; st->render=now()-epoch;
            /* Finals repaint every row, repairing any fbcon overdraw. */
            if(have_fb && cap_fb(is_final)>=0) { shown=1; st->fb=now()-epoch; }
        }
        double display_time=now()-epoch;
        const char *old_time=strstr(line,"\"inference_time\":");
        const char *old_update=strstr(line,"\"inference_update_ms\":");
        double completion=old_time?strtod(old_time+17,NULL):display_time;
        double age=old_update?strtod(old_update+22,NULL):0;
        if(length<2 || line[length-2]!='}') { failed=1; continue; }
        line[length-2]=0;
        int m=snprintf(rendered,sizeof(rendered),"%s,\"time\":%.6f,\"render_ms\":%.3f,\"update_ms\":%.3f,\"latency_valid\":%s,\"capture_anchor\":\"%s\",\"display\":\"%s\",\"caption\":\"%s\",\"committed_prefix\":\"%s\",\"prefix_before_phrase\":%u,\"segments\":%s}\n",
            line,display_time,(display_time-completion)*1000,
            age+(display_time-completion)*1000,
            !event_only && !bulk && isfinite(atomic_load(&capture_epoch))?"true":"false",
            pl_source?(strstr(line,"\"clock_fitted\":true")?"hardware-clock-fit":clock_calibrated?"fifo-measured-rate":"fifo-first-fft"):"ps-paced",shown?"fb":"tcp",caption,captions.prefix,captions.base,segments);
        if(m<0 || (size_t)m>=sizeof(rendered)) { failed=1; continue; }
        for(int k=0;k<CLIENTS;k++) if(clients[k]>=0) send_client(k,rendered,(size_t)m);
        st->tcp=now()-epoch;
        memcpy(last,rendered,(size_t)m+1); last_len=(size_t)m;
        const char *ph=strstr(line,"\"phrase\":"), *sp=strstr(line,"\"state\":\"");
        st->phrase=ph?(unsigned)strtoul(ph+9,NULL,10):0; st->state=sp?sp[9]:'?';
        st->inference=completion;
        int snapshot=ppm_path && !event_only && (is_final || (ppm_every>0 && st->pop-last_ppm>=ppm_every));
        log_line(rendered,g,st,snapshot);
        if(snapshot) last_ppm=st->pop;
        if(isatty(STDERR_FILENO)) fprintf(stderr,"\033[2K\r\033[37m%s\033[33m %s\033[0m",a,b);
    }
    /* The evidence PPM always ends equal to the last framebuffer contents. */
    if(ppm_path && drawn) ppm_snapshot(SIZE_MAX);
    cap_fb_close();
    for(int k=0;k<CLIENTS;k++) if(clients[k]>=0) close(clients[k]);
    close(events[0]); return NULL;
}
/* Input scale stored as float32 at byte 32 of the QNW1 blob (as qnrt_load). */
static int blob_scale(const char *path,float *scale) {
    unsigned char h[36]; FILE *f=fopen(path,"rb");
    if(!f) return -1;
    int ok=fread(h,1,36,f)==36 && !memcmp(h,"QNW1",4); fclose(f);
    if(!ok) { errno=EINVAL; return -1; }
    memcpy(scale,h+32,4);
    if(!isfinite(*scale) || *scale<=0) { errno=EINVAL; return -1; }
    return 0;
}
/* Exactly one submitter per backend. The CPU worker and its qnrt helpers
 * inherit cores 2..3 and nice 5; PL/capture/output keep their own affinities. */
static void infer(unsigned slot,struct cap_backend *b) {
    struct completion *c=&results[slot];
    struct job *j=&c->job;
    struct cap_result r={0};
    c->started=now()-epoch;
    int error=b->submit(b,j->x,j->t,j->n) || b->poll(b,&r,-1)!=1;
    if(!error && (r.stride>800 || r.valid>800)) error=1;
    if(!error && j->final && dump_dir && dump_final(j->phrase,j->x,j->t,j->n,&r)) error=1;
    if(!error) { memcpy(c->logits,r.logits,(size_t)29*r.stride); c->result=r; }
    c->finished=now()-epoch;
    input_put(j->x);
    pthread_mutex_lock(&rlock);
    c->error=error; c->ready=1;
    if(error) { failed=1; stopping=1; }
    pthread_cond_broadcast(&rready); pthread_mutex_unlock(&rlock);
}
static void *final_worker(void *unused) {
    (void)unused;
    cpu_set_t set; CPU_ZERO(&set);
    for(unsigned c=4-final_threads;c<4;c++) CPU_SET(c,&set);
    (void)pthread_setaffinity_np(pthread_self(),sizeof(set),&set);
    (void)setpriority(PRIO_PROCESS,(id_t)syscall(SYS_gettid),5);
    for(;;) {
        pthread_mutex_lock(&rlock);
        while(!final_count && !dispatch_done) pthread_cond_wait(&rready,&rlock);
        if(!final_count && dispatch_done) { pthread_mutex_unlock(&rlock); break; }
        unsigned slot=final_queue[final_head]; final_head=(final_head+1)%8; final_count--;
        pthread_cond_broadcast(&rspace); pthread_mutex_unlock(&rlock);
        infer(slot,&final_backend);
    }
    return NULL;
}
static void *init_final(void *unused) {
    (void)unused;
    cpu_set_t set; CPU_ZERO(&set);
    for(unsigned c=4-final_threads;c<4;c++) CPU_SET(c,&set);
    (void)pthread_setaffinity_np(pthread_self(),sizeof(set),&set);
    (void)setpriority(PRIO_PROCESS,(id_t)syscall(SYS_gettid),5);
    final_init_error=backend_cpu(&final_backend,final_blob,1600,final_threads);
    if(!final_init_error) {
        struct cap_result r;
        /* Touch maximum live tensor extents before measurement starts. */
        final_init_error=final_backend.submit(&final_backend,inputs[0],1600,1600) ||
                         final_backend.poll(&final_backend,&r,-1)!=1;
    }
    return NULL;
}
static void *emit_results(void *unused) {
    (void)unused;
    pin_io();
    FILE *out=event_out;
    /* Final CTC never feeds preview stitching; keep each phrase independent. */
    static struct { struct cap_stitch stitch; size_t previous; unsigned phrase; int used; } phrases[RESULTS];
    fault_zero(phrases,sizeof(phrases));
    for(;;) {
        pthread_mutex_lock(&rlock);
        while(!result_count && !dispatch_done) pthread_cond_wait(&rready,&rlock);
        if(!result_count && dispatch_done) { pthread_mutex_unlock(&rlock); break; }
        struct completion *c=NULL;
        for(unsigned i=0;i<result_count;i++) {
            struct completion *candidate=&results[(result_head+i)%RESULTS];
            if(candidate->emitted) continue;
            if(candidate->job.final && !candidate->announced) {
                candidate->announced=1;
                unsigned phrase=candidate->job.phrase;
                pthread_mutex_unlock(&rlock);
                state_event("Finalising",phrase);
                pthread_mutex_lock(&rlock);
            }
            if(candidate->ready) { c=candidate; break; }
        }
        if(!c) { pthread_cond_wait(&rready,&rlock); pthread_mutex_unlock(&rlock); continue; }
        struct job j=c->job;
        struct cap_result result=c->result;
        double inference_start=c->started;
        double inference_finished=c->finished;
        unsigned depth=c->depth,skips=c->skips;
        int error=c->error;
        /* Copy before returning the slot to the dispatcher. */
        int8_t logits[29*800];
        if(!error) memcpy(logits,c->logits,(size_t)29*result.stride);
        result.logits=logits;
        c->emitted=1;
        while(result_count && results[result_head].emitted) {
            result_head=(result_head+1)%RESULTS; result_count--;
        }
        pthread_cond_broadcast(&rspace); pthread_mutex_unlock(&rlock);
        if(error || stopping) continue;
        unsigned ps=j.phrase%RESULTS;
        if(!phrases[ps].used) {
            phrases[ps].used=1; phrases[ps].phrase=j.phrase;
            memset(&phrases[ps].stitch,0,sizeof(phrases[ps].stitch));
            phrases[ps].previous=j.anchor;
        }
        if(phrases[ps].phrase!=j.phrase) { failed=1; stopping=1; continue; }
        struct cap_stitch *stitch=&phrases[ps].stitch;
        size_t previous=phrases[ps].previous;
        uint8_t ids[800]; unsigned n=j.final?result.valid:(j.real+1)/2;
        if(n>800 || n>result.valid) { failed=1; stopping=1; continue; }
        for(unsigned t=0;t<n;t++) { unsigned best=0; for(unsigned b=1;b<29;b++) if(result.logits[b*result.stride+t]>result.logits[best*result.stride+t]) best=b; ids[t]=(uint8_t)best; }
        char committed[4096]="",tentative[4096]="",final[4096]="";
        if(j.final) cap_ctc(ids,n,final,sizeof(final));
        else if(cap_stitch(stitch,ids,n,j.start/2,(unsigned)(POLICY_LOOKAHEAD*50+.5),committed,tentative,sizeof(committed))) { failed=1; stopping=1; continue; }
        double time=now()-epoch;
        double sample_period; int fitted;
        double anchor_epoch=capture_time(previous,&sample_period,&fitted);
        flockfile(out);
        char audio_time[32]="null";
        if(isfinite(anchor_epoch)) snprintf(audio_time,sizeof(audio_time),"%.9f",anchor_epoch-epoch);
        fprintf(out,"{\"audio_time\":%s,\"oldest_sample\":%zu,\"sample_rate\":%.9f,\"clock_fitted\":%s,\"clock_calibrated\":%s,\"inference_start\":%.6f,\"inference_finished\":%.6f,\"ordered_wait_ms\":%.3f,\"queue_ms\":%.3f,",audio_time,previous,1/sample_period,fitted?"true":"false",clock_calibrated?"true":"false",inference_start,inference_finished,(time-inference_finished)*1000,(inference_start-j.queued)*1000);
        unsigned word_ends[2048], final_frames[800];
        for(unsigned i=0;i<n;i++) final_frames[i]=i;
        unsigned nw=cap_word_ends(j.final?ids:stitch->ids,j.final?final_frames:stitch->frames,
                                 j.final?n:stitch->count,word_ends);
        char word_json[16384]; size_t used=0;
        word_json[used++]='[';
        for(unsigned i=0;i<nw;i++) {
            int written=snprintf(word_json+used,sizeof(word_json)-used,"%s%.3f",i?",":"",j.begin/16000.0+word_ends[i]*.02);
            if(written<0 || (size_t)written>=sizeof(word_json)-used) { failed=1; break; }
            used+=(size_t)written;
        }
        word_json[used++]=']'; word_json[used]=0;
        fprintf(out,"\"phrase_begin\":%.6f,\"word_ends\":%s,\"inference_time\":%.6f,\"phrase\":%u,\"state\":\"%s\",\"capture\":%.6f,\"feature_count\":%u,\"window_start\":%u,\"emitted\":%u,\"preview\":\"%s\",\"tentative\":\"%s\",\"final\":\"%s\",\"compute_ms\":%.3f,\"backend\":\"%s\",\"commands\":%u,\"inference_update_ms\":%.3f,\"queue_depth\":%u,\"merged\":%u,\"lost_frames\":%u,\"bulk\":%s}\n",j.begin/16000.0,word_json,inference_finished,j.phrase,j.final?"Committed":"Speaking",j.sample/16000.0,j.final?j.n:j.start+j.real,j.start,stitch->emitted,committed,tentative,final,result.compute_ms,result.engine?result.engine:"cpu",result.commands,bulk || !isfinite(anchor_epoch)?0:(epoch+inference_finished-anchor_epoch)*1000,depth,skips,stitch->lost,bulk?"true":"false");
        fflush(out); funlockfile(out); phrases[ps].previous=j.sample;
        if(j.final) phrases[ps].used=0;
    }
    return NULL;
}

int main(int argc,char **argv) {
    const char *wav=NULL,*blob=NULL,*prior_path=NULL,*log_path="session.jsonl",*source="cpu-wav",*program=NULL;
    unsigned threads=3,port=0; int use_pl=0;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--bulk")) bulk=1;
        else if(!strcmp(argv[i],"--source") && i+1<argc) { source=argv[++i]; if(strcmp(source,"pl-mic") && i+1<argc) wav=argv[++i]; }
        else if(!strcmp(argv[i],"--blob") && i+1<argc) blob=argv[++i];
        else if(!strcmp(argv[i],"--program") && i+1<argc) program=argv[++i];
        else if(!strcmp(argv[i],"--prior") && i+1<argc) prior_path=argv[++i];
        else if(!strcmp(argv[i],"--log") && i+1<argc) log_path=argv[++i];
        else if(!strcmp(argv[i],"--threads") && i+1<argc) threads=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--final-threads") && i+1<argc) final_threads=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--final-backend") && i+1<argc) {
            final_mode=argv[++i];
            if(strcmp(final_mode,"cpu") && strcmp(final_mode,"pl") && strcmp(final_mode,"auto")) return 2;
        }
        else if(!strcmp(argv[i],"--port") && i+1<argc) port=(unsigned)atoi(argv[++i]);
        else if(!strcmp(argv[i],"--mel-threshold") && i+1<argc) mel_threshold=atof(argv[++i]);
        else if(!strcmp(argv[i],"--vad-snr") && i+1<argc) vad_snr_db=atof(argv[++i]);
        else if(!strcmp(argv[i],"--mic-gain") && i+1<argc) mic_gain_db=atof(argv[++i]);
        else if(!strcmp(argv[i],"--vad-log") && i+1<argc) vad_path=argv[++i];
        else if(!strcmp(argv[i],"--dump") && i+1<argc) dump_dir=argv[++i];
        else if(!strcmp(argv[i],"--stages") && i+1<argc) stages_path=argv[++i];
        else if(!strcmp(argv[i],"--sample-rate") && i+1<argc) {
            char *end; initial_sample_rate=strtod(argv[++i],&end);
            if(*end || !isfinite(initial_sample_rate) || fabs(initial_sample_rate/16000-1)>.001) return 2;
            clock_calibrated=1;
        }
        else if(!strcmp(argv[i],"--ppm") && i+1<argc) ppm_path=argv[++i];
        else if(!strcmp(argv[i],"--no-ppm")) ppm_path=NULL;
        else if(!strcmp(argv[i],"--ppm-every") && i+1<argc) ppm_every=atof(argv[++i]);
        else if(!strcmp(argv[i],"--rt")) realtime=1;
        else if(!strcmp(argv[i],"--mic") && i+1<argc) { const char *m=argv[++i]; if(strcmp(m,"1") && strcmp(m,"2") && strcmp(m,"beam")) return 2; mic_source=!strcmp(m,"beam")?2:atoi(m)-1; }
        else if(!strcmp(argv[i],"--backend") && i+1<argc) { const char *b=argv[++i]; if(strcmp(b,"cpu") && strcmp(b,"pl")) return 2; use_pl=!strcmp(b,"pl"); }
        else { fprintf(stderr,"unknown/incomplete option: %s\n",argv[i]); return 2; }
    }
    if(!strcmp(source,"pl-inject")) pl_source=1;
    else if(!strcmp(source,"pl-mic")) pl_source=2;
    else if(strcmp(source,"cpu-wav")) return 2;
    if(clock_calibrated && pl_source!=1) return 2;
#ifdef CPU_ONLY
    if(pl_source) { fprintf(stderr,"CPU_ONLY build: PL unavailable\n"); return 2; }
#endif
    if((!wav && pl_source!=2) || !blob || !prior_path || threads<1 || threads>4 || final_threads<1 || final_threads>3 || port>65535) {
        fprintf(stderr,"usage: sttcap --source cpu-wav|pl-inject FILE|pl-mic --blob BLOB --prior JSON [--backend cpu|pl --program QNP2] [--final-backend auto|cpu|pl] [--final-threads 1..3] [--bulk] [--threads 1..4] [--log JSONL] [--port PORT] [--ppm FILE|--no-ppm] [--ppm-every SEC] [--stages CSV] [--sample-rate HZ] [--rt] [--mic 1|2|beam] [--mic-gain DB] [--vad-snr DB] [--mel-threshold E] [--vad-log CSV]\n"); return 2;
    }
    if(use_pl && !program) { fprintf(stderr,"--backend pl requires --program QNP2 (exported from the --blob weights)\n"); return 2; }
    if(!use_pl && !strcmp(final_mode,"pl")) { fprintf(stderr,"--final-backend pl requires --backend pl\n"); return 2; }
    separate_finals=use_pl && strcmp(final_mode,"pl");
#ifdef __GLIBC__
    /* Keep the bounded feature scratch heap resident. One arena avoids a
     * new capture-thread arena after startup; large WAV storage still mmap's. */
    (void)mallopt(M_ARENA_MAX,1);
    (void)mallopt(M_MMAP_THRESHOLD,8*1024*1024);
    (void)mallopt(M_TRIM_THRESHOLD,-1);
#endif
    fault_zero(inputs,sizeof(inputs)); fault_zero(results,sizeof(results));
    fault_zero(feature_work,sizeof(feature_work));
    fault_zero(fe_scratch,sizeof(fe_scratch)); fault_zero(tight_work,sizeof(tight_work));
    if(qnf_prior(&prior,prior_path)) return 1;
    if(threads<4) {
        cpu_set_t set; CPU_ZERO(&set);
        for(unsigned c=1;c<=threads;c++) CPU_SET(c,&set);
        (void)pthread_setaffinity_np(pthread_self(),sizeof(set),&set);
    }
    float pl_scale=0;
    if(use_pl && blob_scale(blob,&pl_scale)) { perror(blob); return 1; }
    if(use_pl ? backend_pl(&backend,program,pl_scale) : backend_cpu(&backend,blob,1600,threads)) { perror("backend"); return 1; }
    if(separate_finals) {
        pthread_t init;
        final_blob=blob;
        if(pthread_create(&init,NULL,init_final,NULL)) return 1;
        pthread_join(init,NULL);
        if(final_init_error) { fprintf(stderr,"CPU final backend initialisation failed\n"); return 1; }
    }
    fprintf(stderr,"final routing: %s, CPU threads=%u, out-of-order result slots=%u\n",final_mode,final_threads,RESULTS);
    if(pl_source!=2) {
    audio=qn_wav(wav,&samples); if(!audio) return 1;
    size_t length=samples+12800; float *extended=realloc(audio,length*sizeof(float));
    if(!extended) return 1;
    audio=extended; memset(audio+samples,0,12800*sizeof(float)); samples=length;
    }
    session=fopen(log_path,"w"); if(!session) { perror(log_path); return 1; }
    if(port) {
        listener=socket(AF_INET,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
        int one=1; setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons((uint16_t)port),.sin_addr={htonl(INADDR_ANY)}};
        if(bind(listener,(struct sockaddr *)&addr,sizeof(addr)) || listen(listener,4)) { perror("TCP"); return 1; }
    }
    signal(SIGINT,stop); signal(SIGTERM,stop);
    if(stages_path) {
        stages_file=fopen(stages_path,"w"); if(!stages_file) { perror(stages_path); return 1; }
        fprintf(stages_file,"phrase,state,inference,pop,render,fb,tcp,log,stdout,ppm\n");
    }
    for(unsigned i=0;i<LOG_SLOTS;i++) {
        free_slots[free_count++]=i;
        /* Fault the bounded writer pool in at startup, not over the soak. */
        fault_zero(logpool[i].line,LOG_BYTES);
    }
    if(pipe(events)) return 1;
    FILE *out=fdopen(events[1],"w");
    event_out=out;
    pthread_t cap,display,files;
    epoch=now();
    state_event("Listening",0);
    atomic_store(&capture_epoch,pl_source?NAN:epoch);
    clock_origin=pl_source?NAN:epoch;
    clock_period=pl_source?1/initial_sample_rate:1.0/16000;
    void *(*capture_fn)(void *)=capture;
#ifndef CPU_ONLY
    if(pl_source) capture_fn=capture_pl;
#endif
    if(pthread_create(&files,NULL,writer,NULL) || pthread_create(&display,NULL,output,NULL) ||
       pthread_create(&cap,NULL,capture_fn,NULL)) return 1;
    pthread_t finals,emitter;
    if(pthread_create(&emitter,NULL,emit_results,NULL)) return 1;
    if(separate_finals && pthread_create(&finals,NULL,final_worker,NULL)) return 1;
    for(;;) {
        pthread_mutex_lock(&lock);
        while(!count && !done) pthread_cond_wait(&ready,&lock);
        if(!count && done) { pthread_mutex_unlock(&lock); break; }
        struct job j=queue[head]; head=(head+1)%8; count--;
        active_final=j.final && !(separate_finals && (!strcmp(final_mode,"cpu") || j.t>640));
        active_frames=j.t;
        unsigned depth=count,skips=merged;
        pthread_cond_signal(&space); pthread_mutex_unlock(&lock);
        if(stopping) { input_put(j.x); continue; }
        int cpu_final=j.final && separate_finals && (!strcmp(final_mode,"cpu") || j.t>640);
        pthread_mutex_lock(&rlock);
        while(result_count==RESULTS || (cpu_final && final_count==8)) pthread_cond_wait(&rspace,&rlock);
        unsigned slot=(result_head+result_count++)%RESULTS;
        results[slot].job=j; results[slot].depth=depth; results[slot].skips=skips;
        results[slot].ready=0; results[slot].error=0;
        results[slot].emitted=0; results[slot].announced=0;
        if(cpu_final) {
            final_queue[(final_head+final_count++)%8]=slot;
            pthread_cond_broadcast(&rready);
        }
        pthread_mutex_unlock(&rlock);
        if(!cpu_final) infer(slot,&backend);
    }
    pthread_mutex_lock(&rlock); dispatch_done=1; pthread_cond_broadcast(&rready); pthread_mutex_unlock(&rlock);
    if(separate_finals) pthread_join(finals,NULL);
    pthread_join(emitter,NULL);
    pthread_join(cap,NULL); fclose(out); pthread_join(display,NULL);
    pthread_mutex_lock(&wlock); writer_stop=1; pthread_cond_signal(&wready); pthread_mutex_unlock(&wlock);
    pthread_join(files,NULL);
    if(log_dropped) { fprintf(stderr,"writer queue full: %u log lines dropped\n",log_dropped); failed=1; }
    if(stages_file && fclose(stages_file)) failed=1;
    fclose(session); if(listener>=0) close(listener);
    if(separate_finals) final_backend.destroy(&final_backend);
    backend.destroy(&backend); free(audio); return failed?1:0;
}
