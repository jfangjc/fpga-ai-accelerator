CC ?= gcc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Werror -ffp-contract=off
LDLIBS = -pthread -lm
# Build products go to $(BUILD_ROOT)/sw/app: the repository's build/ directory,
# or build/ beside this folder when it is copied without the repository.
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(notdir $(abspath $(HERE)/..)),sw)
BUILD_ROOT ?= $(abspath $(HERE)/../../build)
else
BUILD_ROOT ?= $(abspath $(HERE)/../build)
endif
B := $(BUILD_ROOT)/sw/app
ifeq ($(CPU_ONLY),1)
DEFS += -DCPU_ONLY
else
PL_SOURCES = ../fedrv/fedrv.c ../fedrv/stream.c ../fedrv/features.c ../plio/plio.c
endif
# ENGDRV=1 (default on PL builds) links the PL inference backend: the
# backend.c stub is excluded by HAVE_ENGDRV and sw/engdrv/backend_pl.c plus
# libengdrv.a (driver; its plio.o is not pulled because plio.c is linked
# directly) provide backend_pl. ENGDRV=0 keeps the ENOSYS stub.
ENGDRV ?= 1
ifneq ($(CPU_ONLY),1)
ifeq ($(ENGDRV),1)
DEFS += -DHAVE_ENGDRV
PL_SOURCES += ../engdrv/backend_pl.c
ENGDRV_LIB = $(BUILD_ROOT)/sw/engdrv/libengdrv.a
endif
endif
TESTS := test_stream test_clock test_caption test_emit
all: sttcap
policy.h policy.json: gen_policy.py ../../model/stream_policy.py
	python3 gen_policy.py
$(B)/sttcap: main.c caption.h clock.h backend.c backend.h stream.c display.c policy.h ../qnrt/qnrt.c ../qnrt/wav.c ../frontend_c/frontend.c $(PL_SOURCES) $(ENGDRV_LIB) | $(B)
	$(CC) $(CFLAGS) $(DEFS) main.c backend.c stream.c display.c ../qnrt/qnrt.c ../qnrt/wav.c ../frontend_c/frontend.c $(PL_SOURCES) $(ENGDRV_LIB) $(LDLIBS) -o $@
$(BUILD_ROOT)/sw/engdrv/libengdrv.a: FORCE
	$(MAKE) -C ../engdrv CC='$(CC)' BUILD_ROOT='$(BUILD_ROOT)' libengdrv.a
FORCE:
$(B)/test_stream: test_stream.c stream.c policy.h | $(B)
	$(CC) $(CFLAGS) test_stream.c stream.c -o $@
$(B)/test_clock: test_clock.c clock.h | $(B)
	$(CC) $(CFLAGS) test_clock.c -lm -o $@
$(B)/test_caption: test_caption.c caption.h | $(B)
	$(CC) $(CFLAGS) test_caption.c -o $@
$(B)/test_emit: test_emit.c main.c caption.h stream.c display.c backend.c clock.h | $(B)
	$(CC) $(CFLAGS) -DCPU_ONLY test_emit.c backend.c stream.c display.c ../qnrt/qnrt.c ../qnrt/wav.c ../frontend_c/frontend.c $(LDLIBS) -o $@
check: $(addprefix $(B)/,$(TESTS))
	$(B)/test_stream
	$(B)/test_clock
	$(B)/test_caption
	$(B)/test_emit
$(B):
	mkdir -p $@
sttcap $(TESTS): %: $(B)/%
clean:
	rm -rf $(B)
.PHONY: all check clean FORCE sttcap $(TESTS)
