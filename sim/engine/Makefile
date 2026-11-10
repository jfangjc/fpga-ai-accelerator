SRC := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(SRC)/../..)
PYTHON ?= $(ROOT)/.venv/bin/python
# Vectors, Verilator models, cycle CSVs and logs all go to $(B); every step runs there.
BUILD_ROOT ?= $(ROOT)/build
B := $(BUILD_ROOT)/sim/engine
export PYTHONPYCACHEPREFIX := $(BUILD_ROOT)/pycache
RUN := cd $(B) &&
.PHONY: check clean
check:
	mkdir -p $(B)
	$(RUN) $(PYTHON) $(ROOT)/model/test_qnc2_export.py
	$(RUN) $(PYTHON) $(SRC)/vectors.py
	$(RUN) $(PYTHON) $(SRC)/depthwise_vectors.py
	$(RUN) $(PYTHON) $(SRC)/add_vectors.py
	bash $(SRC)/lint_engine.sh
	verilator --lint-only -Wall $(ROOT)/rtl/engine/stt_requant.sv
	$(RUN) verilator --cc --exe --build -j 8 -Wall --top-module stt_requant --Mdir obj_dir $(ROOT)/rtl/engine/stt_requant.sv $(SRC)/tb_requant.cpp
	$(RUN) ./obj_dir/Vstt_requant
	bash $(SRC)/test_act_unpack.sh
	bash $(SRC)/test_engine.sh $(B) > $(B)/engine.log 2>&1 || { tail -30 $(B)/engine.log; echo 'FAIL engine'; exit 1; }
	tail -1 $(B)/engine.log
	$(RUN) ./obj_engine/Vstt_engine add > add.log 2>&1 || { tail -20 $(B)/add.log; exit 1; }
	$(RUN) ./obj_engine/Vstt_engine add150 > add150.log 2>&1 || { tail -20 $(B)/add150.log; exit 1; }
	$(RUN) $(PYTHON) $(SRC)/check_add_cycles.py
	$(RUN) $(PYTHON) $(SRC)/test_depthwise.py
	$(RUN) $(PYTHON) $(SRC)/check_depthwise_cycles.py
	$(RUN) ./obj_engine/Vstt_engine t150 > cycle.log 2>&1 || { tail -20 $(B)/cycle.log; exit 1; }
	$(RUN) $(PYTHON) $(SRC)/check_engine_cycles.py
	$(RUN) $(PYTHON) $(SRC)/maximum_vector.py
	$(RUN) ./obj_engine/Vstt_engine max maximum.bin > maximum.log 2>&1 || { tail -20 $(B)/maximum.log; exit 1; }
	tail -1 $(B)/maximum.log
	$(RUN) $(PYTHON) $(SRC)/network_vectors.py --all
	$(RUN) ./obj_engine/Vstt_engine network > network.log 2>&1 || { tail -20 $(B)/network.log; exit 1; }
	$(RUN) $(PYTHON) $(SRC)/check_network_cycles.py
	tail -1 $(B)/network.log
	@echo 'PASS engine check'
clean:
	rm -rf $(B)
