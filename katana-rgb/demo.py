r"""
Live per-key RGB demo for EvoFox Katana Pro (SinoWealth 258A:0016).

Sequence:
  1. read current config tables and verify they match the backup
  2. switch keyboard to custom preset 1 (CM1) via modes table
  3. sweep a red dot across all 132 LED slots (one slot at a time)
  4. static rainbow + scrolling rainbow animation (with fps stats)
  5. restore the original tables from .\backup\*.bin and verify

Ctrl+C still restores, thanks to try/finally.
"""

import os
import sys
import time
import colorsys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidwin import HidHandle, enumerate_hid_paths  # noqa: E402
from probe import (  # noqa: E402
    VID_PID, PAYLOAD_LEN, REQ_INIT, REQ_MODES, REQ_PL_CM12, BACKUP_DIR, hexdump,
)

LED_COUNT = 132
COLORS_OFF = 8            # first per-key byte inside the 1032-byte payload
MODE_PER_KEY = 0x0F       # value for modes_buf[21] when custom preset active
PRESET = 0                # use CM1 (first color half)
PER_KEY_MODE_IDX = 20
CURRENT_MODE_IDX = 21


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


def write_table(data, buf):
    return data.send_feature(buf)


def load_backup(name):
    with open(os.path.join(BACKUP_DIR, f"{name}.bin"), "rb") as f:
        return bytearray(f.read())


def set_per_key_colors(per_key_buf, colors):
    """colors: iterable of (r, g, b) for each of the 132 slots."""
    buf = bytearray(per_key_buf)
    for i, (r, g, b) in enumerate(colors):
        if i >= LED_COUNT:
            break
        buf[COLORS_OFF + i] = b
        buf[COLORS_OFF + LED_COUNT + i] = g
        buf[COLORS_OFF + 2 * LED_COUNT + i] = r
    return bytes(buf)


def rainbow(count, phase=0.0):
    out = []
    for i in range(count):
        h = ((i / count) + phase) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


def main():
    cmd, data = open_kbd()
    print(f"cmd  : {cmd.path.split('#')[1]}")
    print(f"data : {data.path.split('#')[1]}")

    modes_orig = load_backup("modes")
    colors_orig = load_backup("colors")
    cm12_orig = load_backup("per_led_cm12")

    # --- 1. verify we still see the same state as the probe ------------------
    modes_now = read_table(cmd, data, REQ_MODES)
    if modes_now is None:
        raise SystemExit("could not read modes table")
    same = bytes(modes_now) == bytes(modes_orig)
    print(f"\nmodes table matches backup: {same}")
    print(f"current mode byte [21] = {modes_now[21]}, per-key flag [20] = {modes_now[20]}")

    if not cmd.send_feature(REQ_INIT):
        print("warning: init handshake failed")

    cm12_now = read_table(cmd, data, REQ_PL_CM12)
    if cm12_now is None:
        raise SystemExit("could not read per-key table")
    print(f"per-key table head: {hexdump(cm12_now, 0, 8)}")

    try:
        # --- 2. switch to custom preset CM1 ----------------------------------
        print("\n>>> switching keyboard to custom per-key mode (CM1)")
        mode_buf = bytearray(modes_orig)
        mode_buf[PER_KEY_MODE_IDX] = 1
        mode_buf[CURRENT_MODE_IDX] = MODE_PER_KEY
        mode_buf[CURRENT_MODE_IDX + 1] = 0x20 | PRESET
        ok = write_table(data, bytes(mode_buf))
        print(f"mode write ok: {ok}")
        time.sleep(0.1)

        # --- 3. red dot sweep ------------------------------------------------
        print(">>> red dot sweep over 132 LED slots (watch the keyboard!)")
        off = set_per_key_colors(cm12_orig, [(0, 0, 0)] * LED_COUNT)
        write_table(data, off)
        for i in range(LED_COUNT):
            colors = [(0, 0, 0)] * LED_COUNT
            colors[i] = (255, 0, 0)
            write_table(data, set_per_key_colors(cm12_orig, colors))
            print(f"  slot {i:>3}", end="\r")
            time.sleep(0.04)
        print(f"  slot {LED_COUNT - 1:>3}  done")

        # --- 4. static rainbow ----------------------------------------------
        print(">>> static rainbow across all 132 slots")
        write_table(data, set_per_key_colors(cm12_orig, rainbow(LED_COUNT)))

        # verify round trip on the device
        check = read_table(cmd, data, REQ_PL_CM12)
        if check is not None:
            print(f"read-back head: {hexdump(check, COLORS_OFF, 12)}")

        # --- 4b. animated rainbow, measure fps ------------------------------
        print(">>> animated rainbow (5 s), measuring throughput ...")
        frames = 0
        t0 = time.perf_counter()
        t_end = t0 + 5.0
        worst = 0.0
        while time.perf_counter() < t_end:
            t1 = time.perf_counter()
            write_table(data, set_per_key_colors(cm12_orig, rainbow(LED_COUNT, frames / 30.0)))
            dt = time.perf_counter() - t1
            worst = max(worst, dt)
            frames += 1
        elapsed = time.perf_counter() - t0
        print(f"frames: {frames} in {elapsed:.2f}s  ->  {frames / elapsed:.1f} fps "
              f"(worst frame {worst * 1000:.1f} ms)")

    finally:
        # --- 5. restore ------------------------------------------------------
        print("\n>>> restoring original configuration")
        write_table(data, bytes(cm12_orig))
        clear = bytearray(modes_orig)
        clear[PER_KEY_MODE_IDX] = 0
        clear[CURRENT_MODE_IDX] = 0
        clear[CURRENT_MODE_IDX + 1] = 0
        write_table(data, bytes(clear))
        time.sleep(0.25)
        write_table(data, bytes(modes_orig))
        time.sleep(0.2)

        modes_after = read_table(cmd, data, REQ_MODES)
        cm12_after = read_table(cmd, data, REQ_PL_CM12)
        colors_after = read_table(cmd, data, REQ_COLORS)
        print("restore check:")
        print(f"  modes  identical to backup: {bytes(modes_after) == bytes(modes_orig)}")
        print(f"  cm12   identical to backup: {bytes(cm12_after) == bytes(cm12_orig)}")
        print(f"  colors identical to backup: {bytes(colors_after) == bytes(colors_orig)}")

        cmd.close()
        data.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
