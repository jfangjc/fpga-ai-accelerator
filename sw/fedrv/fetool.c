/* stt-fe board tool: I1 clip replay, paced soak, mic self-test, LED/button.
 *   fetool clip [-p] [-n irq_frames] [-c chunk] [-g gap_ms] [-s scale] [-P prior.bin] in.pcm out.bin
 *   fetool soak [-n irq_frames] -t seconds ref.bin loop.pcm
 *   fetool mic [-t seconds] [-m mic1|mic2|beam] [-r] [-o frames.bin]  (64 LE uint64 powers per frame)
 *   fetool led on|off      fetool button [timeout_s]
 * PCM is raw little-endian int16 at 16 kHz; samples are promoted by 4. */
#define _POSIX_C_SOURCE 200809L
#include "features.h"
#include "stream.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void die(const char *what) {
  perror(what);
  exit(1);
}
static int32_t *load_pcm(const char *path, size_t *n) {
  FILE *f = fopen(path, "rb");
  if (!f) die(path);
  fseek(f, 0, SEEK_END);
  long bytes = ftell(f);
  fseek(f, 0, SEEK_SET);
  *n = (size_t)bytes / 2;
  int16_t *raw = malloc(*n * 2 + 2);
  int32_t *x = malloc(*n * 4 + 4);
  if (!raw || !x || fread(raw, 2, *n, f) != *n) die("read pcm");
  fclose(f);
  for (size_t i = 0; i < *n; i++) x[i] = (int32_t)raw[i] * 4;
  free(raw);
  return x;
}
/* PL clock as configured by the overlay (falls back to nominal). */
static double pl_hz(void) {
  FILE *f = fopen("/sys/kernel/debug/clk/pl0_ref/clk_rate", "r");
  double hz = 100e6;
  if (f) {
    if (fscanf(f, "%lf", &hz) != 1) hz = 100e6;
    fclose(f);
  }
  return hz;
}

struct source { const int32_t *x; size_t n, pos, chunk; int gap_ms, loop; uint64_t deadline; };
static size_t fill(void *ctx, int32_t *dst, size_t max) {
  struct source *s = ctx;
  if (s->loop) {
    if (plio_now_ns() >= s->deadline) return 0;
  } else if (s->pos >= s->n)
    return 0;
  if (s->gap_ms && s->pos) {
    struct timespec t = {s->gap_ms / 1000, (s->gap_ms % 1000) * 1000000L};
    nanosleep(&t, NULL);
  }
  size_t m = max < s->chunk ? max : s->chunk, k = 0;
  while (k < m) {
    if (s->pos == s->n) {
      if (!s->loop) break;
      s->pos = 0;
    }
    size_t c = m - k < s->n - s->pos ? m - k : s->n - s->pos;
    memcpy(dst + k, s->x + s->pos, c * 4);
    k += c;
    s->pos += c;
  }
  return k;
}

/* Streaming interval/latency statistics with a 10 us histogram to 100 ms. */
struct hist { uint64_t n, bins[10001]; double sum, sq, lo, hi; };
static void hist_add(struct hist *h, double us) {
  if (!h->n || us < h->lo) h->lo = us;
  if (!h->n || us > h->hi) h->hi = us;
  h->n++;
  h->sum += us;
  h->sq += us * us;
  long b = lround(us / 10);
  h->bins[b < 0 ? 0 : b > 10000 ? 10000 : b]++;
}
static double hist_pct(const struct hist *h, double p) {
  uint64_t want = (uint64_t)ceil(p * (double)h->n), c = 0;
  for (int b = 0; b <= 10000; b++)
    if ((c += h->bins[b]) >= want) return b * 10.0;
  return h->hi;
}
static void hist_print(const char *name, const struct hist *h) {
  if (!h->n) return;
  double mean = h->sum / (double)h->n, sd = sqrt(fmax(0, h->sq / (double)h->n - mean * mean));
  printf("%s_us n=%llu mean=%.3f std=%.3f min=%.3f p50=%.0f p99=%.0f p999=%.0f max=%.3f\n", name,
         (unsigned long long)h->n, mean, sd, h->lo, hist_pct(h, .5), hist_pct(h, .99),
         hist_pct(h, .999), h->hi);
}

