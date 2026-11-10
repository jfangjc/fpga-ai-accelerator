#define _POSIX_C_SOURCE 200809L
#include "../plio/plio.h"
#include "transport.h"
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct engdrv {
  struct transport *tr;
  unsigned count, tag;
  uint8_t *program;
  size_t size;
  uint32_t **rec;
  int8_t *logits, *features;
  unsigned frames, valid, ot, ov;
  pthread_t worker;
  pthread_mutex_t lock;
  pthread_cond_t cond;
  int active, done, error, poisoned;
  struct engdrv_timing timing, *layer_timing;
  unsigned timed;
  int mode, network_ok, network_errno;
  uint32_t version;
  uint8_t *head;
  struct engdrv_run_info info;
};
const char *engdrv_status_name(uint32_t status) {
  static const char *names[] = {"OK",
                                "magic/version",
                                "opcode",
                                "length/TKEEP/TLAST",
                                "descriptor/parameters",
                                "accumulator overflow",
                                "idle watchdog",
                                "internal invariant"};
  return status < 8 ? names[status] : "unknown status";
}
static uint32_t rd(const void *p) {
  const uint8_t *b = p;
  return b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 |
         (uint32_t)b[3] << 24;
}
static size_t pad(size_t n) { return (n + 15) & ~(size_t)15; }
static int bad(void) {
  errno = EINVAL;
  return -1;
}
int engdrv_open(engdrv **out, const struct engdrv_config *cfg) {
  if (!out || !cfg || cfg->timeout_ms <= 0)
    return bad();
  engdrv *e = calloc(1, sizeof(*e));
  if (!e)
    return -1;
  if ((cfg->cosim ? cosim_open : board_open)(&e->tr, cfg)) {
    free(e);
    return -1;
  }
  pthread_mutex_init(&e->lock, 0);
  pthread_cond_init(&e->cond, 0);
  *out = e;
  return 0;
}
void engdrv_close(engdrv *e) {
  if (!e)
    return;
  if (e->active)
    pthread_join(e->worker, 0);
  e->tr->close(e->tr);
  free(e->rec);
  free(e->program);
  free(e->logits);
  free(e->features);
  free(e->layer_timing);
  free(e->head);
  pthread_mutex_destroy(&e->lock);
  pthread_cond_destroy(&e->cond);
  free(e);
}
int engdrv_recover(engdrv *e) {
  int r = e->tr->reset(e->tr);
  e->poisoned = !!r;
  return r;
}
int engdrv_version(engdrv *e, uint32_t *v) {
  if (!e || !v)
    return bad();
  *v = e->tr->version(e->tr);
  return 0;
}
int engdrv_exchange(engdrv *e, const void *p, size_t n, void *r, size_t *cap,
                    struct engdrv_timing *t) {
  struct engdrv_timing local = {0};
  if (e && e->poisoned) {
    errno = EIO;
    return -1;
  }
  if (!e || !p || n < 16 || n % 16 || !r || !cap || *cap < 64)
    return bad();
  if (!t)
    t = &local;
  memset(t, 0, sizeof(*t));
  t->submit_ns = plio_now_ns();
  if (e->tr->exchange(e->tr, p, n, r, cap, t)) {
    int err = errno;
    engdrv_recover(e);
    errno = err;
    return -1;
  }
  t->ready_ns = plio_now_ns();
  if (*cap < 64 || rd(r) != 0x32534e51 || rd((char *)r + 4) != 1 ||
      (n >= 64 && rd((char *)r + 12) != rd((const char *)p + 12))) {
    engdrv_recover(e);
    errno = EPROTO;
    return -1;
  }
  t->status = rd((char *)r + 8);
  t->cycles = rd((char *)r + 16) | (uint64_t)rd((char *)r + 20) << 32;
  t->starvation = rd((char *)r + 24);
  t->terms = rd((char *)r + 28);
  return 0;
}
int engdrv_ping(engdrv *e, struct engdrv_timing *t) {
  uint32_t p[16] = {0x32434e51, 1, 0, 0}, r[16];
  size_t n = 64;
  struct engdrv_timing x;
  if (!t)
    t = &x;
  p[3] = ++e->tag;
  if (engdrv_exchange(e, p, 64, r, &n, t))
    return -1;
  /* 0x00010001 (RUN_LAYER) and 0x00010002 (adds RUN_NETWORK). */
  if (t->status || n != 64 || r[13] != 0 || r[15] >> 16 != 1 ||
      (r[15] & 0xffff) < 1) {
    errno = EIO;
    return -1;
  }
  return 0;
}
unsigned engdrv_layers(const engdrv *e) { return e->count; }
int engdrv_load(engdrv *e, const char *path) {
  if (e->active) {
    errno = EBUSY;
    return -1;
  }
  FILE *f = fopen(path, "rb");
  if (!f)
    return -1;
  if (fseek(f, 0, SEEK_END)) {
    fclose(f);
    return -1;
  }
  long l = ftell(f);
  rewind(f);
  if (l < 16 || l > 128 * 1024 * 1024) {
    fclose(f);
    return bad();
  }
  size_t n = (size_t)l;
  uint8_t *p = malloc(n);
  if (!p) {
    fclose(f);
    return -1;
  }
  int ok = fread(p, 1, n, f) == n;
  fclose(f);
  if (!ok || rd(p) != 0x32504e51 || rd(p + 4) != 1 || rd(p + 12) != n) {
    free(p);
    return bad();
  }
  unsigned count = rd(p + 8);
  if (!count || count > (n - 16) / 64) {
    free(p);
    return bad();
  }
  uint32_t **rec = calloc(count, sizeof(*rec));
  if (!rec) {
    free(p);
    return -1;
  }
  size_t off = 16;
  for (unsigned i = 0; i < count; i++) {
    if (off > n || n - off < 64)
      goto invalid;
    uint32_t *d = (uint32_t *)(p + off);
    size_t bytes = d[1];
    unsigned co = d[7];
    if (d[0] != 0x32524c51 || bytes < 64 || bytes % 16 || bytes > n - off ||
        d[2] != i + 1 || d[4] > i || d[5] > i || d[6] > 1 || !co || co > 1024 ||
        d[14] || d[15])
      goto invalid;
    size_t arr = pad(co * 4), expected;
    if (d[3] == 2) {
      if (d[6] != 1)
        goto invalid;
      for (int j = 8; j < 14; j++)
        if (d[j])
          goto invalid;
      expected = 64 + 4 * arr;
    } else if (d[3] == 1) {
      if (!d[8] || d[8] > 1024)
        goto invalid;
      int pw =
          d[9] == 1 && d[10] == 1 && d[11] == 1 && d[12] == 0 && d[13] == 1;
      int dw = d[8] == 1 && d[13] == co && d[9] >= 1 && d[9] <= 87 &&
               (d[10] == 1 || d[10] == 2) && (d[11] == 1 || d[11] == 2) &&
               d[12] <= 86;
      if (!pw && !dw)
        goto invalid;
      expected =
          64 + 3 * arr + ((co + 31) / 32) * ((d[8] * d[9] + 15) / 16) * 512;
    } else
      goto invalid;
    if (bytes != expected)
      goto invalid;
    unsigned pairs = d[3] == 2 ? 2 : 1;
    for (unsigned k = 0; k < pairs; k++) {
      uint8_t *m = (uint8_t *)d + 64 + (d[3] == 2 ? 2 * k : 1) * arr;
      for (unsigned c = 0; c < co; c++)
        if ((int32_t)rd(m + c * 4) <= 0 || (int32_t)rd(m + arr + c * 4) < -30)
          goto invalid;
    }
    rec[i] = d;
    off += bytes;
  }
  if (off != n)
    goto invalid;
  free(e->program);
  free(e->rec);
  e->program = p;
  e->rec = rec;
  e->size = n;
  e->count = count;
  /* RUN_NETWORK needs the 0x00010002 engine, the fixed 186-record graph
   * (the RTL checks every descriptor) and a resident, cleaned program. */
  e->network_ok = 0;
  e->network_errno = ENOTSUP;
  e->version = e->tr->version(e->tr);
  if (e->version >> 16 == 1 && (e->version & 0xffff) >= 2 && count == 186 &&
      rec[count - 1][7] == 29 && !rec[count - 1][6] &&
      e->tr->program && e->tr->network) {
    if (!e->tr->program(e->tr, p, n))
      e->network_ok = 1;
    else
      e->network_errno = errno;
  }
  return 0;
invalid:
  free(p);
  free(rec);
  return bad();
}
static int is_add(const uint32_t *d) { return d[3] == 2; }
static int is_dw(const uint32_t *d) {
  return !is_add(d) && !(d[9] == 1 && d[10] == 1 && d[11] == 1 &&
                         d[12] == 0 && d[13] == 1);
}
static unsigned in_channels(const uint32_t *d) {
  return is_add(d) || is_dw(d) ? d[7] : d[8];
}
/* Convolution output length; floor division matters when valid=0. */
static int conv_len(const uint32_t *d, unsigned padding, int n) {
  if (is_add(d))
    return n;
  int num = n + 2 * (int)padding - (int)d[11] * ((int)d[9] - 1) - 1;
  int s = (int)d[10];
  return num >= 0 ? num / s + 1 : -((-num + s - 1) / s) + 1;
}
/* One RUN_LAYER command for record d, with `padding` in place of d[12]
 * (tiled slices carry their halo explicitly and use padding 0). */
