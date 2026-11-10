#include "qnrt.h"
int main(int argc, char **argv) {
  qnrt *c = 0;
  int r = argc == 2 ? qnrt_load(&c, argv[1], 32, 1) : -1;
  qnrt_free(c);
  return r ? 1 : 0;
}
