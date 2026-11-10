#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include "../fedrv.c"
int main(void) {
  uint32_t regs[32] = {0};
  struct fe f = {0};
  f.regs.regs = regs;
  f.control = C_ENABLE | C_PACE | C_BUTTON_IRQ;
  assert(fe_ui_poll(&f, 1) == 0);
  assert(regs[CONTROL/4] == (C_ENABLE | C_PACE | C_BUTTON_IRQ | C_LED));
  regs[STATUS/4] = 9;
  assert(fe_ui_poll(&f, 0) == 1);
  assert(regs[CLEAR/4] == 8); /* Preserve unrelated frame IRQ. */
  assert(regs[CONTROL/4] == (C_ENABLE | C_PACE | C_BUTTON_IRQ));
  regs[STATUS/4] = 1; /* Model W1C of the sticky flag. */
  assert(fe_ui_poll(&f, 0) == 0);
  return 0;
}
