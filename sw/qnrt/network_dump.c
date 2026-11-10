#include "qnrt.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
  if (argc != 5)
    return 2;
  unsigned T = atoi(argv[3]);
  qnrt *c;
  if (qnrt_load(&c, argv[1], T, 4))
    return 1;
  FILE *f = fopen(argv[2], "rb");
  int8_t *x = malloc((size_t)64 * T);
  if (!f || !x || fread(x, 1, (size_t)64 * T, f) != (size_t)64 * T)
    return 1;
  fclose(f);
  const int8_t *y;
  unsigned t, v;
  if (qnrt_run(c, x, T, atoi(argv[4]), &y, &t, &v))
    return 1;
  for (unsigned k = 0; k < 29; k++)
    fwrite(y + (size_t)k * t, 1, v, stdout);
  qnrt_free(c);
  free(x);
  return 0;
}
