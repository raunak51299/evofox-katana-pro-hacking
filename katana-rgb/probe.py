r"""
Read-only probe of the EvoFox Katana Pro (SinoWealth 258A:0016) HID protocol.

Mirrors the request sequence used by OpenRGB's SinowealthKeyboard16Controller.
Nothing is written to the keyboard's config here - only feature-report
requests and reads (the same handshake the vendor software does on start-up).

Usage:  python probe.py
Writes a raw backup of every config table into .\backup\*.bin
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidwin import HidHandle, enumerate_hid_paths, hid  # noqa: E402

VID_PID = "vid_258a&pid_0016"
PAYLOAD_LEN = 1032  # feature report size used by the Sinowealth 0x0016 protocol

REQ_INIT = bytes([0x05, 0x01, 0xAA, 0xBB, 0x2F, 0x3E])
REQ_MODES = bytes([0x05, 0x83, 0x00, 0x00, 0x00, 0x00])
REQ_COLORS = bytes([0x05, 0x88, 0xA8, 0x00, 0x40, 0x00])
REQ_PL_CM12 = bytes([0x05, 0x89, 0xAC, 0x00, 0x40, 0x00])
REQ_PL_CM34 = bytes([0x05, 0x89, 0xB0, 0x00, 0x40, 0x00])
REQ_PL_CM5 = bytes([0x05, 0x89, 0xB4, 0x00, 0x40, 0x00])

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup")


def hexdump(data, start=0, n=32):
    return " ".join(f"{b:02X}" for b in data[start:start + n])


def find_collections():
    cols = []
    for path in enumerate_hid_paths():
        if VID_PID not in path.lower():
            continue
        try:
            h = HidHandle(path)
        except OSError as e:
            print(f"  ! cannot open {path}: {e}")
            continue
        suffix = re.search(r"&col(\d+)", path.lower())
        cols.append({
            "handle": h,
            "path": path,
            "col": int(suffix.group(1)) if suffix else -1,
            "usage_page": h.caps.UsagePage,
            "usage": h.caps.Usage,
            "feature_len": h.caps.FeatureReportByteLength,
            "input_len": h.caps.InputReportByteLength,
            "output_len": h.caps.OutputReportByteLength,
        })
    return cols


def request_table(dev_cmd, dev_data, request):
    """Send a table request on dev_cmd, read the 0x06 response from dev_data."""
    if not dev_cmd.send_feature(request):
        return None
    resp = dev_data.get_feature(0x06, PAYLOAD_LEN)
    return resp


def decode_modes(buf, out):
    if buf is None:
        out["modes_read"] = False
        return
    out["modes_read"] = True
    out["modes_raw_head"] = hexdump(buf, 0, 24)
    out["per_key_flag_byte20"] = buf[20]
    out["current_mode_byte21"] = buf[21]
    modes = []
    for i in range(15):
        b0, b1 = buf[32 + i * 2], buf[32 + i * 2 + 1]
        modes.append({
            "idx": i,
            "color_preset": b0 & 0x07,
            "direction_left": (b0 >> 7) & 1,
            "brightness": b1 & 0x0F,
            "speed": (b1 >> 4) & 0x0F,
        })
    out["device_modes"] = modes


def decode_colors(buf, out):
    if buf is None:
        out["colors_read"] = False
        return
    out["colors_read"] = True
    out["colors_raw_head"] = hexdump(buf, 0, 24)
    # colors_start_idx = 8, 15 modes x 7 presets x 3 bytes (B,G,R)
    presets = []
    for i in range(7):
        off = 8 + i * 3
        presets.append({"blue": buf[off], "green": buf[off + 1], "red": buf[off + 2]})
    out["mode0_presets"] = presets


def decode_perled(buf, out, key):
    if buf is None:
        out[key] = {"read": False}
        return
    colors = buf[8:8 + 396]
    nonzero = sum(1 for b in colors if b)
    out[key] = {
        "read": True,
        "head": hexdump(buf, 0, 12),
        "first_leds": [list(colors[i * 3:i * 3 + 3]) for i in range(8)],  # B,G,R
        "nonzero_bytes_in_396": nonzero,
    }


def main():
    print("Enumerating 258A:0016 HID collections ...\n")
    cols = find_collections()
    for c in cols:
        print(f"  col{c['col']:>2}  usage_page=0x{c['usage_page']:04X} usage=0x{c['usage']:04X} "
              f"feat={c['feature_len']}B in={c['input_len']}B out={c['output_len']}B")
    if not cols:
        print("No 258A:0016 collections found.")
        return 1

    vendor = [c for c in cols if c["usage_page"] == 0xFF00]
    print(f"\nVendor (0xFF00) collections: {len(vendor)}")
    if not vendor:
        print("No 0xFF00 vendor collection - different protocol family.")
        return 1

    # The Sinowealth protocol splits work across two vendor collections:
    #  - a small "command" collection (short feature reports, 6 bytes)
    #  - a large "data" collection (1032-byte feature reports)
    cmds = [c for c in vendor if 0 < c["feature_len"] <= 64]
    datas = [c for c in vendor if c["feature_len"] >= PAYLOAD_LEN]
    if not cmds or not datas:
        print(f"Unexpected vendor layout: feature lens {[c['feature_len'] for c in vendor]}")
        print("-> different protocol family.")
        return 1
    dev_cmd = cmds[0]["handle"]
    dev_data = datas[0]["handle"]
    dev_data_col = datas[0]["col"]
    print(f"cmd collection: col{cmds[0]['col']} ({cmds[0]['feature_len']}B feature reports)")
    print(f"data collection: col{datas[0]['col']} ({datas[0]['feature_len']}B feature reports)")

    mode_req_ok = dev_cmd.send_feature(REQ_MODES)
    print(f"send modes request on cmd collection: {mode_req_ok}")
    resp = dev_data.get_feature(0x06, PAYLOAD_LEN)
    print(f"read 0x06 feature report on data collection: {resp is not None}"
          f"  head={hexdump(resp, 0, 8) if resp else '-'}")


    if not dev_cmd.send_feature(REQ_INIT):
        print("init handshake failed")
    else:
        print("init handshake sent (05 01 AA BB 2F 3E)")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    result = {"vid_pid": VID_PID, "timestamp": time.time()}

    tables = {
        "modes": REQ_MODES,
        "colors": REQ_COLORS,
        "per_led_cm12": REQ_PL_CM12,
        "per_led_cm34": REQ_PL_CM34,
        "per_led_cm5": REQ_PL_CM5,
    }
    raw = {}
    for name, req in tables.items():
        buf = request_table(dev_cmd, dev_data, req)
        raw[name] = buf
        if buf is not None:
            with open(os.path.join(BACKUP_DIR, f"{name}.bin"), "wb") as f:
                f.write(buf)
        print(f"table {name:<12} read: {buf is not None}")

    decode_modes(raw["modes"], result)
    decode_colors(raw["colors"], result)
    decode_perled(raw["per_led_cm12"], result, "per_led_cm12")
    decode_perled(raw["per_led_cm34"], result, "per_led_cm34")
    decode_perled(raw["per_led_cm5"], result, "per_led_cm5")

    with open(os.path.join(BACKUP_DIR, "probe_result.json"), "w") as f:
        json.dump(result, f, indent=2)

    print("\n--- decoded modes table ---")
    print("per-key custom mode flag [20] :", result.get("per_key_flag_byte20"))
    print("current mode [21]             :", result.get("current_mode_byte21"))
    for m in result.get("device_modes", []):
        print(f"  mode {m['idx']:>2}: color={m['color_preset']} brightness={m['brightness']} "
              f"speed={m['speed']} dir_left={m['direction_left']}")
    print("\nmode 0 color presets (B,G,R):", result.get("mode0_presets"))
    for key in ("per_led_cm12", "per_led_cm34", "per_led_cm5"):
        d = result.get(key, {})
        print(f"{key}: read={d.get('read')} nonzero={d.get('nonzero_bytes_in_396')} head={d.get('head')}")

    print(f"\nraw backups -> {BACKUP_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
