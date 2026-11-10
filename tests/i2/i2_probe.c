/* Board-only diagnostics, linked against the unmodified engine driver. */
#include "../../sw/engdrv/engdrv.h"
#include "../../sw/plio/plio.h"
#include "../../sw/qnrt/qnrt.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint64_t sync_ns;
static uint64_t dma_submit_ns, irq_wait_ns;
static unsigned sync_calls, dma_calls, irq_calls;
int __real_plio_buffer_sync_range(struct plio_buffer *, size_t, size_t, int);
int __wrap_plio_buffer_sync_range(struct plio_buffer *b, size_t o, size_t n, int cpu) {
  uint64_t start = plio_now_ns();
  int rc = __real_plio_buffer_sync_range(b, o, n, cpu);
  sync_ns += plio_now_ns() - start;
  sync_calls++;
  return rc;
}
int __real_plio_dma_start_sized(struct plio_dma *, uint64_t, size_t, uint64_t, size_t);
int __wrap_plio_dma_start_sized(struct plio_dma *d, uint64_t src, size_t n, uint64_t dst, size_t cap) {
  uint64_t start = plio_now_ns();
  int rc = __real_plio_dma_start_sized(d, src, n, dst, cap);
  dma_submit_ns += plio_now_ns() - start; dma_calls++;
  return rc;
}
int __real_plio_irq_wait(struct plio_uio *, int, uint32_t *);
int __wrap_plio_irq_wait(struct plio_uio *u, int ms, uint32_t *delta) {
  uint64_t start = plio_now_ns();
  int rc = __real_plio_irq_wait(u, ms, delta);
  irq_wait_ns += plio_now_ns() - start; irq_calls++;
  return rc;
}
static void check(int rc) { if (rc) { perror("probe"); exit(1); } }
int main(int argc, char **argv) {
  if (argc != 4) return 2;
  engdrv *e;
  struct engdrv_config cfg = {0, 2, 3, 10000};
  struct engdrv_timing timing;
  check(engdrv_open(&e, &cfg));
  uint32_t version;
  check(engdrv_version(e, &version));
  if (version != 0x10002) return 1;
  check(engdrv_ping(e, &timing));
  printf("PASS VERSION=%08x PING cycles=%llu\n", version, (unsigned long long)timing.cycles);
  for (unsigned i = 0; i < 4; i++) {
    uint32_t p[16] = {0x32434e51, 1, 0, 77}, response[16];
    unsigned word[] = {0, 1, 2, 4}, value[] = {0, 2, 99, 16}, status[] = {1, 1, 2, 3};
    p[word[i]] = value[i]; size_t n = sizeof(response);
    check(engdrv_exchange(e, p, sizeof(p), response, &n, &timing));
    if (timing.status != status[i]) return 1;
    check(engdrv_ping(e, &timing));
  }
  puts("PASS malformed/recovery 4");
  check(engdrv_recover(e)); check(engdrv_ping(e, &timing));
  puts("PASS explicit reset/recovery");
  check(engdrv_load(e, argv[1]));
  if (!engdrv_network_capable(e)) return 1;
  check(engdrv_set_mode(e, ENGDRV_NETWORK));
  puts("PASS RUN_NETWORK capable (resident program)");
  FILE *f = fopen(argv[2], "rb"); if (!f) return 1;
  int8_t x[64*304]; if (fread(x, 1, sizeof(x), f) != sizeof(x)) return 1; fclose(f);
  const int8_t *y; unsigned t, v;
  sync_ns = dma_submit_ns = irq_wait_ns = 0;
  sync_calls = dma_calls = irq_calls = 0;
  uint64_t start = plio_now_ns();
  check(engdrv_run_network(e, x, 304, 300, &y, &t, &v, &timing));
  uint64_t elapsed = plio_now_ns() - start;
  printf("NETWORK wall_ns=%llu sync_ns=%llu cycles=%llu output=%u/%u\n",
         (unsigned long long)elapsed, (unsigned long long)sync_ns,
         (unsigned long long)timing.cycles, t, v);
  printf("TRANSPORT dma_submit_ns=%llu irq_wait_ns=%llu sync_calls=%u dma_calls=%u irq_calls=%u\n",
         (unsigned long long)dma_submit_ns, (unsigned long long)irq_wait_ns,
         sync_calls, dma_calls, irq_calls);
  puts("layer,cycles,submit_irq_ns,irq_ready_ns,total_ns,starvation,terms");
  for (unsigned i = 0; i < 1; i++) {
    check(engdrv_network_timing(e, i, &timing));
    printf("%u,%llu,%llu,%llu,%llu,%u,%u\n", i,
      (unsigned long long)timing.cycles,
      (unsigned long long)(timing.irq_ns-timing.submit_ns),
      (unsigned long long)(timing.ready_ns-timing.irq_ns),
      (unsigned long long)(timing.ready_ns-timing.submit_ns), timing.starvation, timing.terms);
  }
  qnrt *q; check(qnrt_load(&q, argv[3], 304, 4));
  puts("repeat,cpu_ns,pl_ns,cycles,sync_ns,dma_submit_ns,irq_wait_ns,host_remaining_ns,submit_irq_ns,irq_ready_ns,outside_ns,sync_calls");
  for (unsigned i = 0; i < 20; i++) {
    const int8_t *cpu_y; unsigned ct, cv; uint64_t cpu_ns = 0, pl_ns = 0;
    for (unsigned j = 0; j < 2; j++) {
      if ((i + j) % 2 == 0) {
        start = plio_now_ns();
        check(qnrt_run(q, x, 304, 300, &cpu_y, &ct, &cv));
        cpu_ns = plio_now_ns() - start;
      } else {
        sync_ns = dma_submit_ns = irq_wait_ns = 0; sync_calls = 0;
        start = plio_now_ns();
        check(engdrv_run_network(e, x, 304, 300, &y, &t, &v, &timing));
        pl_ns = plio_now_ns() - start;
      }
    }
    if (ct != t || cv != v || memcmp(cpu_y, y, 29*t)) return 1;
    struct engdrv_run_info info; check(engdrv_last_run(e, &info));
    if (!info.network || info.commands != 1) return 1;
    printf("%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u\n", i,
      (unsigned long long)cpu_ns, (unsigned long long)pl_ns,
      (unsigned long long)timing.cycles, (unsigned long long)sync_ns,
      (unsigned long long)dma_submit_ns, (unsigned long long)irq_wait_ns,
      (unsigned long long)(pl_ns-sync_ns-dma_submit_ns-irq_wait_ns),
      (unsigned long long)(timing.irq_ns-timing.submit_ns),
      (unsigned long long)(timing.ready_ns-timing.irq_ns),
      (unsigned long long)info.outside_ns, sync_calls);
  }
  puts("PASS 20 paired CPU/PL 3 s windows bit-exact");
  /* CPU reference kept for the post-recovery comparisons below. */
  const int8_t *ref; unsigned rt, rv;
  check(qnrt_run(q, x, 304, 300, &ref, &rt, &rv));
  static int8_t want[29*152]; memcpy(want, ref, 29*rt);
  qnrt_free(q);
  check(engdrv_recover(e));
  check(engdrv_run_network(e, x, 304, 300, &y, &t, &v, &timing));
  if (t != rt || v != rv || memcmp(want, y, 29*t)) return 1;
  puts("PASS explicit reset -> RUN_NETWORK bit-exact");
  engdrv_close(e);
  cfg.timeout_ms = 1; check(engdrv_open(&e, &cfg)); check(engdrv_load(e, argv[1]));
  errno = 0;
  check(engdrv_set_mode(e, ENGDRV_NETWORK));
  int rc = engdrv_run_network(e, x, 304, 300, &y, &t, &v, &timing);
  if (rc != -1 || errno != ETIMEDOUT) { fprintf(stderr,"timeout rc=%d errno=%d\n",rc,errno); return 1; }
  check(engdrv_ping(e, &timing)); puts("PASS timeout/reset/PING recovery");
  engdrv_close(e);
  cfg.timeout_ms = 10000; check(engdrv_open(&e, &cfg)); check(engdrv_load(e, argv[1]));
  check(engdrv_set_mode(e, ENGDRV_NETWORK));
  check(engdrv_run_network(e, x, 304, 300, &y, &t, &v, &timing));
  if (t != rt || v != rv || memcmp(want, y, 29*t)) return 1;
  puts("PASS RUN_NETWORK bit-exact after timeout recovery");
  engdrv_close(e);
  return 0;
}