static void print_stats(struct fe *f, struct fe_stream *s) {
  printf("stats frames=%llu irqs=%llu sequence_errors=%llu ring_overruns=%llu consumer_drops=%llu "
         "tx_underruns=%llu mic_overruns=%u status=0x%x\n",
         (unsigned long long)f->stats.frames, (unsigned long long)f->stats.irqs,
         (unsigned long long)f->stats.sequence_errors, (unsigned long long)f->stats.ring_overruns,
         (unsigned long long)atomic_load(&s->dropped), (unsigned long long)f->stats.tx_underruns,
         f->stats.mic_overruns, f->stats.status);
}

struct record { uint32_t sequence, pad; uint64_t power[64], irq_ns, avail_ns, pop_ns; };

static int cmd_clip(int argc, char **argv) {
  struct fe_config cfg = {.source = FE_SRC_INJECT, .irq_frames = 1, .gain = 16384};
  size_t chunk = FE_TX_CHUNK;
  int gap = 0, opt;
  float scale = 0.036928060486560735f;
  const char *prior = NULL;
  while ((opt = getopt(argc, argv, "pn:c:g:s:P:")) != -1) {
    if (opt == 'p') cfg.paced = 1;
    else if (opt == 'n') cfg.irq_frames = (unsigned)atoi(optarg);
    else if (opt == 'c') chunk = (size_t)atol(optarg);
    else if (opt == 'g') gap = atoi(optarg);
    else if (opt == 's') scale = strtof(optarg, NULL);
    else if (opt == 'P') prior = optarg;
    else return 2;
  }
  if (argc - optind != 2 || !chunk || chunk > FE_TX_CHUNK) return 2;
  size_t n;
  int32_t *x = load_pcm(argv[optind], &n);
  uint64_t T = fe_frames_for(n);
  struct record *r = calloc(T + 1, sizeof(*r));
  static struct fe f;
  static struct fe_stream s;
  if (!r || T < 2) die("clip too short");
  if (fe_open(&f) || fe_configure(&f, &cfg)) die("fe_open");
  struct source src = {.x = x, .n = n, .chunk = chunk, .gap_ms = gap};
  uint64_t t0 = plio_now_ns();
  if (fe_stream_start(&s, &f, fill, &src)) die("fe_stream_start");
  uint64_t got = 0;
  struct fe_frame fr;
  int rc;
  while ((rc = fe_stream_next(&s, &fr, 5000)) == 1) {
    uint64_t pop = plio_now_ns();
    if (got < T) {
      r[got].sequence = fr.sequence;
      memcpy(r[got].power, fr.power, sizeof(fr.power));
      r[got].irq_ns = fr.irq_ns;
      r[got].avail_ns = fr.avail_ns;
      r[got].pop_ns = pop;
    }
    got++;
  }
  int err = fe_stream_join(&s);
  uint64_t t1 = plio_now_ns(), start = f.tx_start_ns;
  printf("clip %s samples=%zu frames=%llu expected=%llu paced=%d irq_frames=%u chunk=%zu gap_ms=%d "
         "wall_s=%.6f rc=%d err=%d\n", argv[optind], n, (unsigned long long)got,
         (unsigned long long)T, cfg.paced, cfg.irq_frames, chunk, gap, (t1 - t0) * 1e-9, rc, err);
  print_stats(&f, &s);
  uint32_t seq_bad = 0;
  for (uint64_t k = 0; k < got && k < T; k++) seq_bad += r[k].sequence != k;
  double hz = pl_hz(), sample_ns = FE_SAMPLE_CLOCKS * 1e9 / hz;
  struct hist jit = {0}, lat = {0}, latpop = {0};
  for (uint64_t k = 0; k < got && k < T; k++) {
    if (k) hist_add(&jit, (r[k].irq_ns - r[k - 1].irq_ns) * 1e-3);
    if (cfg.paced) {
      double last = (double)start + (160.0 * (double)k + 319) * sample_ns;
      hist_add(&lat, ((double)r[k].avail_ns - last) * 1e-3);
      hist_add(&latpop, ((double)r[k].pop_ns - last) * 1e-3);
    }
  }
  printf("sequence_mismatches=%u pl_hz=%.0f\n", seq_bad, hz);
  hist_print("irq_interval", &jit);
  hist_print("latency_avail", &lat);
  hist_print("latency_consumer", &latpop);
  for (uint64_t k = 0; k < got && k < T; k++) {
    r[k].irq_ns -= start;
    r[k].avail_ns -= start;
    r[k].pop_ns -= start;
  }
  /* PS features: utterance normalisation and running (optional prior). */
  float *zu = malloc(T * 64 * 4), *zr = malloc(T * 64 * 4), z[64];
  int8_t *qu = malloc(T * 64), *qr = malloc(T * 64);
  if (!zu || !zr || !qu || !qr) die("malloc");
  for (uint64_t k = 0; k < T; k++) {
    fe_log_frame(r[k].power, z);
    for (int b = 0; b < 64; b++) zu[b * T + k] = zr[b * T + k] = z[b];
  }
  struct fe_norm_state st = {0};
  if (prior) {
    FILE *p = fopen(prior, "rb");
    double v[129];
    if (!p || fread(v, 8, 129, p) != 129) die(prior);
    fclose(p);
    st.count = (long)v[0];
    memcpy(st.mean, v + 1, 64 * 8);
    memcpy(st.m2, v + 65, 64 * 8);
  }
  uint64_t tf = plio_now_ns();
  if (fe_normalize_utterance(zu, T)) die("normalize");
  fe_normalize_running(zr, T, T, &st);
  fe_quantize(zu, T * 64, scale, qu);
  fe_quantize(zr, T * 64, scale, qr);
  printf("ps_features_ms=%.3f (utterance+running+quantize, %llu frames)\n", (plio_now_ns() - tf) * 1e-6,
         (unsigned long long)T);
  FILE *o = fopen(argv[optind + 1], "wb");
  uint64_t hdr[8] = {0x31494546, T, n, (uint64_t)cfg.paced, cfg.irq_frames, got, (uint64_t)hz, 0};
  if (!o || fwrite(hdr, 8, 8, o) != 8 || fwrite(r, sizeof(*r), T, o) != T ||
      fwrite(qu, 1, T * 64, o) != T * 64 || fwrite(qr, 1, T * 64, o) != T * 64 ||
      fwrite(zu, 4, T * 64, o) != T * 64 || fwrite(zr, 4, T * 64, o) != T * 64 || fclose(o))
    die(argv[optind + 1]);
  int ok = !err && rc == 0 && got == T && !seq_bad && !f.stats.ring_overruns &&
           !atomic_load(&s.dropped) && !f.stats.sequence_errors;
  printf("%s\n", ok ? "CLIP PASS (counts/sequence; bit-exactness is checked on the host)" : "CLIP FAIL");
  fe_close(&f);
  return ok ? 0 : 1;
}

