# Build from source

Install the external package with `scripts/setup_dependencies.py` before building. It provides the generated block design, ONNX Runtime headers, model, firmware and driver sources. These files are ignored by Git.

The prebuilt deployment path does not require FPGA tools. This page is for changes to RTL, native libraries or drivers.

## FPGA project

Use Vivado 2026.1 with support for `xck26-sfvc784-2LV-c` and the board definition `xilinx.com:kv260_som:part0:2.0`. Run from the repository root:

```sh
vivado -mode batch -source scripts/project.tcl -log build-check.log -journal build-check.jou -tclargs check
```

The script imports `hardware/reference/asr_v2_system/asr_v2_system.bd`, adds the selected RTL and source constraints, regenerates IP, and checks block-design connectivity. It creates `build/fpga/kv260_asr.xpr`. It refuses to overwrite that directory.

To synthesize and route, start with a fresh `build/fpga` path and replace `check` with `build`. Preserve a previous build under another name first. The build requires setup slack of at least 0.001 ns, nonnegative hold slack and no DRC Error or Critical Warning. It writes an XSA under `build/fpga` and reports under `reports/local`.

The reference top keeps the existing PMOD mapping. The inference image does not qualify microphone capture at 166.667 MHz. Do not infer capture support from the presence of microphone ports or historical CDC constraints.

The shipped branch image has 0.043 ns setup slack and 0.010 ns hold slack in its recorded routed build. A new route can produce different results. Convert a rebuilt bitstream with the matching AMD tools, pair it with the matching overlay, and qualify it before replacing the supplied firmware. The included bulk RTL is the rollback design's source. `project.tcl` recreates the selected inference image, not a new bulk implementation.

## RTL simulations

```sh
vivado -mode batch -source scripts/simulate.tcl -tclargs ab_mac_conformance
vivado -mode batch -source scripts/simulate.tcl -tclargs branch512_ab_fast_sim
vivado -mode batch -source scripts/simulate.tcl -tclargs branch512_ab_reset_sim
vivado -mode batch -source scripts/simulate.tcl -tclargs branch512_ab_payload_reset_sim
```

The vendor DSP comparison checks 203,980 cases. The full-block test checks 25 residual branches, stalls and malformed packets. The reset tests cover six pipeline and two output-buffer replay cases. Full-block tests use the checked behavioral MAC model for simulation speed. Synthesis uses the vendor DSP primitives in the production core.

## Native libraries

On an ARM64 build machine with GCC and G++:

```sh
sh scripts/build_native.sh
```

The command writes new libraries under `build/native`. It preserves the supplied runtime libraries. The `-ffp-contract=off` setting is part of the arithmetic contract. Do not add fast-math flags without new numerical tests.

To adopt a rebuild, compare it against the INT8 CPU graph on the board. After that review, replace the corresponding supplied libraries and update the dependency hashes and archive as described in [dependency setup](dependencies.md). Rebuilt bytes will not necessarily match the original binaries even when the behavior matches.

## Model assets

`models/citrinet-256/cpu-int8-cse.onnx` is the checkpoint-specific INT8 graph used for the recorded test. `frontend.npz` and `vocab.txt` define the frontend and WordPiece vocabulary. The upstream checkpoint is NVIDIA's `stt_en_citrinet_256_ls`, revision `c7cdacff0262bbb2a17771d20ec9f9f3b5cff6ed`.

The external dependency package includes the ready-to-run model. Retraining and recalibration are outside this build path. A replacement model needs new graph-compiler checks, numerical parity, held-out accuracy evaluation and timing measurements. Renaming another ONNX file to the expected path does not make it compatible.
