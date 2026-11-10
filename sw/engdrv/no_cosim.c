#include "transport.h"
#include <errno.h>
int cosim_open(struct transport **t, const struct engdrv_config *c) {
  (void)t;
  (void)c;
  errno = ENOTSUP;
  return -1;
}