static int layer_cmd(engdrv *e, const uint32_t *d, unsigned padding,
                     const void *a, const void *b, unsigned frames,
                     unsigned valid, int sign, void *out, size_t capacity,
                     unsigned *ot, unsigned *ov, struct engdrv_timing *tim) {
  int add = is_add(d), dw = is_dw(d);
  unsigned ci = in_channels(d);
  if ((add && (!b || !sign)) || (!add && b) || !frames || frames > 800 ||
      valid > frames)
    return bad();
  int tout = conv_len(d, padding, (int)frames),
      v = conv_len(d, padding, (int)valid);
  if (tout < 1 || tout > 800 || capacity < (size_t)d[7] * tout)
    return bad();
  if (v < 0)
    v = 0;
  size_t input = pad((size_t)ci * frames),
         n = 64 + input * (add ? 2 : 1) + d[1],
         rn = 64 + pad((size_t)d[7] * tout);
  uint8_t *cmd = calloc(1, n), *resp = malloc(rn);
  if (!cmd || !resp) {
    free(cmd);
    free(resp);
    return -1;
  }
  uint32_t *h = (uint32_t *)cmd;
  h[0] = 0x32434e51;
  h[1] = 1;
  h[2] = 1;
  h[3] = ++e->tag;
  h[4] = n - 64;
  h[5] = ci;
  h[6] = frames;
  h[7] = valid;
  h[8] = d[7];
  h[9] = sign;
  h[10] = add ? 2 : dw ? 1 : 0;
  if (dw) {
    h[11] = d[9];
    h[12] = d[10];
    h[13] = d[11];
    h[14] = padding;
  }
  memcpy(cmd + 64, a, (size_t)ci * frames);
  if (add)
    memcpy(cmd + 64 + input, b, (size_t)ci * frames);
  uint8_t *record = cmd + 64 + input * (add ? 2 : 1);
  memcpy(record, d, d[1]);
  if (!add)
    ((uint32_t *)record)[12] = padding;
  struct engdrv_timing local;
  if (!tim)
    tim = &local;
  int rc = engdrv_exchange(e, cmd, n, resp, &rn, tim);
  if (!rc && (tim->status || rn != 64 + pad((size_t)d[7] * tout) ||
              rd(resp + 32) != (size_t)d[7] * tout || rd(resp + 36) != d[7] ||
              rd(resp + 40) != (unsigned)tout || rd(resp + 44) != (unsigned)v ||
              rd(resp + 48) != !d[6])) {
    errno = EPROTO;
    rc = -1;
  }
  if (!rc) {
    memcpy(out, resp + 64, (size_t)d[7] * tout);
    *ot = tout;
    *ov = v;
  }
  free(cmd);
  free(resp);
  return rc;
}
int engdrv_run_layer(engdrv *e, unsigned layer, const void *a, const void *b,
                     unsigned frames, unsigned valid, int sign, void *out,
                     size_t capacity, unsigned *ot, unsigned *ov,
                     struct engdrv_timing *tim) {
  if (layer >= e->count || !a || !out || !ot || !ov || !frames ||
      frames > 800 || valid > frames || sign < 0 || sign > 1)
    return bad();
  uint32_t *d = e->rec[layer];
  return layer_cmd(e, d, is_add(d) ? 0 : d[12], a, b, frames, valid, sign, out,
                   capacity, ot, ov, tim);
}
static void accumulate(struct engdrv_timing *sum, const struct engdrv_timing *t,
                       unsigned *commands) {
  if (!*commands)
    sum->submit_ns = t->submit_ns;
  sum->irq_ns = t->irq_ns;
  sum->ready_ns = t->ready_ns;
  sum->cycles += t->cycles;
  sum->starvation += t->starvation;
  sum->terms += t->terms;
  ++*commands;
}
/* Per-layer execution of record d when its allocated input or output length
 * exceeds the 800-frame RUN_LAYER limit: exact time tiling. Each convolution
 * slice carries its halo, is masked at the source valid length by the host
 * (the RTL mask is disabled by valid = slice length) and runs with padding 0;
 * unmasked outputs past the valid length match golden_int8 because the
 * reference also computes them from zeroed inputs. Add is elementwise. */
