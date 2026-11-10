# Measured evidence

Raw logs, reports and summaries behind every number in
[docs/results.md](../docs/results.md). The folders are kept as recorded, so
scripts and command lines inside them refer to the paths in use at the time
(for example `hw/measurements/...`, the earlier name of this folder).

| Folder | Contents | Report |
|---|---|---|
| `platform/` | loopback DMA, IRQ soak, loopback-design timing and utilisation | [platform](../docs/platform.md), [model_platform](../docs/results/model_platform.md) |
| `i1/` | front-end design timing and utilisation; per-clip bulk/paced front-end logs | [i1](../docs/results/i1.md) |
| `i2/`, `i2-network/` | `stt-full` design reports; I2 per-layer and RUN_NETWORK runs | [i2](../docs/results/i2.md) |
| `engdrv/` | engine driver co-simulation and board cycle counts | [engdrv](../docs/results/engdrv.md) |
| `full150/` | `stt-full150` implementation reports and board acceptance | [full150](../docs/results/full150.md) |
| `app/` | captioning app timelines, A/B and benchmark summaries, framebuffer captures | [app](../docs/results/app.md) |
| `t1/` | T1 per-clip results, summaries and raw run archive | [t1](../docs/results/t1.md) |
| `t2/` | T2 deployment, soaks, follow-ups and demo rehearsals | [t2](../docs/results/t2.md) |
| `mic/` | live microphone runs, offline gain study, service runs | [mic](../docs/results/mic.md) |