/* Continuous paced replay of a loop whose length is a multiple of 160 samples
 * and ends in silence, so frame k must equal frame k mod P bit-for-bit. The
 * first period is also written for host comparison with model/frontend_fixed. */
static int cmd_soak(int argc, char **argv) {
  struct fe_config cfg = {.source = FE_SRC_INJECT, .paced = 1, .irq_frames = 1, .gain = 16384};
  double seconds = 1800;
  int opt;
  while ((opt = getopt(argc, argv, "n:t:b")) != -1) {
    if (opt == 'n') cfg.irq_frames = (unsigned)atoi(optarg);
    else if (opt == 't') seconds = atof(optarg);
    else if (opt == 'b') cfg.paced = 0;
    else return 2;
  }
  if (argc - optind != 2) return 2;
  size_t n;
  int32_t *x = load_pcm(argv[optind + 1], &n);
  if (n % 160 || n < 640 || x[n - 1]) {
    fprintf(stderr, "loop must be a multiple of 160 samples ending in silence\n");
    return 2;
  }
  size_t P = n / 160;
  uint64_t (*ref)[64] = malloc(P * sizeof(*ref));
  static struct fe f;
  static struct fe_stream s;
  if (!ref || fe_open(&f) || fe_configure(&f, &cfg)) die("fe_open");
  struct source src = {.x = x, .n = n, .chunk = FE_TX_CHUNK, .loop = 1,
                       .deadline = plio_now_ns() + (uint64_t)(seconds * 1e9)};
  double hz = pl_hz(), sample_ns = FE_SAMPLE_CLOCKS * 1e9 / hz;
  if (fe_stream_start(&s, &f, fill, &src)) die("fe_stream_start");
  struct hist *jit = calloc(1, sizeof(*jit)), *lat = calloc(1, sizeof(*lat));
  uint64_t got = 0, mismatched = 0, prev_irq = 0, next_report = 60, t0 = plio_now_ns();
  struct fe_frame fr;
  int rc;
  while ((rc = fe_stream_next(&s, &fr, 5000)) == 1) {
    uint64_t k = got++;
    if (k < P) memcpy(ref[k], fr.power, sizeof(fr.power));
    else mismatched += memcmp(ref[k % P], fr.power, sizeof(fr.power)) != 0;
    if (k) hist_add(jit, (fr.irq_ns - prev_irq) * 1e-3);
    prev_irq = fr.irq_ns;
    if (cfg.paced) hist_add(lat, ((double)fr.avail_ns - ((double)f.tx_start_ns + (160.0 * (double)k + 319) * sample_ns)) * 1e-3);
    if (k == P - 1) {
      FILE *o = fopen(argv[optind], "wb");
      if (!o || fwrite(ref, sizeof(*ref), P, o) != P || fclose(o)) die(argv[optind]);
    }
    double el = (plio_now_ns() - t0) * 1e-9;
    if (el >= (double)next_report) {
      printf("t=%.0f frames=%llu mismatched=%llu ring_overruns=%llu drops=%llu tx_underruns=%llu "
             "seq_errors=%llu max_interval_us=%.0f max_latency_us=%.0f\n", el,
             (unsigned long long)got, (unsigned long long)mismatched,
             (unsigned long long)f.stats.ring_overruns, (unsigned long long)atomic_load(&s.dropped),
             (unsigned long long)f.stats.tx_underruns, (unsigned long long)f.stats.sequence_errors,
             jit->hi, lat->hi);
      fflush(stdout);
      next_report += 60;
    }
  }
  int err = fe_stream_join(&s);
  uint64_t expect = fe_frames_for(atomic_load(&f.tx_submitted));
  double wall = (plio_now_ns() - t0) * 1e-9;
  printf("soak seconds=%.3f samples=%llu frames=%llu expected=%llu period_frames=%zu loops=%.2f "
         "mismatched_frames=%llu paced=%d irq_frames=%u rc=%d err=%d pl_hz=%.0f\n", wall,
         (unsigned long long)atomic_load(&f.tx_submitted), (unsigned long long)got,
         (unsigned long long)expect, P, (double)got / (double)P, (unsigned long long)mismatched,
         cfg.paced, cfg.irq_frames, rc, err, hz);
  print_stats(&f, &s);
  hist_print("irq_interval", jit);
  hist_print("latency_avail", lat);
  int ok = !err && rc == 0 && got == expect && got > P && !mismatched && !f.stats.ring_overruns &&
           !atomic_load(&s.dropped) && !f.stats.sequence_errors && !f.stats.tx_underruns;
  printf("%s\n", ok ? "SOAK PASS" : "SOAK FAIL");
  fe_close(&f);
  return ok ? 0 : 1;
}