static int layer_tiled(engdrv *e, const uint32_t *d, const uint8_t *a,
                       const uint8_t *b, unsigned frames, unsigned valid,
                       int sign, uint8_t *out, unsigned tout,
                       struct engdrv_timing *sum, unsigned *commands,
                       uint64_t *exchange_ns) {
  unsigned ci = in_channels(d), co = d[7];
  int add = is_add(d);
  unsigned span = add ? 1 : d[11] * (d[9] - 1) + 1, step = add ? 1 : d[10];
  unsigned m = add ? 800 : (800 - span) / step + 1;
  if (m > 800)
    m = 800;
  uint8_t *x = malloc((size_t)ci * 800 * (add ? 2 : 1)),
          *y = malloc((size_t)co * m);
  int rc = -1;
  if (!x || !y)
    goto out;
  for (unsigned o0 = 0; o0 < tout; o0 += m) {
    unsigned mm = tout - o0 < m ? tout - o0 : m;
    unsigned len = add ? mm : (mm - 1) * step + span;
    long g0 = add ? (long)o0 : (long)o0 * step - (long)d[12];
    unsigned local_valid = len;
    if (add) {
      local_valid = valid > o0 ? (valid - o0 < mm ? valid - o0 : mm) : 0;
      for (unsigned c = 0; c < ci; c++) {
        memcpy(x + (size_t)c * len, a + (size_t)c * frames + o0, len);
        memcpy(x + (size_t)ci * len + (size_t)c * len,
               b + (size_t)c * frames + o0, len);
      }
    } else
      for (unsigned c = 0; c < ci; c++)
        for (unsigned j = 0; j < len; j++) {
          long g = g0 + (long)j;
          x[(size_t)c * len + j] =
              g >= 0 && g < (long)valid ? a[(size_t)c * frames + g] : 0;
        }
    struct engdrv_timing t;
    unsigned st, sv;
    if (layer_cmd(e, d, 0, x, add ? x + (size_t)ci * len : 0, len,
                  local_valid, sign, y, (size_t)co * mm, &st, &sv, &t))
      goto out;
    if (st != mm) {
      errno = EPROTO;
      goto out;
    }
    *exchange_ns += t.ready_ns - t.submit_ns;
    accumulate(sum, &t, commands);
    for (unsigned c = 0; c < co; c++)
      memcpy(out + (size_t)c * tout + o0, y + (size_t)c * mm, mm);
  }
  rc = 0;
out:
  free(x);
  free(y);
  return rc;
}
/* Allocated/valid lengths and channel checks for every tensor (0 = input). */
static int walk(const engdrv *e, unsigned frames, unsigned valid, unsigned *ts,
                unsigned *vs) {
  ts[0] = frames;
  vs[0] = valid;
  for (unsigned i = 0; i < e->count; i++) {
    uint32_t *d = e->rec[i];
    unsigned a = d[4], b = d[5];
    unsigned channels = a ? e->rec[a - 1][7] : 64;
    if (channels != in_channels(d) ||
        (is_add(d) && (!a || !b || ts[a] != ts[b] || vs[a] != vs[b] ||
                       e->rec[b - 1][7] != channels || e->rec[a - 1][6] ||
                       e->rec[b - 1][6])))
      return bad();
    int t = conv_len(d, is_add(d) ? 0 : d[12], (int)ts[a]),
        v = conv_len(d, is_add(d) ? 0 : d[12], (int)vs[a]);
    if (t < 1)
      return bad();
    ts[i + 1] = t;
    vs[i + 1] = v < 0 ? 0 : v;
  }
  if (e->rec[e->count - 1][7] != 29 || e->rec[e->count - 1][6] != 0)
    return bad();
  return 0;
}
/* Whole graph as one RUN_NETWORK packet: header + features, then the
 * transport's resident program. */
