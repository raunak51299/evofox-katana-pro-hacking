r"""Verify current keyboard state against the backup in .\backup\*.bin (read-only)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidwin import HidHandle, enumerate_hid_paths  # noqa: E402
from probe import (  # noqa: E402
    VID_PID, PAYLOAD_LEN, REQ_INIT, REQ_MODES, REQ_COLORS,
    REQ_PL_CM12, REQ_PL_CM34, REQ_PL_CM5, BACKUP_DIR,
)

TABLES = {
    "modes": REQ_MODES,
    "colors": REQ_COLORS,
    "per_led_cm12": REQ_PL_CM12,
    "per_led_cm34": REQ_PL_CM34,
    "per_led_cm5": REQ_PL_CM5,
}


def main():
    vendor = []
    for path in enumerate_hid_paths():
        if VID_PID not in path.lower():
            continue
        try:
            h = HidHandle(path)
        except OSError:
            continue
        (vendor if h.caps.UsagePage == 0xFF00 else []).append(h) if h.caps.UsagePage == 0xFF00 else h.close()
    cmd = next(h for h in vendor if 0 < h.caps.FeatureReportByteLength <= 64)
    data = next(h for h in vendor if h.caps.FeatureReportByteLength >= PAYLOAD_LEN)

    cmd.send_feature(REQ_INIT)

    print(f"current mode byte [21] = {(read(cmd, data, REQ_MODES))[21]}")
    for name, req in TABLES.items():
        cur = read(cmd, data, req)
        if cur is None:
            print(f"{name:<12} READ FAILED")
            continue
        with open(os.path.join(BACKUP_DIR, f"{name}.bin"), "rb") as f:
            orig = f.read()
        diffs = [i for i, (a, b) in enumerate(zip(cur, orig)) if a != b]
        nz = sum(1 for b in cur[8:404] if b)
        print(f"{name:<12} identical={not diffs} diffs={len(diffs)}"
              f"{(' first=' + str(diffs[:8])) if diffs else ''}  nonzero_in_first_half={nz}")

    cmd.close()
    data.close()


def read(cmd, data, req):
    if not cmd.send_feature(req):
        return None
    return data.get_feature(0x06, PAYLOAD_LEN)


if __name__ == "__main__":
    main()
