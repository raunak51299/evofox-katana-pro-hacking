r"""Analyse the backed-up config tables: where does real color data live?"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import BACKUP_DIR  # noqa: E402


def load(name):
    with open(os.path.join(BACKUP_DIR, f"{name}.bin"), "rb") as f:
        return f.read()


def show(name):
    buf = load(name)
    nz = [i for i, b in enumerate(buf) if b]
    print(f"\n=== {name} ({len(buf)} bytes) ===")
    print(f"nonzero bytes: {len(nz)}")
    # group consecutive offsets
    groups = []
    for i in nz:
        if groups and i == groups[-1][1] + 1:
            groups[-1][1] = i
        else:
            groups.append([i, i])
    for a, b in groups[:40]:
        vals = " ".join(f"{v:02X}" for v in buf[a:min(b + 1, a + 12)])
        print(f"  [{a:4d}-{b:4d}] n={b - a + 1:<4} {vals}")


for table in ("modes", "colors", "per_led_cm12", "per_led_cm34", "per_led_cm5"):
    show(table)

# decode cm12 assuming layout: [8..404)=CM1 (B 132, G 132, R 132)
buf = load("per_led_cm12")
for half, base in (("CM1", 8), ("CM2", 404)):
    b = buf[base:base + 132]
    g = buf[base + 132:base + 264]
    r = buf[base + 264:base + 396]
    leds = [(r[i], g[i], b[i]) for i in range(132)]
    nzleds = [(i, c) for i, c in enumerate(leds) if c != (0, 0, 0)]
    print(f"\n{buf[0]:02X} table, {half}: nonzero LEDs: {len(nzleds)}")
    for i, c in nzleds[:40]:
        print(f"  slot {i:>3}: R={c[0]:>3} G={c[1]:>3} B={c[2]:>3}")
