CC ?= gcc
CFLAGS = -O2 -std=c11 -Wall -Wextra -Werror
# Build products go to $(BUILD_ROOT)/sw/plio: the repository's build/ directory,
# or build/ beside this folder when it is copied without the repository.
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(notdir $(abspath $(HERE)/..)),sw)
BUILD_ROOT ?= $(abspath $(HERE)/../../build)
else
BUILD_ROOT ?= $(abspath $(HERE)/../build)
endif
B := $(BUILD_ROOT)/sw/plio
TESTS := loopback irq_soak buffers gpio range
all: libplio.a $(addprefix tests/,$(TESTS))
$(B)/plio.o: plio.c plio.h | $(B)/tests
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/libplio.a: $(B)/plio.o
	$(AR) rcs $@ $^
$(B)/tests/%: tests/%.c $(B)/libplio.a
	$(CC) $(CFLAGS) -I. $< $(B)/libplio.a -o $@
$(B)/tests:
	mkdir -p $@
libplio.a $(addprefix tests/,$(TESTS)): %: $(B)/%
clean:
	rm -rf $(B)
.PHONY: all clean libplio.a $(addprefix tests/,$(TESTS))
