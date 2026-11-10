CC ?= gcc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra
LDLIBS = -pthread -lm
# Build products go to $(BUILD_ROOT)/sw/qnrt: the repository's build/ directory,
# or build/ beside this folder when it is copied without the repository.
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(notdir $(abspath $(HERE)/..)),sw)
BUILD_ROOT ?= $(abspath $(HERE)/../../build)
else
BUILD_ROOT ?= $(abspath $(HERE)/../build)
endif
B := $(BUILD_ROOT)/sw/qnrt
LINKED := qnrt_test qnrt_transcribe qnrt_stream_test qnrt_bench load_test network_dump peak
PROGRAMS := $(LINKED) qnrt_test_basic qnrt_test_scalar
all: libqnrt.a qnrt_test qnrt_transcribe
$(B)/%.o: %.c | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/frontend.o: ../frontend_c/frontend.c | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/libqnrt.a: $(B)/qnrt.o
	$(AR) rcs $@ $^
$(B)/qnrt_test: $(B)/test.o $(B)/libqnrt.a
$(B)/qnrt_transcribe: $(B)/transcribe.o $(B)/wav.o $(B)/frontend.o $(B)/libqnrt.a
$(B)/qnrt_stream_test: $(B)/stream_test.o $(B)/wav.o $(B)/frontend.o $(B)/libqnrt.a
$(B)/qnrt_bench: $(B)/bench.o $(B)/libqnrt.a
$(B)/load_test: $(B)/load_test.o $(B)/libqnrt.a
$(B)/network_dump: $(B)/network_dump.o $(B)/libqnrt.a
$(B)/peak: $(B)/peak.o
$(addprefix $(B)/,$(LINKED)):
	$(CC) $(CFLAGS) -o $@ $^ $(LDLIBS)
$(B)/qnrt_test_basic: test.c qnrt.c | $(B)
	$(CC) $(CFLAGS) -DQNRT_BASIC -o $@ $^ $(LDLIBS)
$(B)/qnrt_test_scalar: test.c qnrt.c | $(B)
	$(CC) $(CFLAGS) -DQNRT_SCALAR -o $@ $^ $(LDLIBS)
$(B):
	mkdir -p $@
libqnrt.a $(PROGRAMS): %: $(B)/%
clean:
	rm -rf $(B)
.PHONY: all clean libqnrt.a $(PROGRAMS)
