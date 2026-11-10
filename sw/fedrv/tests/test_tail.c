/* Build with -ffunction-sections -Wl,--gc-sections; unused board APIs drop out. */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdint.h>
static unsigned writes, offsets[2];
static uint32_t values[2];
static void fe_test_mmio(unsigned o, uint32_t v) {
  assert(writes < 2);
  offsets[writes] = o;
  values[writes++] = v;
}
#define FE_TEST_MMIO
#include "../fedrv.c"
int main(void) {
  uint32_t regs[32] = {0};
  struct fe f = {0};
  f.dma.tx.regs = regs;
  const unsigned tails[] = {MM2S_TAIL, S2MM_TAIL};
  for (unsigned i = 0; i < 2; i++) {
    writes = 0;
    put64(&f, tails[i], UINT64_C(0x123456789abc0040));
    assert(writes == 2 && offsets[0] == tails[i] && offsets[1] == tails[i]+4);
    assert(values[0] == 0x9abc0040 && values[1] == 0x12345678);
    assert(regs[tails[i]/4] == values[0] && regs[tails[i]/4+1] == values[1]);
  }
  return 0;
}
