CC ?= gcc
CXX ?= g++
CFLAGS ?= -O3 -g -std=c11 -fPIC -Wall -Wextra
# Build products go to $(BUILD_ROOT)/sw/engdrv: the repository's build/ directory,
# or build/ beside this folder when it is copied without the repository.
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(notdir $(abspath $(HERE)/..)),sw)
BUILD_ROOT ?= $(abspath $(HERE)/../../build)
else
BUILD_ROOT ?= $(abspath $(HERE)/../build)
endif
B := $(BUILD_ROOT)/sw/engdrv
# Override to build cosim against a snapshot, e.g. `make rtl-snapshot` then
# `make cosim RTL=$(B)/rtl/rtl/engine`.
RTL ?= ../../rtl/engine
SOURCES := $(RTL)/stt_engine_pkg.sv $(filter-out $(RTL)/stt_engine_pkg.sv,$(wildcard $(RTL)/*.sv))
COMMON = $(addprefix $(B)/,engdrv.o board.o plio.o backend_pl.o)
PY ?= $(abspath $(HERE)/../../.venv/bin/python)
SHARDS ?= 8
all: libengdrv.a
$(B)/libengdrv.a: $(COMMON) $(B)/no_cosim.o
	$(AR) rcs $@ $^
$(B)/%.o: %.c engdrv.h transport.h ../plio/plio.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/plio.o: ../plio/plio.c ../plio/plio.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/obj/Vstt_engine__ALL.a: $(SOURCES) cosim.cpp | $(B)
	verilator --cc --build -j 8 -CFLAGS "-O3 -fPIC" -Wall --top-module stt_engine --Mdir $(B)/obj $(SOURCES)
$(B)/cosim.o: cosim.cpp transport.h $(B)/obj/Vstt_engine__ALL.a
	$(CXX) -fPIC -O3 -std=c++17 -I$(B)/obj -I/usr/share/verilator/include -I/usr/share/verilator/include/vltstd -c $< -o $@
$(B)/libengdrv_cosim.so: $(COMMON) $(B)/cosim.o $(B)/obj/Vstt_engine__ALL.a
	$(CXX) -fPIC -shared -o $@ $^ /usr/share/verilator/include/verilated.cpp /usr/share/verilator/include/verilated_threads.cpp -I/usr/share/verilator/include -pthread
cosim:
	$(MAKE) CFLAGS='$(CFLAGS) -fPIC' $(B)/libengdrv_cosim.so
# Committed engine RTL, isolated from in-progress working-tree edits.
rtl-snapshot: | $(B)
	rm -rf $(B)/rtl && mkdir -p $(B)/rtl
	git -C ../.. archive HEAD rtl/engine | tar -x -C $(B)/rtl
	git -C ../.. rev-parse HEAD > $(B)/rtl/COMMIT
$(B)/i2: $(B)/i2.o $(B)/libengdrv.a ../qnrt/qnrt.c
	$(CC) $(CFLAGS) -o $@ $(B)/i2.o $(B)/libengdrv.a ../qnrt/qnrt.c -pthread -lm
$(B)/libqnrt.so: ../qnrt/qnrt.c | $(B)
	$(CC) -O3 -fPIC -shared $< -pthread -lm -o $@
# Full suite: misc checks, all manifest vectors in parallel shards and the
# T_in=48 real-window network. Long windows: see docs/results/engdrv.md.
check: $(B)/libengdrv_cosim.so $(B)/libqnrt.so
	mkdir -p $(B)/measurements
	cd ../.. && BUILD_ROOT=$(BUILD_ROOT) $(PY) sw/engdrv/tools/test_cosim.py --misc --network clip0:0:48:48
	cd ../.. && for k in $$(seq 0 $$(($(SHARDS)-1))); do \
	  BUILD_ROOT=$(BUILD_ROOT) $(PY) sw/engdrv/tools/test_cosim.py --vectors $$k/$(SHARDS) > $(B)/measurements/vectors_$$k.log 2>&1 & \
	done; fail=0; for j in $$(jobs -p); do wait $$j || fail=1; done; \
	grep -h '^PASS' $(B)/measurements/vectors_*.log; exit $$fail
$(B):
	mkdir -p $@
libengdrv.a libengdrv_cosim.so libqnrt.so i2: %: $(B)/%
clean:
	rm -rf $(B)
.PHONY: all clean cosim check rtl-snapshot libengdrv.a libengdrv_cosim.so libqnrt.so i2
