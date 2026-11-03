r"""
Repair script: restore every config table from .\backup\*.bin using the
correct write header (byte1 = request byte1 & 0x7F, bytes[2..5] = request address).

Refuses to write any table it could not first read successfully
(same safety rule OpenRGB uses for these controllers).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidwin import HidHandle, enumerate_hid_paths  # noqa: E402
from probe import (  # noqa: E402
    VID_PID, PAYLOAD_LEN, REQ_INIT, REQ_MODES, REQ_COLORS,
    REQ_PL_CM12, REQ_PL_CM34, REQ_PL_CM5, BACKUP_DIR, hexdump,
)

TABLES = {
    "modes": REQ_MODES,
    "colors": REQ_COLORS,
    "per_led_cm12": REQ_PL_CM12,
    "per_led_cm34": REQ_PL_CM34,
    "per_led_cm5": REQ_PL_CM5,
}


def open_kbd():
    vendor = []
    for path in enumerate_hid_paths():
        if VID_PID not in path.lower():
            continue
        try:
            h = HidHandle(path)
        except OSError:
            continue
        if h.caps.UsagePage == 0xFF00:
            vendor.append(h)
        else:
            h.close()
    cmd = next((h for h in vendor if 0 < h.caps.FeatureReportByteLength <= 64), None)
    data = next((h for h in vendor if h.caps.FeatureReportByteLength >= PAYLOAD_LEN), None)
    if not cmd or not data:
        raise SystemExit("keyboard vendor collections not found")
    return cmd, data


def read_table(cmd, data, request):
    if not cmd.send_feature(request):
        return None
    return data.get_feature(0x06, PAYLOAD_LEN)


def write_table(data, request, buf):
    """Correct write: header byte1 = request[1] & 0x7F, address from request."""
    out = bytearray(buf)
    out[1] = request[1] & 0x7F
    out[2:6] = request[2:6]
    return data.send_feature(bytes(out))


def main():
    cmd, data = open_kbd()
    print(f"cmd  : {cmd.path.split('#')[1]}")
    print(f"data : {data.path.split('#')[1]}")
    cmd.send_feature(REQ_INIT)
    time.sleep(0.1)

    for name, req in TABLES.items():
        with open(os.path.join(BACKUP_DIR, f"{name}.bin"), "rb") as f:
            orig = bytearray(f.read())

        cur = read_table(cmd, data, req)
        if cur is None:
            print(f"{name:<12} READ FAILS - skipping (won't write blind)")
            continue

        diffs = [i for i, (a, b) in enumerate(zip(cur, orig)) if a != b]
        print(f"{name:<12} read ok, diffs vs backup: {len(diffs)}"
              f"{(' -> repairing' if diffs else ' -> already correct')}"
              f"{('   diff bytes: ' + hexdump(bytes(diffs[:16])) if 0 < len(diffs) <= 16 else '')}")
        if diffs:
            ok = write_table(data, req, orig)
            time.sleep(0.15)
            after = read_table(cmd, data, req)
            same = after is not None and bytes(after) == bytes(orig)
            print(f"{name:<12} repair write ok={ok}, verified={same}")

    print("\nfinal check:")
    for name, req in TABLES.items():
        cur = read_table(cmd, data, req)
        if cur is None:
            print(f"  {name:<12} READ FAILS")
            continue
        with open(os.path.join(BACKUP_DIR, f"{name}.bin"), "rb") as f:
            orig = f.read()
        print(f"  {name:<12} identical to backup: {bytes(cur) == orig}")

    modes = read_table(cmd, data, REQ_MODES)
    if modes:
        print(f"\ncurrent mode byte [21] = {modes[21]}, per-key flag [20] = {modes[20]}")
    print("restarting keyboard config check done. you may want to unplug/replug once more.")

    cmd.close()
    data.close()


if __name__ == "__main__":
    main()
