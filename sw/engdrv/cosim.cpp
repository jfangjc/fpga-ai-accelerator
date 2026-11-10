#include "transport.h"
extern "C" {
#include "../plio/plio.h"
}
#include "Vstt_engine.h"
#include "verilated.h"
#include <cerrno>
#include <cstring>
#include <vector>
struct sim {
  VerilatedContext context;
  Vstt_engine d{&context};
  int ms;
  std::vector<uint8_t> program;
};
static void tick(sim *s) {
  s->d.aclk = 0;
  s->d.eval();
  s->d.aclk = 1;
  s->d.eval();
}
static int write(sim *s, unsigned addr, unsigned val) {
  auto &d = s->d;
  d.s_axil_awaddr = addr;
  d.s_axil_wdata = val;
  d.s_axil_wstrb = 15;
  bool aw = false, w = false;
  for (int i = 0; i < 100; i++) {
    d.aclk = 0;
    d.s_axil_awvalid = !aw;
    d.s_axil_wvalid = !w;
    d.eval();
    bool a = d.s_axil_awvalid && d.s_axil_awready,
         b = d.s_axil_wvalid && d.s_axil_wready, done = d.s_axil_bvalid;
    tick(s);
    aw |= a;
    w |= b;
    if (done) {
      d.s_axil_awvalid = d.s_axil_wvalid = 0;
      return 0;
    }
  }
  errno = ETIMEDOUT;
  return -1;
}
static uint32_t version(transport *t) {
  auto s = (sim *)t->state;
  auto &d = s->d;
  d.s_axil_araddr = 8;
  d.s_axil_arvalid = 1;
  for (int i = 0; i < 100; i++) {
    d.aclk = 0;
    d.eval();
    bool a = d.s_axil_arready, r = d.s_axil_rvalid;
    uint32_t v = d.s_axil_rdata;
    tick(s);
    if (a)
      d.s_axil_arvalid = 0;
    if (r)
      return v;
  }
  return 0;
}
static int reset(transport *t) {
  auto s = (sim *)t->state;
  s->d.s_axis_tvalid = 0;
  s->d.m_axis_tready = 0;
  int r = write(s, 0, 1);
  for (int i = 0; i < 8; i++)
    tick(s);
  return r;
}
/* Streams segment p (n bytes) then q (m bytes) as one AXIS packet. */
static int stream(transport *t, const void *p, size_t n, const void *q,
                  size_t m, void *r, size_t *cap, engdrv_timing *tim) {
  auto s = (sim *)t->state;
  auto &d = s->d;
  size_t sent = 0, got = 0;
  const size_t total = n + m;
  uint64_t deadline = plio_now_ns() + uint64_t(s->ms) * 1000000;
  write(s, 0x10, 3);
  for (uint64_t c = 0;; c++) {
    if (!(c & 4095) && plio_now_ns() >= deadline) {
      errno = ETIMEDOUT;
      return -1;
    }
    d.aclk = 0;
    d.s_axis_tvalid = sent < total;
    d.s_axis_tlast = sent + 16 == total;
    d.s_axis_tkeep = 65535;
    const char *beat = sent < n ? (const char *)p + sent
                                : (const char *)q + (sent - n);
    for (int i = 0; i < 4; i++) {
      uint32_t w = 0;
      if (sent < total)
        memcpy(&w, beat + 4 * i, 4);
      d.s_axis_tdata[i] = w;
    }
    d.m_axis_tready = 1;
    d.eval();
    bool sf = d.s_axis_tvalid && d.s_axis_tready, rf = d.m_axis_tvalid,
         done = rf && d.m_axis_tlast;
    if (rf) {
      if (d.m_axis_tkeep != 65535 || got + 16 > *cap) {
        errno = EPROTO;
        return -1;
      }
      for (int i = 0; i < 4; i++) {
        uint32_t w = d.m_axis_tdata[i];
        memcpy((char *)r + got + 4 * i, &w, 4);
      }
      got += 16;
    }
    d.aclk = 1;
    d.eval();
    if (sf)
      sent += 16;
    if (done) {
      d.s_axis_tvalid = 0;
      for (int i = 0; i < 8 && !d.irq; i++)
        tick(s);
      if (!d.irq || sent != total) {
        errno = EPROTO;
        return -1;
      }
      tim->irq_ns = plio_now_ns();
      *cap = got;
      write(s, 0x10, 3);
      for (int i = 0; i < 3; i++)
        tick(s);
      return 0;
    }
  }
}
static int exchange(transport *t, const void *p, size_t n, void *r, size_t *cap,
                    engdrv_timing *tim) {
  return stream(t, p, n, nullptr, 0, r, cap, tim);
}
static int program(transport *t, const void *p, size_t n) {
  auto s = (sim *)t->state;
  if (n % 16) {
    errno = EINVAL;
    return -1;
  }
  s->program.assign((const uint8_t *)p, (const uint8_t *)p + n);
  return 0;
}
static int network(transport *t, const void *p, size_t n, void *r, size_t *cap,
                   engdrv_timing *tim) {
  auto s = (sim *)t->state;
  if (s->program.empty()) {
    errno = ENOTSUP;
    return -1;
  }
  return stream(t, p, n, s->program.data(), s->program.size(), r, cap, tim);
}
static void destroy(transport *t) {
  delete (sim *)t->state;
  delete t;
}
extern "C" int cosim_open(transport **out, const engdrv_config *cfg) {
  auto t = new transport{};
  auto s = new sim;
  t->state = s;
  t->exchange = exchange;
  t->version = version;
  t->reset = reset;
  t->close = destroy;
  t->program = program;
  t->network = network;
  s->ms = cfg->timeout_ms;
  s->d.aresetn = 0;
  s->d.s_axil_bready = s->d.s_axil_rready = 1;
  for (int i = 0; i < 8; i++)
    tick(s);
  s->d.aresetn = 1;
  for (int i = 0; i < 8; i++)
    tick(s);
  write(s, 0xc, 3);
  *out = t;
  return 0;
}