static int cmd_mic(int argc, char **argv) {
  struct fe_config cfg = {.source = FE_SRC_MIC1, .irq_frames = 1, .gain = 16384};
  double seconds = 2;
  const char *dump = NULL;
  int opt;
  while ((opt = getopt(argc, argv, "t:m:ro:")) != -1) {
    if (opt == 't') seconds = atof(optarg);
    else if (opt == 'o') dump = optarg;
    else if (opt == 'm') cfg.source = !strcmp(optarg, "mic2") ? FE_SRC_MIC2 : !strcmp(optarg, "beam") ? FE_SRC_BEAM : FE_SRC_MIC1;
    else if (opt == 'r') cfg.right_channel = 1;
    else return 2;
  }
  static struct fe f;
  static struct fe_stream s;
  FILE *out = dump ? fopen(dump, "wb") : NULL;
  if (dump && !out) die(dump);
  if (fe_open(&f) || fe_configure(&f, &cfg) || fe_stream_start(&s, &f, NULL, NULL)) die("fe");
  struct fe_frame fr;
  uint64_t got = 0, first = 0, last = 0;
  uint64_t end = plio_now_ns() + (uint64_t)(seconds * 1e9);
  while (plio_now_ns() < end) {
    int rc = fe_stream_next(&s, &fr, 1000);
    if (rc == 1) {
      if (out && fwrite(fr.power, sizeof(fr.power), 1, out) != 1) die(dump);
      if (!got++) first = fr.irq_ns;
      last = fr.irq_ns;
    } else if (rc != -2)
      break;
  }
  fe_stream_stop(&s);
  int err = fe_stream_join(&s);
  if (out && fclose(out)) die(dump);
  double rate = got > 1 ? (double)(got - 1) / ((last - first) * 1e-9) : 0;
  printf("mic source=%d right=%d frames=%llu frame_rate_hz=%.4f (I2S WS clock error %.3f%%) err=%d\n",
         cfg.source, cfg.right_channel, (unsigned long long)got, rate, (rate / 100 - 1) * 100, err);
  print_stats(&f, &s);
  for (unsigned m = 1; m <= 2; m++) {
    struct fe_mic st;
    fe_mic_read(&f, m, &st);
    printf("mic%u peak=%u dc=%d mean_square=%llu\n", m, st.peak, st.dc, (unsigned long long)st.mean_square);
  }
  int ok = !err && fabs(rate / 100 - 1) < 0.01;
  printf("%s\n", ok ? "MIC CLOCK PASS (within 1%)" : "MIC CLOCK FAIL");
  fe_close(&f);
  return ok ? 0 : 1;
}

