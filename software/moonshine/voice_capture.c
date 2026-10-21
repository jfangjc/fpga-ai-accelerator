/* SPH0645 FIFO reader for the pinned 100 MHz bulk image. Inference never runs
 * in this process. Stdout is framed signed PCM24-in-int32, not console text. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/file.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;
static void stop(int signal_number) { (void)signal_number; stopping=1; }
static uint64_t nanos(void) {
 struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
 return (uint64_t)t.tv_sec*1000000000u+(uint64_t)t.tv_nsec;
}
static uint32_t mmio(volatile uint32_t *p,unsigned offset) {
 uint32_t v=p[offset/4];
#if defined(__aarch64__)
 __asm__ __volatile__("dmb oshld" ::: "memory");
#endif
 return v;
}
static int write_all(const void *data,size_t bytes) {
 const uint8_t *p=data;
 uint64_t deadline=nanos()+200000000u;
 while(bytes) {
  ssize_t n=write(STDOUT_FILENO,p,bytes);
  if(n>0){p+=n;bytes-=(size_t)n;continue;}
  if(n<0 && errno==EINTR)continue;
  if(n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) {
   if(nanos()>deadline){fprintf(stderr,"capture output blocked; aborting without hiding sample loss\n");return -1;}
   struct pollfd fd={STDOUT_FILENO,POLLOUT,0};poll(&fd,1,5);continue;
  }
  return -1;
 }
 return 0;
}
struct frame {
 uint32_t magic,sequence;
 uint64_t end_ns;
 uint32_t samples,captured,dropped,startup_dropped,fifo_count,reserved;
};
_Static_assert(sizeof(struct frame)==40,"capture frame ABI");
int main(int argc,char **argv) {
 if(argc!=3 && argc!=4){fprintf(stderr,"usage: voice_capture /dev/uioN seconds(0=continuous) [MIC1-selection:0..3]\n");return 2;}
 char *end=NULL;double seconds=strtod(argv[2],&end);
 if(!end || *end || !__builtin_isfinite(seconds) || seconds<0 || seconds>86400)return 2;
 uint64_t target=(uint64_t)(seconds*16000.0),total=0;
 if(seconds>0 && target==0)return 2;
 struct sigaction action={0};action.sa_handler=stop;sigemptyset(&action.sa_mask);
 if(sigaction(SIGINT,&action,NULL) || sigaction(SIGTERM,&action,NULL))return 1;
 action.sa_handler=SIG_IGN;
 if(sigaction(SIGPIPE,&action,NULL))return 1;
 int lock=open("/run/lock/kv260-microphone.lock",O_CREAT|O_RDWR,0644);
 if(lock<0 || flock(lock,LOCK_EX|LOCK_NB)<0){fprintf(stderr,"microphone already in use or lock unavailable\n");return 1;}
 if(argc==4 && access("/sys/bus/platform/devices/asr-v3-mic-select-clock/set_rate",R_OK)) {
  fprintf(stderr,"selectable microphone app is not active\n");return 1;
 }
 int fd=open(argv[1],O_RDWR|O_SYNC);if(fd<0){perror("open voice UIO");return 1;}
 volatile uint32_t *p=mmap(NULL,65536,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
 if(p==MAP_FAILED){perror("mmap voice UIO");close(fd);return 1;}
 if(argc==4) {
  char *selection_end=NULL;long selection=strtol(argv[3],&selection_end,10);
  if(selection_end==argv[3] || *selection_end || selection<0 || selection>3)return 2;
  if(mmio(p,0x8014)!=0x3143494du){fprintf(stderr,"MIC1 capture firmware required\n");return 1;}
  p[0x8010/4]=(uint32_t)selection;
#if defined(__aarch64__)
  __asm__ __volatile__("dmb oshst" ::: "memory");
#endif
  struct timespec settle={0,1000000};nanosleep(&settle,NULL);
  if(mmio(p,0x8010)!=(uint32_t)selection){fprintf(stderr,"microphone selection did not latch\n");return 1;}
 }
 int flags=fcntl(STDOUT_FILENO,F_GETFL);if(flags<0 || fcntl(STDOUT_FILENO,F_SETFL,flags|O_NONBLOCK)<0)return 1;
 /* Model initialization can leave the free-running FIFO full. Discard only
  * this startup backlog; all subsequent hardware drops are fatal. */
 unsigned discarded=0;uint64_t start=nanos();
 while(mmio(p,0x8000)&0x7ffu) {
  (void)mmio(p,0x8004);discarded++;
  if(discarded>16384 || nanos()-start>1000000000u){fprintf(stderr,"cannot drain startup FIFO\n");return 1;}
 }
 uint32_t baseline=mmio(p,0x8008),initial=mmio(p,0x800c),sequence=0;
 int32_t samples[320];unsigned count=0;int status=0;
 start=nanos();uint64_t last_sample=start;
 while(!stopping && (!target || total<target)) {
  uint32_t available=mmio(p,0x8000)&0x7ffu;
  if(available>1024){fprintf(stderr,"invalid FIFO status\n");status=1;break;}
  uint32_t dropped=mmio(p,0x8008);
  if(dropped!=baseline){fprintf(stderr,"hardware FIFO dropped %u samples\n",dropped-baseline);status=1;break;}
  if(!available) {
   if(nanos()-last_sample>1000000000u){fprintf(stderr,"microphone clock/capture stalled\n");status=1;break;}
   struct timespec delay={0,500000};nanosleep(&delay,NULL);continue;
  }
  for(unsigned i=0;i<available && (!target || total<target);i++) {
   uint32_t bits=mmio(p,0x8004);
   int32_t value=(int32_t)(bits<<8)>>8;
   samples[count++]=value;total++;last_sample=nanos();
   if(count==320 || (target && total==target)) {
    struct frame h={0x31434d56u,sequence++,last_sample,count,mmio(p,0x800c),mmio(p,0x8008),baseline,mmio(p,0x8000)&0x7ffu,0};
    if(h.dropped!=baseline || write_all(&h,sizeof h) || write_all(samples,count*sizeof *samples)){status=1;break;}
    count=0;
   }
  }
  if(status)break;
 }
 if(!status && count) {
  struct frame h={0x31434d56u,sequence++,last_sample,count,mmio(p,0x800c),mmio(p,0x8008),baseline,mmio(p,0x8000)&0x7ffu,0};
  if(h.dropped!=baseline || write_all(&h,sizeof h) || write_all(samples,count*sizeof *samples))status=1;
 }
 uint32_t dropped=mmio(p,0x8008)-baseline;
 fprintf(stderr,"{\"samples\":%" PRIu64 ",\"seconds\":%.6f,\"startup_discarded\":%u,\"startup_dropped\":%u,\"newly_dropped\":%u,\"captured_delta\":%u}\n",
         total,(nanos()-start)/1e9,discarded,baseline,dropped,mmio(p,0x800c)-initial);
 if(dropped)status=1;
 munmap((void*)p,65536);close(fd);close(lock);return status;
}
