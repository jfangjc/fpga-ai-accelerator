# Live FPGA-Accelerated Speech-to-Text on the Kria KV260

Live captions from a microphone, computed on the KV260's FPGA: a DSP front end turns the audio into mel features,
and an INT8 engine runs the QuartzNet 15x5 speech model on them. The captions appear on a DisplayPort monitor and
stream to PCs over TCP. The project's plan is the [proposal](docs/Project_Proposal.pdf).

- [Setup](docs/setup.md): prepare the model and the FPGA design on a PC and install them on the board.
- [Run](docs/run.md): live captions from the microphone, and evaluation on recorded clips.

| Folder | Contents |
|---|---|
| `scripts/` | model preparation, board install and evaluation |
| `rtl/` | the front end (`frontend/`) and the engine (`engine/`) |
| `sim/` | their testbenches |
| `hw/` | the KV260 block design, overlay and pins |
| `sw/` | the board's driver and captioning app |

The QuartzNet weights (NVIDIA NGC `stt_en_quartznet15x5`) and LibriSpeech are downloaded by `scripts/setup.py` and
keep their own licences.