int main(int argc, char **argv) {
  if (argc < 2) goto usage;
  const char *cmd = argv[1];
  argv++;
  argc--;
  if (!strcmp(cmd, "clip")) return cmd_clip(argc, argv);
  if (!strcmp(cmd, "soak")) return cmd_soak(argc, argv);
  if (!strcmp(cmd, "mic")) return cmd_mic(argc, argv);
  if (!strcmp(cmd, "led") && argc == 2) {
    static struct fe f;
    if (fe_open(&f)) die("fe_open");
    fe_led(&f, !strcmp(argv[1], "on"));
    printf("led %s (status 0x%x)\n", argv[1], fe_status(&f));
    plio_uio_close(&f.regs); /* keep the LED: skip fe_close's stop/reset path */
    return 0;
  }
  if (!strcmp(cmd, "button")) {
    static struct fe f;
    struct fe_config cfg = {.source = FE_SRC_INJECT, .irq_frames = 1, .button_irq = 1, .gain = 16384};
    if (fe_open(&f) || fe_configure(&f, &cfg)) die("fe_open");
    int r = fe_button_wait(&f, (argc > 1 ? atoi(argv[1]) : 10) * 1000);
    printf("button %s\n", r == 1 ? "pressed" : r == 0 ? "timeout" : "error");
    fe_close(&f);
    return r == 1 ? 0 : 1;
  }
usage:
  fprintf(stderr, "usage: fetool clip|soak|mic|led|button ... (see source header)\n");
  return 2;
}
