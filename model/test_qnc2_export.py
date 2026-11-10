"""Independently verify every payload byte and record bound of the real export."""
from pathlib import Path
import json
import struct
import unittest
from qnc2_export import export, read_qnw, read_program, run_layer, run_network
from engine_model import s2_overlap_estimate, network_allocation


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1]/'data/export/quartznet_int8.bin').read_bytes()

    def test_roundtrip(self):
        records = read_qnw(self.source)
        result = export(self.source)
        self.assertEqual(struct.unpack_from('<4s3I', result), (b'QNP2', 1, len(records), len(result)))
        cursor = 16
        for i, (d, arrays) in enumerate(records, 1):
            h = struct.unpack_from('<16I', result, cursor)
            self.assertEqual(h[0], 0x32524C51)
            self.assertEqual(h[2], i)
            self.assertEqual(list(h[3:14]), d[:11])
            end = cursor+h[1]
            cursor += 64
            for a in arrays[1:]+arrays[:1] if d[0] == 1 else arrays:
                self.assertEqual(result[cursor:cursor+len(a)], a)
                cursor += len(a)
                padding = (-cursor)%16
                self.assertEqual(result[cursor:cursor+padding], bytes(padding))
                cursor += padding
            self.assertEqual(cursor, end)
        self.assertEqual(cursor, len(result))

    def test_program_reader(self):
        original = read_qnw(self.source)
        encoded = export(self.source)
        decoded = read_program(encoded)
        for (d, arrays), (other, payload) in zip(original, decoded):
            self.assertEqual(d[:11], other[:11])
            self.assertEqual(arrays, payload)
        for cut in (0, 15, 16, 79, len(encoded)-1):
            with self.assertRaises(ValueError):
                read_program(encoded[:cut])
        for offset, value in ((4, 2), (8, 185), (8, 187), (12, 0),
                              (16, 0), (20, 16), (24, 2), (32, 1),
                              (72, 1), (80+struct.unpack_from('<I', encoded, 44)[0]*4, 0)):
            damaged = bytearray(encoded)
            struct.pack_into('<I', damaged, offset, value)
            with self.assertRaises(ValueError, msg=str(offset)):
                read_program(damaged)

    def test_network_envelope_and_liveness(self):
        program = export(self.source)
        records = read_program(program)
        for steps, valid in ((32, 18), (48, 48), (112, 102),
                             (300, 300), (304, 300), (320, 320), (320, 0), (640, 640)):
            command = run_network(program, bytes(64*steps), steps, valid, tag=123)
            h = struct.unpack_from('<16I', command)
            self.assertEqual(h, (0x32434e51, 1, 2, 123, len(command)-64,
                                64, steps, valid, 29, 1, 186, len(program), 0, 0, 0, 0))
            self.assertEqual(command[64+64*steps:], program)
            plan = network_allocation(records, steps, valid)
            self.assertLessEqual(plan['peak_banks'], 5)
            live = {0: 0}
            last = {}
            for i, (d, _) in enumerate(records, 1):
                for src in ([d[1], d[2]] if d[0] == 2 else [d[1]]):
                    last[src] = i
            for row, (d, _) in zip(plan['rows'], records):
                i = row['id']
                sources = [d[1], d[2]] if d[0] == 2 else [d[1]]
                self.assertEqual(row['source_banks'], [live[s] for s in sources])
                self.assertNotIn(row['bank'], live.values())
                self.assertLessEqual(row['words'], plan['bank_words'])
                live[i] = row['bank']
                live = {s: b for s, b in live.items() if s == i or last.get(s, 0) > i}
            self.assertEqual(plan['rows'][-1]['valid'], (valid+1)//2)
        for steps, valid in ((641, 300), (0, 0), (300, 301), (300, -1)):
            with self.assertRaises(ValueError):
                run_network(program, bytes(64*max(steps, 0)), steps, valid)
        with self.assertRaises(ValueError):
            run_network(program, b'', 300, 300)

    def test_reject(self):
        for offset, value in [(0, 0), (4, 9), (28, 1), (36, 1), (64, 99), (68, 999), (112, 1), (144, 1)]:
            b = bytearray(self.source)
            struct.pack_into('<I', b, offset, value)
            with self.assertRaises(ValueError, msg=str(offset)):
                export(b)
        with self.assertRaises(ValueError):
            export(self.source[:-1])

    def test_s1_command(self):
        records = read_qnw(self.source)
        count = 0
        for d, arrays in records:
            if d[0] != 1 or d[6:11] != [1, 1, 1, 0, 1]:
                continue
            features = bytes(d[5]*17)
            cmd = run_layer((d, arrays), features, 17, 11, tag=123)
            h = struct.unpack_from('<16I', cmd)
            self.assertEqual(h[:10], (0x32434e51, 1, 1, 123, len(cmd)-64,
                                     d[5], 17, 11, d[4], 1))
            cursor = 64 + (len(features)+15)//16*16
            r = struct.unpack_from('<16I', cmd, cursor)
            self.assertEqual(r[1], len(cmd)-cursor)
            self.assertEqual(list(r[3:14]), d[:11])
            cursor += 64
            for a in arrays[1:]+arrays[:1]:
                self.assertEqual(cmd[cursor:cursor+len(a)], a)
                cursor += (len(a)+15)//16*16
            self.assertEqual(cursor, len(cmd))
            with self.assertRaises(ValueError):
                run_layer((d, arrays), features, 17, 18)
            count += 1
        self.assertEqual(count, 94)

    def test_all_manifest_commands(self):
        root = Path(__file__).resolve().parents[1]/'data/export'
        records = read_qnw(self.source)
        names = json.loads((root/'layers.json').read_text())
        lookup = {x['name']: records[x['id']-1] for x in names}
        counts = [0, 0, 0]
        for entry in json.loads((root/'vectors/manifest.json').read_text()):
            d, arrays = lookup[entry['layer']]
            inputs = entry['inputs']
            source = inputs[0]
            ci, t = source['shape']
            x = (root/'vectors'/source['file']).read_bytes()
            b = ((root/'vectors'/inputs[1]['file']).read_bytes()
                 if len(inputs) == 2 else None)
            cmd = run_layer((d, arrays), x, t, source['valid_frames'],
                            signed=source['dtype'] == 'int8', branch1=b)
            h = struct.unpack_from('<16I', cmd)
            kind = 2 if d[0] == 2 else int(d[10] == d[4] and d[5] == 1)
            counts[kind] += 1
            self.assertEqual(h[4], len(cmd)-64)
            self.assertEqual(h[5:9], (ci, t, source['valid_frames'], d[4]))
            self.assertEqual(h[10], kind)
            self.assertEqual(h[11:15], tuple(d[6:10]) if kind == 1 else (0,)*4)
            cursor = 64
            for blob in [x] + ([b] if b is not None else []):
                self.assertEqual(cmd[cursor:cursor+len(blob)], blob)
                cursor += (len(blob)+15)//16*16
            rec = struct.unpack_from('<16I', cmd, cursor)
            self.assertEqual(list(rec[3:14]), d[:11])
            self.assertEqual(rec[1], len(cmd)-cursor)
            cursor += 64
            for blob in arrays if kind == 2 else arrays[1:]+arrays[:1]:
                self.assertEqual(cmd[cursor:cursor+len(blob)], blob)
                cursor += (len(blob)+15)//16*16
            self.assertEqual(cursor, len(cmd))
        self.assertEqual(sum(counts), 558)
        self.assertTrue(all(counts))

    def test_s2_reject_branch_shape(self):
        record = next(r for r in read_qnw(self.source) if r[0][0] == 2)
        c = record[0][4]
        for branch, signed in [(None, True), (bytes(c*17-1), True),
                               (bytes(c*17), False)]:
            with self.assertRaises(ValueError):
                run_layer(record, bytes(c*17), 17, 11, branch1=branch, signed=signed)

    def test_overlap_estimate(self):
        records = read_qnw(self.source)
        r = s2_overlap_estimate(records)
        self.assertEqual(len(r['rows']), len(records))
        self.assertGreater(r['serial_cycles'], r['overlap_bound_cycles'])
        self.assertEqual(r['dw_macs'], 128)
        self.assertEqual(r['dw_read_bytes_per_cycle'], 128)
        faster = s2_overlap_estimate(records, mhz=150)
        self.assertEqual(r['serial_cycles'], faster['serial_cycles'])
        self.assertAlmostEqual(r['serial_ms']/1.5, faster['serial_ms'])
        # Every hidden producer must feed exactly one adjacent pointwise.
        hidden = 0
        for a, b in zip(r['rows'], r['rows'][1:]):
            if a['kind'] == 'depthwise' and b['source'] == a['id'] and b['kind'] == 'pointwise':
                uses = sum(d[1] == a['id'] for d, _ in records)
                uses += sum(d[0] == 2 and d[2] == a['id'] for d, _ in records)
                if uses == 1:
                    hidden += min(a['cycles'], b['cycles'])
        self.assertEqual(r['serial_cycles']-hidden, r['overlap_bound_cycles'])


if __name__ == '__main__':
    unittest.main()

