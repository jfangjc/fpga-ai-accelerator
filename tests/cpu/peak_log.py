"""Parse peak's already median-reduced, per-variant output."""
import math
import re

def parse_peak(text, variant='smlal_4h_vec', threads=4):
    rows = re.findall(r'^variant=(\S+) threads=(\d+) seconds=(\S+) gmac_s=(\S+)$', text, re.M)
    rates = [float(rate) for name, nt, sec, rate in rows
             if name == variant and int(nt) == threads
             and math.isfinite(float(sec)) and float(sec) > 0]
    if len(rates) != 1 or not math.isfinite(rates[0]) or rates[0] <= 0:
        raise ValueError('Expected one positive peak median for variant/thread count')
    return rates[0]

if __name__ == '__main__':
    sample = '''variant=smlal_4h_vec threads=1 seconds=0.100000 gmac_s=1.0000
variant=smlal_4h_vec threads=4 seconds=0.100000 gmac_s=4.0000
variant=int8_sched threads=4 seconds=0.100000 gmac_s=8.0000
freq_add_chain threads=1 seconds=0.100000 est_hz=1.0000e+09
'''
    assert parse_peak(sample) == 4.0
    for invalid in ['', sample+sample, sample.replace('4.0000', 'nan')]:
        try:
            parse_peak(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('accepted invalid peak log')
    print('PASS peak log parser')
