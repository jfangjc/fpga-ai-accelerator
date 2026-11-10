SRC := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(SRC)/../..)
PYTHON ?= $(ROOT)/.venv/bin/python
# Stimulus, Verilator models and logs go to $(B); speech clips are extracted to
# $(B)/speech by extract_speech.py. The RTL loads its coefficient tables by bare
# file name, so the simulations themselves run in rtl/frontend.
BUILD_ROOT ?= $(ROOT)/build
B := $(BUILD_ROOT)/sim/frontend
export PYTHONPYCACHEPREFIX := $(BUILD_ROOT)/pycache
RTL := $(ROOT)/rtl/frontend
VERILATE := cd $(RTL) && verilator --binary --timing -Wno-fatal
SIM := cd $(RTL) &&
.PHONY: test lint clean test-top test-i2s test-control test-beam numerical check regression
test:
	mkdir -p $(B)
	cd $(ROOT) && $(PYTHON) model/frontend_fixed.py && $(PYTHON) sim/frontend/vectors.py $(B)
	$(VERILATE) --top-module tb $(SRC)/tb.sv stt_fft_mel.sv stt_fft_ram.sv stt_serial_mul.sv --Mdir $(B)/obj_dir > $(B)/build.log 2>&1
	$(SIM) $(B)/obj_dir/Vtb +vectors=$(B)
lint:
	verilator --lint-only -Wall --top-module stt_frontend $(RTL)/*.sv
test-top: test
	$(VERILATE) --top-module tb_top $(SRC)/tb_top.sv *.sv --Mdir $(B)/obj_top > $(B)/top_build.log 2>&1
	$(SIM) $(B)/obj_top/Vtb_top +vectors=$(B) +period=3
	$(SIM) $(B)/obj_top/Vtb_top +vectors=$(B) +period=1
test-i2s:
	mkdir -p $(B)
	$(VERILATE) --top-module tb_i2s $(SRC)/tb_i2s.sv stt_i2s.sv --Mdir $(B)/obj_i2s > $(B)/i2s_build.log 2>&1
	$(SIM) $(B)/obj_i2s/Vtb_i2s
test-control:
	mkdir -p $(B)
	$(VERILATE) --top-module tb_control $(SRC)/tb_control.sv *.sv --Mdir $(B)/obj_control > $(B)/control_build.log 2>&1
	$(SIM) $(B)/obj_control/Vtb_control
numerical:
	cd $(ROOT) && $(PYTHON) -m model.test_frontend_fixed $(B)/speech/*.flac
test-beam:
	cd $(ROOT) && $(PYTHON) sim/frontend/beam_vectors.py $(B)
	$(VERILATE) --top-module tb_beam $(SRC)/tb_beam.sv stt_beamformer.sv --Mdir $(B)/obj_beam > $(B)/beam_build.log 2>&1
	$(SIM) $(B)/obj_beam/Vtb_beam +vectors=$(B)
regression: lint test test-top test-i2s test-control test-beam numerical
check:
	@if $(MAKE) regression; then echo "PASS: all frontend checks"; else echo "FAIL: frontend regression"; exit 1; fi
clean:
	rm -rf $(B)
