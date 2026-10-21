"""Bounded independent capture, with explicit discontinuity and overrun errors."""
from dataclasses import dataclass
import json
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time

import numpy as np

RATE = 16000
BLOCK = 320
HEADER = struct.Struct('<IIQIIIIII')


@dataclass
class Block:
    samples: np.ndarray
    end_ns: int


class Highpass:
    """Continuous one-pole DC blocker; prime without an onset transient."""
    def __init__(self, hz=30):
        if not np.isfinite(hz) or not 0 < hz <= 200:
            raise ValueError('High-pass cutoff must be in (0,200] Hz')
        self.a = float(np.exp(-2*np.pi*hz/RATE))
        self.powers = self.a**np.arange(1, BLOCK+1, dtype=np.float64)
        self.previous_x = None
        self.previous_y = 0.

    def process(self, samples):
        x = np.asarray(samples, dtype=np.float64)
        if x.ndim != 1 or not 0 < len(x) <= BLOCK or not np.isfinite(x).all():
            raise ValueError('Expected finite capture block for high-pass filter')
        if self.previous_x is None:
            self.previous_x = float(x[0])
        delta = np.empty_like(x)
        delta[0] = x[0]-self.previous_x
        delta[1:] = np.diff(x)
        powers = self.powers[:len(x)]
        y = powers*(self.previous_y+np.cumsum(self.a*delta/powers))
        self.previous_x, self.previous_y = float(x[-1]), float(y[-1])
        return y.astype(np.float32)


def read_exact(stream, count, eof=False):
    result = bytearray()
    while len(result) < count:
        part = stream.read(count-len(result))
        if not part:
            if eof and not result:
                return None
            raise RuntimeError('Incomplete capture packet')
        result.extend(part)
    return bytes(result)


def packets(stream):
    sequence, baseline = 0, None
    while True:
        data = read_exact(stream, HEADER.size, eof=True)
        if data is None:
            return
        magic, seq, end, count, captured, dropped, startup, fifo, reserved = HEADER.unpack(data)
        if magic != 0x31434d56 or seq != sequence or not 0 < count <= BLOCK or fifo > 1024 or reserved:
            raise RuntimeError('Invalid/discontinuous microphone packet')
        if dropped != startup or (baseline is not None and startup != baseline):
            raise RuntimeError('Microphone FIFO overflow')
        baseline = startup
        sequence = (sequence+1) & 0xffffffff
        values = np.frombuffer(read_exact(stream, count*4), dtype='<i4')
        if np.any(values < -8388608) or np.any(values > 8388607):
            raise RuntimeError('Capture data is not signed PCM24')
        yield Block(values.astype(np.float32)/np.float32(8388608), end)


def microphone_device(selection=None):
    """Find only the voice region in the known, pinned bulk application."""
    streaming = Path('/sys/bus/platform/devices/asr-v3-mic-select-clock/set_rate')
    if selection is not None and not streaming.exists():
        raise RuntimeError('Input/slot selection requires the separate asr-v3-mic-select FPGA image')
    clock = streaming if streaming.exists() else Path('/sys/bus/platform/devices/asr-v3-bulk-clock/set_rate')
    if not clock.exists() or abs(int(clock.read_text())-100000000) > 5:
        raise RuntimeError('Microphone requires the 100 MHz bulk application and board wrapper')
    candidates = []
    for node in Path('/sys/class/uio').glob('uio*'):
        if (node/'name').read_text().strip() != 'voice-prototype':
            continue
        if int((node/'maps/map0/addr').read_text(), 0) != 0xa0000000:
            continue
        if int((node/'maps/map0/size').read_text(), 0) != 0x10000:
            continue
        candidates.append(Path('/dev')/node.name)
    if len(candidates) != 1:
        raise RuntimeError('Expected one voice-prototype UIO region; bind uio_pdrv_genirq with of_id=generic-uio')
    return candidates[0]