static int network_cmd(engdrv *e, const int8_t *features, unsigned frames,
                       unsigned valid, unsigned tout, unsigned vout,
                       struct engdrv_timing *tim) {
  size_t fb = pad((size_t)64 * frames), n = 64 + fb,
         rn = 64 + pad((size_t)29 * tout);
  if (!e->head &&
      !(e->head = malloc(64 + 64 * (size_t)ENGDRV_NETWORK_FRAMES)))
    return -1;
  uint8_t *resp = malloc(rn), *logits = malloc((size_t)29 * tout);
  if (!resp || !logits) {
    free(resp);
    free(logits);
    return -1;
  }
  uint32_t h[16] = {0x32434e51, 1, 2, ++e->tag, (uint32_t)(fb + e->size),
                    64, frames, valid, 29, 1, e->count, (uint32_t)e->size};
  memcpy(e->head, h, 64);
  memcpy(e->head + 64, features, (size_t)64 * frames);
  memset(e->head + 64 + (size_t)64 * frames, 0, fb - (size_t)64 * frames);
  int rc = -1;
  memset(tim, 0, sizeof(*tim));
  if (e->poisoned) {
    errno = EIO;
    goto out;
  }
  tim->submit_ns = plio_now_ns();
  if (e->tr->network(e->tr, e->head, n, resp, &rn, tim)) {
    int err = errno;
    engdrv_recover(e);
    errno = err;
    goto out;
  }
  tim->ready_ns = plio_now_ns();
  if (rn < 64 || rd(resp) != 0x32534e51 || rd(resp + 4) != 1 ||
      rd(resp + 12) != h[3]) {
    engdrv_recover(e);
    errno = EPROTO;
    goto out;
  }
  tim->status = rd(resp + 8);
  tim->cycles = rd(resp + 16) | (uint64_t)rd(resp + 20) << 32;
  tim->starvation = rd(resp + 24);
  tim->terms = rd(resp + 28);
  if (tim->status) {
    errno = EPROTO;
    goto out;
  }
  if (rn != 64 + pad((size_t)29 * tout) || rd(resp + 32) != 29 * tout ||
      rd(resp + 36) != 29 || rd(resp + 40) != tout || rd(resp + 44) != vout ||
      rd(resp + 48) != 1 || rd(resp + 52) != 2) {
    errno = EPROTO;
    goto out;
  }
  memcpy(logits, resp + 64, (size_t)29 * tout);
  free(e->logits);
  e->logits = (int8_t *)logits;
  logits = 0;
  rc = 0;
out:
  free(resp);
  free(logits);
  return rc;
}
int engdrv_set_mode(engdrv *e, int mode) {
  if (!e || mode < ENGDRV_AUTO || mode > ENGDRV_NETWORK)
    return bad();
  if (e->active) {
    errno = EBUSY;
    return -1;
  }
  e->mode = mode;
  return 0;
}
int engdrv_network_capable(const engdrv *e) { return e && e->network_ok; }
int engdrv_last_run(const engdrv *e, struct engdrv_run_info *info) {
  if (!e || !info || !e->timed)
    return bad();
  *info = e->info;
  return 0;
}
int engdrv_run_network(engdrv *e, const int8_t *features, unsigned frames,
                       unsigned valid, const int8_t **out, unsigned *ot,
                       unsigned *ov, struct engdrv_timing *tim) {
  if (!e->count || !features || !out || !ot || !ov || !frames ||
      frames > ENGDRV_MAX_FRAMES || valid > frames)
    return bad();
  unsigned *ts = calloc(e->count + 1, sizeof(*ts)),
           *vs = calloc(e->count + 1, sizeof(*vs));
  void **x = calloc(e->count + 1, sizeof(*x));
  struct engdrv_timing *lt = calloc(e->count, sizeof(*lt));
  struct engdrv_run_info info = {0};
  struct engdrv_timing total = {0}, t;
  int rc = -1;
  if (!x || !ts || !vs || !lt)
    goto done;
  if (walk(e, frames, valid, ts, vs))
    goto done;
  unsigned longest = 0;
  for (unsigned i = 1; i <= e->count; i++)
    if (ts[i] > longest)
      longest = ts[i];
  int fits = frames <= ENGDRV_NETWORK_FRAMES && longest <= 320;
  uint64_t sync0 = e->tr->sync_ns;
  unsigned calls0 = e->tr->sync_calls;
  uint64_t start = plio_now_ns();
  if (e->mode == ENGDRV_NETWORK && (!e->network_ok || !fits)) {
    errno = !e->network_ok ? e->network_errno : E2BIG;
    goto done;
  }
  if (e->mode != ENGDRV_LAYER && e->network_ok && fits) {
    if (network_cmd(e, features, frames, valid, ts[e->count], vs[e->count],
                    &total))
      goto done;
    lt[0] = total;
    info.network = 1;
    info.commands = 1;
    info.exchange_ns = total.ready_ns - total.submit_ns;
    e->timed = 1;
  } else {
    x[0] = (void *)features;
    for (unsigned i = 0; i < e->count; i++) {
      uint32_t *d = e->rec[i];
      unsigned a = d[4], b = d[5];
      int sign = a ? !e->rec[a - 1][6] : 1;
      x[i + 1] = malloc((size_t)d[7] * ts[i + 1]);
      if (!x[i + 1])
        goto done;
      struct engdrv_timing layer = {0};
      unsigned commands = 0;
      if (ts[a] > 800 || ts[i + 1] > 800) {
        if (layer_tiled(e, d, x[a], is_add(d) ? x[b] : 0, ts[a], vs[a], sign,
                        x[i + 1], ts[i + 1], &layer, &commands,
                        &info.exchange_ns))
          goto done;
      } else {
        unsigned st, sv;
        if (layer_cmd(e, d, is_add(d) ? 0 : d[12], x[a],
                      is_add(d) ? x[b] : 0, ts[a], vs[a], sign, x[i + 1],
                      (size_t)d[7] * ts[i + 1], &st, &sv, &t))
          goto done;
        if (st != ts[i + 1] || sv != vs[i + 1]) {
          errno = EPROTO;
          goto done;
        }
        info.exchange_ns += t.ready_ns - t.submit_ns;
        accumulate(&layer, &t, &commands);
      }
      lt[i] = layer;
      info.commands += commands;
      total.cycles += layer.cycles;
      total.starvation += layer.starvation;
      total.terms += layer.terms;
      total.irq_ns = layer.irq_ns;
    }
    free(e->logits);
    e->logits = x[e->count];
    x[e->count] = 0;
    e->timed = e->count;
  }
  total.submit_ns = start;
  total.ready_ns = plio_now_ns();
  info.wall_ns = total.ready_ns - start;
  info.outside_ns = info.wall_ns - info.exchange_ns;
  info.sync_ns = e->tr->sync_ns - sync0;
  info.sync_calls = e->tr->sync_calls - calls0;
  e->info = info;
  *out = e->logits;
  *ot = ts[e->count];
  *ov = vs[e->count];
  if (tim)
    *tim = total;
  free(e->layer_timing);
  e->layer_timing = lt;
  lt = 0;
  rc = 0;
done:
  if (rc)
    e->timed = 0;
  free(lt);
  if (x)
    for (unsigned i = 1; i <= e->count; i++)
      free(x[i]);
  free(x);
  free(ts);
  free(vs);
  return rc;
}
int engdrv_network_timing(const engdrv *e, unsigned layer,
                          struct engdrv_timing *t) {
  if (!e || !t || layer >= e->timed)
    return bad();
  *t = e->layer_timing[layer];
  return 0;
}
static void *worker(void *p) {
  engdrv *e = p;
  const int8_t *out;
  int r = engdrv_run_network(e, e->features, e->frames, e->valid, &out, &e->ot,
                             &e->ov, &e->timing);
  int err = errno;
  pthread_mutex_lock(&e->lock);
  e->error = r ? (err ? err : EIO) : 0;
  e->done = 1;
  pthread_cond_broadcast(&e->cond);
  pthread_mutex_unlock(&e->lock);
  return 0;
}
int engdrv_submit(engdrv *e, const int8_t *x, unsigned t, unsigned v) {
  if (!x || !t || t > ENGDRV_MAX_FRAMES || v > t)
    return bad();
  if (e->active) {
    errno = EBUSY;
    return -1;
  }
  int8_t *p = realloc(e->features, 64 * t);
  if (!p)
    return -1;
  e->features = p;
  memcpy(p, x, 64 * t);
  e->frames = t;
  e->valid = v;
  e->done = 0;
  e->active = 1;
  int err = pthread_create(&e->worker, 0, worker, e);
  if (err) {
    e->active = 0;
    errno = err;
    return -1;
  }
  return 0;
}
int engdrv_poll(engdrv *e, const int8_t **out, unsigned *t, unsigned *v,
                struct engdrv_timing *tim, int ms) {
  if (!e->active || !out || !t || !v)
    return bad();
  pthread_mutex_lock(&e->lock);
  if (!e->done && ms) {
    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += ms / 1000;
    deadline.tv_nsec += (ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
      deadline.tv_sec++;
      deadline.tv_nsec -= 1000000000L;
    }
    while (!e->done) {
      int r = ms < 0 ? pthread_cond_wait(&e->cond, &e->lock)
                     : pthread_cond_timedwait(&e->cond, &e->lock, &deadline);
      if (r)
        break;
    }
  }
  int done = e->done;
  pthread_mutex_unlock(&e->lock);
  if (!done)
    return 0;
  pthread_join(e->worker, 0);
  e->active = 0;
  if (e->error) {
    errno = e->error;
    return -1;
  }
  *out = e->logits;
  *t = e->ot;
  *v = e->ov;
  if (tim)
    *tim = e->timing;
  return 1;
}
