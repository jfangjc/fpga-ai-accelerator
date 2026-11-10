"""Compare a raw /dev/fb0 capture with sttcap's caption.ppm; write PNGs.

Usage: fb_check.py PREFIX...   (PREFIX.fb, PREFIX.fbinfo, PREFIX.ppm from
i3_board.sh). fbinfo lines: virtual_size, bits_per_pixel, stride, pan.
Only RGB565 and XRGB8888 framebuffers are decoded. The PPM is reduced to
the framebuffer's channel depth before comparing the 640x240 caption area.
"""
import struct
import sys
import zlib
from pathlib import Path

import numpy as np


def png(path, rgb):
    """Minimal 8-bit RGB PNG writer (no imaging dependency)."""
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
    rgb = np.ascontiguousarray(rgb, np.uint8)
    rows = b''.join(b'\x00'+r.tobytes() for r in rgb)
    header = struct.pack('>IIBBBBB', rgb.shape[1], rgb.shape[0], 8, 2, 0, 0, 0)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', header)
                           + chunk(b'IDAT', zlib.compress(rows))+chunk(b'IEND', b''))


def main():
    ok = True
    for prefix in sys.argv[1:]:
        info = Path(prefix+'.fbinfo').read_text().split()
        bpp, stride = int(info[1]), int(info[2])
        pan_y = int(info[3].split(',')[1])
        raw = np.fromfile(prefix+'.fb', np.uint8)
        ppm = Path(prefix+'.ppm').read_bytes()
        rgb = np.frombuffer(ppm[-640*240*3:], np.uint8).reshape(240, 640, 3)
        rows = raw[pan_y*stride:(pan_y+240)*stride].reshape(240, stride)
        if bpp == 16:
            v = rows[:, :1280].copy().view('<u2').astype(np.uint32)
            fb = np.stack([(v >> 11) & 31, (v >> 5) & 63, v & 31], -1)
            want = np.stack([rgb[..., 0] >> 3, rgb[..., 1] >> 2, rgb[..., 2] >> 3], -1)
            shown = np.stack([fb[..., 0] << 3, fb[..., 1] << 2, fb[..., 2] << 3], -1)
        else:
            v = rows[:, :2560].copy().view('<u4')
            fb = np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], -1)
            want = rgb.astype(np.uint32)
            shown = fb
        same = int((fb == want).all(-1).sum())
        lit = int((rgb != 12).any(-1).sum())
        png(prefix+'.ppm.png', rgb)
        png(prefix+'.fb.png', shown)
        print(f'{prefix}: {bpp} bpp, {same}/{640*240} caption pixels match PPM, '
              f'{lit} text pixels', flush=True)
        ok &= same == 640*240
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