class Microphone:
    def __init__(self, executable, seconds=0, selection=None):
        if seconds < 0:
            raise ValueError('Capture duration must be nonnegative')
        self.executable, self.seconds = Path(executable), seconds
        if selection is not None and selection not in (0, 1, 2, 3):
            raise ValueError('Invalid microphone input/slot selection')
        self.selection = selection
        if self.selection is None and Path('/sys/bus/platform/devices/asr-v3-mic-select-clock/set_rate').exists():
            self.selection = 0
        self.process = None
        self.stderr = bytearray()

    def __iter__(self):
        command = [str(self.executable.resolve()), str(microphone_device(self.selection)), str(self.seconds)]
        if self.selection is not None:
            command.append(str(self.selection))
        self.process = subprocess.Popen(command,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def errors():
            while True:
                line = self.process.stderr.readline()
                if not line:
                    break
                # Keep diagnostics bounded even on repeated external failures.
                self.stderr.extend(line)
                del self.stderr[:-16384]
        reader = threading.Thread(target=errors, daemon=True)
        reader.start()
        try:
            yield from packets(self.process.stdout)
            status = self.process.wait(timeout=3)
            reader.join(timeout=3)
            if status:
                raise RuntimeError('Microphone helper failed: '+self.stderr.decode(errors='replace'))
        finally:
            self.close()
            reader.join(timeout=3)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


class Replay:
    def __init__(self, path, paced=True):
        self.path, self.paced = path, paced
        self.stop = threading.Event()

    def __iter__(self):
        import soundfile as sf
        with sf.SoundFile(self.path) as wav:
            if wav.samplerate != RATE or wav.channels != 1:
                raise ValueError('Replay requires mono 16 kHz audio')
            origin, count = time.monotonic_ns(), 0
            for data in wav.blocks(blocksize=BLOCK, dtype='float32', always_2d=False):
                if self.stop.is_set():
                    break
                count += len(data)
                end = origin+count*1000000000//RATE
                if self.paced and self.stop.wait(max(0, (end-time.monotonic_ns())/1e9)):
                    break
                yield Block(data.copy(), end)

    def close(self):
        self.stop.set()


class BufferedCapture:
    """Reader continues during inference. Queue exhaustion fails visibly."""
    def __init__(self, source, seconds=10):
        if not 0.1 <= seconds <= 60:
            raise ValueError('Capture buffer must be between 0.1 and 60 seconds')
        self.source = source
        self.queue = queue.Queue(maxsize=max(1, int(seconds*RATE/BLOCK)))
        self.done, self.stopping = threading.Event(), threading.Event()
        self.error = None
        self.high_water_blocks = 0
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self):
        try:
            for block in self.source:
                if self.stopping.is_set():
                    break
                if not 0 < len(block.samples) <= BLOCK or not np.isfinite(block.samples).all():
                    raise ValueError('Invalid capture audio')
                try:
                    self.queue.put_nowait(block)
                except queue.Full:
                    raise RuntimeError('Capture queue exhausted: inference cannot keep up; audio was not silently discarded')
                self.high_water_blocks = max(self.high_water_blocks, self.queue.qsize())
        except Exception as error:
            if not self.stopping.is_set():
                self.error = error
        finally:
            self.source.close()
            self.done.set()

    def __enter__(self):
        self.thread.start()
        return self

    def __iter__(self):
        while True:
            if self.error is not None:
                raise self.error
            try:
                yield self.queue.get(timeout=0.05)
            except queue.Empty:
                if self.done.is_set():
                    if self.error is not None:
                        raise self.error
                    return

    def close(self):
        self.stopping.set()
        self.source.close()
        self.thread.join(timeout=4)
        if self.thread.is_alive():
            raise RuntimeError('Capture thread failed to stop')

    def __exit__(self, *_):
        self.close()


def main():
    import argparse
    import math
    import soundfile as sf
    p = argparse.ArgumentParser(description='Check the physical SPH0645 stream and save a PCM24 WAV plus health report')
    p.add_argument('--helper', type=Path, default=Path('build/native/voice_capture'))
    p.add_argument('--seconds', type=float, default=5)
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--mic-select', type=int, choices=range(4), help='MIC1 firmware: 0/1=primary left/right; 2/3=secondary left/right')
    a = p.parse_args()
    if not math.isfinite(a.seconds) or not .1 <= a.seconds <= 3600:
        p.error('Capture duration must be 0.1 to 3600 seconds')
    a.out_dir.mkdir(parents=True, exist_ok=False)
    subprocess.run(['modprobe', 'uio_pdrv_genirq', 'of_id=generic-uio'], check=True)
    source = Microphone(a.helper, a.seconds, a.mic_select)
    count, nonzero, squares, peak = 0, 0, 0., 0.
    first_ns = last_ns = None
    error = None
    try:
        with sf.SoundFile(str(a.out_dir/'microphone.wav'), 'w', samplerate=RATE, channels=1, subtype='PCM_24') as wav:
            with BufferedCapture(source) as captured:
                for block in captured:
                    wav.write(block.samples)
                    count += len(block.samples)
                    nonzero += int(np.count_nonzero(block.samples))
                    squares += float(np.square(block.samples, dtype=np.float64).sum())
                    peak = max(peak, float(np.max(np.abs(block.samples))))
                    if first_ns is None:
                        first_ns = block.end_ns
                    last_ns = block.end_ns
    except Exception as failure:
        error = str(failure)
    transport = error is None and count == int(a.seconds*RATE)
    report = {'samples': count, 'sample_rate': RATE, 'audio_seconds': count/RATE,
        'mic_selection': source.selection,
        'transport_passed': transport, 'signal_present': nonzero > 0,
        'rms_dbfs': 20*math.log10(max(math.sqrt(squares/max(1, count)), 1e-12)),
        'peak': peak, 'zero_fraction': 1-nonzero/max(1, count), 'error': error,
        'helper_diagnostics': source.stderr.decode(errors='replace'),
        'first_packet_ns': first_ns, 'last_packet_ns': last_ns,
        'passed': transport and nonzero > 0}
    (a.out_dir/'capture.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 2)


if __name__ == '__main__':
    main()
