/* Bounded, single-thread reproducer for stt-fe SG completion stall.
 * cc -O2 -std=c11 -I. tools/repro_i1.c libfedrv.a -pthread -lm -o repro_i1
 * sudo flock /home/ubuntu/stt/board.lock ./repro_i1
 */
#include "fedrv.h"  /* build with -I<fedrv directory> */
#include <stdio.h>

int main(void) {
  struct fe f;
  struct fe_config c = {.source = FE_SRC_INJECT, .irq_frames = 1, .gain = 16384};
  int32_t samples[4096] = {0};
  struct fe_frame frames[32];
  if (fe_open(&f) || fe_configure(&f, &c) || fe_start(&f)) {
    perror("setup"); return 1;
  }
  if (fe_inject(&f, samples, 4096, 1000) != 4096) {
    perror("inject"); fe_close(&f); return 1;
  }
  unsigned got = 0;
  unsigned bad_power = 0;
  uint64_t end = plio_now_ns() + 2000000000ULL;
  while (plio_now_ns() < end) {
    int n = fe_capture(&f, frames, 32, 100);
    if (n < 0) { perror("capture"); break; }
    for (int i = 0; i < n; i++)
      for (unsigned b = 0; b < FE_BANDS; b++) bad_power += frames[i].power[b] != 0;
    got += (unsigned)n;
  }
  printf("zero_samples=4096 expected=24 captured=%u hw_frames=%u status=0x%x\n",
         got, fe_hw_frames(&f), fe_status(&f));
  printf("MM2S_CR=0x%x SR=0x%x S2MM_CR=0x%x SR=0x%x\n",
         f.dma.tx.regs[0], f.dma.tx.regs[1], f.dma.tx.regs[12], f.dma.tx.regs[13]);
  if (plio_buffer_sync_range(&f.rx, 0, 128, 1)) perror("sync");
  printf("RX_BD0_status=0x%x RX_BD1_status=0x%x\n",
         ((uint32_t *)f.rx.data)[7], ((uint32_t *)f.rx.data)[23]);
  if (plio_buffer_sync_range(&f.tx, 0, 192, 1)) perror("tx sync");
  for (unsigned i = 0; i < 3; i++) {
    uint32_t *d = (uint32_t *)f.tx.data + i * 16;
    printf("TX_BD%u next=%08x:%08x buf=%08x:%08x control=%08x status=%08x\n",
           i, d[1], d[0], d[3], d[2], d[6], d[7]);
  }
  if (plio_buffer_sync_range(&f.rx, 65536, 576, 1)) perror("data sync");
  for (unsigned i = 0; i < 12; i++)
    printf("RX_word%u=%08x\n", i, ((uint32_t *)f.rx.data)[16384+i]);
  int ok = got == 24 && fe_hw_frames(&f) == 24 && !bad_power &&
           !(f.dma.tx.regs[1] & 0x4770) && !(f.dma.tx.regs[13] & 0x4770);
  fe_close(&f);
  return ok ? 0 : 1;
}
