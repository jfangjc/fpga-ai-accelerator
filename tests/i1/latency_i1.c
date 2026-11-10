/* Sample-acceptance timing from FIFO occupancy during first-frame compute.
 * COLLECT drains far faster than 16 kHz: count>=4 with FRAMES==0 identifies
 * first FFT, which has consumed exactly 320 samples. With one queued 4096
 * sample BD, no FIFO-full condition, acceptance continues every 6250 clocks.
 * At the register read, sample 319+count was accepted within the last period.
 * Subtract count periods to bracket frame 0's last sample. */
#include "fedrv.h"  /* build with -I<fedrv directory> */
#include <stdio.h>
int main(void) {
  struct fe f;
  struct fe_config c = {.source=FE_SRC_INJECT,.paced=1,.irq_frames=1,.gain=16384};
  int32_t x[4096] = {0};
  struct fe_frame frame;
  double lo_sum=0, hi_sum=0, width_max=0, hi_max=0;
  for (unsigned i=0; i<100; i++) {
    if (fe_open(&f) || fe_configure(&f,&c) || fe_start(&f) ||
        fe_inject(&f,x,4096,1000)!=4096) { perror("setup"); return 1; }
    uint64_t deadline=plio_now_ns()+100000000ULL, a, b;
    unsigned count;
    do {
      if (fe_hw_frames(&f)) { fprintf(stderr,"missed first FFT\n"); return 1; }
      a=plio_now_ns();
      count=fe_status(&f)>>4;
      b=plio_now_ns();
      if (b>deadline || fe_hw_frames(&f)) return 1;
    } while (count<4);
    if (count>=1024 || fe_capture(&f,&frame,1,1000)!=1) return 1;
    uint64_t last_lo=a-(count+1)*62500ULL, last_hi=b-count*62500ULL;
    double lo=(frame.avail_ns-last_hi)*1e-3, hi=(frame.avail_ns-last_lo)*1e-3;
    lo_sum+=lo; hi_sum+=hi;
    if (hi-lo>width_max) width_max=hi-lo;
    if (hi>hi_max) hi_max=hi;
    fe_close(&f);
  }
  printf("trials=100 latency_mean_lower_us=%.3f latency_mean_upper_us=%.3f "
         "max_upper_us=%.3f max_bracket_width_us=%.3f\n",
         lo_sum/100,hi_sum/100,hi_max,width_max);
  return 0;
}
