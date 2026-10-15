# Kernel drivers

The inference and rollback images use different `qn_tile` modules. Both modules have the same module name, so the runner unloads one before loading the other.

| Purpose | Source | External binary |
|---|---|---|
| Bulk rollback | `drivers/bulk` | `drivers/prebuilt/6.8.0-1036-xilinx/bulk/qn_tile.ko` |
| Retained branch inference | `drivers/branch` | `drivers/prebuilt/6.8.0-1036-xilinx/branch/qn_tile.ko` |

Install the external package with `scripts/setup_dependencies.py` first. It supplies both driver sources and prebuilt modules outside the source repository.

## Rebuild for a kernel

Obtain the headers and generated configuration for the exact running Xilinx kernel. A similarly numbered generic Ubuntu kernel is not a substitute. Check the build directory before compiling:

```sh
test -f /lib/modules/$(uname -r)/build/Makefile
make -C drivers/bulk
make -C drivers/branch
modinfo -F vermagic drivers/bulk/qn_tile.ko
modinfo -F vermagic drivers/branch/qn_tile.ko
```

The Makefiles accept `KDIR=/path/to/kernel/build` for a prepared kernel tree. Cross-compilation also needs the matching architecture and toolchain configuration. The shipped modules remain unchanged when you build in the source directories.

After reviewing a rebuild, place both modules under `drivers/prebuilt/<kernel-release>/bulk` and `branch`. For local testing, update the relevant hashes in `config/dependencies.json`, then run `python scripts/write_manifest.py`. Publishing changed dependencies also requires a new archive, its size and SHA256, and new qualification. This only records bytes. It does not establish ABI compatibility or hardware correctness.

Test installation, loading, protocol parameters, physical counters, CPU logit parity and rollback on that kernel. The project has only qualified the recorded `6.8.0-1036-xilinx` environment. Public headers and out-of-tree kernel APIs may need source changes on another release.
