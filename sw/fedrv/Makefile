CC ?= cc
# fp-contract=off keeps the float log/normalise port operation-for-operation with NumPy.
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Werror -ffp-contract=off
# Build products go to $(BUILD_ROOT)/sw/fedrv: the repository's build/ directory,
# or build/ beside this folder when it is copied without the repository.
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(notdir $(abspath $(HERE)/..)),sw)
BUILD_ROOT ?= $(abspath $(HERE)/../../build)
else
BUILD_ROOT ?= $(abspath $(HERE)/../build)
endif
B := $(BUILD_ROOT)/sw/fedrv
LIB = $(addprefix $(B)/,fedrv.o stream.o features.o plio.o)
all: fetool libfedrv.a
$(B)/plio.o: ../plio/plio.c ../plio/plio.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/%.o: %.c fedrv.h stream.h features.h ../plio/plio.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/libfedrv.a: $(LIB)
	ar rcs $@ $^
$(B)/fetool: $(B)/fetool.o $(B)/libfedrv.a
	$(CC) $(CFLAGS) $^ -pthread -lm -o $@
$(B)/test_tail: tests/test_tail.c fedrv.c | $(B)
	$(CC) $(CFLAGS) -ffunction-sections $< -Wl,--gc-sections -o $@
$(B)/test_ui: tests/test_ui.c fedrv.c | $(B)
	$(CC) $(CFLAGS) -ffunction-sections $< -Wl,--gc-sections -o $@
check-tail: $(B)/test_tail
	$(B)/test_tail
check-ui: $(B)/test_ui
	$(B)/test_ui
$(B):
	mkdir -p $@
fetool libfedrv.a: %: $(B)/%
clean:
	rm -rf $(B)
.PHONY: all clean check-tail check-ui fetool libfedrv.a
