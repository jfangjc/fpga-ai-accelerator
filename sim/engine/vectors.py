"""Golden-generated edge cases plus deterministic random legal INT32/Q31 inputs."""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'model'))
from golden_int8 import requant

rng = np.random.default_rng(20261101)
count = 0
with open('requant.txt', 'w') as f:
    for n in list(range(1, 65))+[65, 96, 2147483678]:
        for lo, hi in [(-128, 127), (0, 255), (-32768, 32767)]:
            cases = [(a, m) for a in [-2147483648, -2147483647, -3, -1, 0, 1, 3, 2147483647]
                     for m in [1, 1073741824, 2147483647]]
            cases += [(int(rng.integers(-2147483648, 2147483648)), int(rng.integers(1, 2147483648))) for _ in range(20)]
            for a, m in cases:
                y = int(requant(np.array([[a]], np.int32), [m], [n-31], lo, hi)[0, 0])
                f.write(f'{a} {m} {n-31} {lo} {hi} {y}\n')
                count += 1
print(f'Generated {count} golden requant cases')
