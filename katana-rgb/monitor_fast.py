r"""
Fast USB watcher: polls every 30 ms, unbuffered output.
Catches brief appearances (bootloader windows, crash-reboot loops).
Optionally dumps HID caps of anything that appears.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidwin import enumerate_usb_paths, enumerate_hid_paths, HidHandle  # noqa: E402

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
INTERVAL = 0.03


def snap():
    return set(enumerate_usb_paths()), set(enumerate_hid_paths())


def label(path):
    m = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", path.lower())
    vid, pid = (m.group(1), m.group(2)) if m else ("?", "?")
    tag = path.split("#")[1] if "#" in path else path
    return f"VID_{vid.upper()}:PID_{pid.upper()} {tag}"


def main():
    print(f"fast watch {DURATION:.0f}s @ {INTERVAL * 1000:.0f}ms", flush=True)
    usb, hidp = snap()
    print(f"initial usb={len(usb)} hid={len(hidp)} 258a={sum('vid_258a' in p.lower() for p in usb | hidp)}",
          flush=True)
    t0 = time.time()
    while time.time() - t0 < DURATION:
        time.sleep(INTERVAL)
        usb2, hidp2 = snap()
        usb, hidp = usb2, hidp
        for p in usb2 - usb:
            print(f"[{time.time() - t0:7.2f}s] + USB {label(p)}", flush=True)
        for p in usb - usb2:
            print(f"[{time.time() - t0:7.2f}s] - USB {label(p)}", flush=True)
        for p in hidp2 - hidp:
            print(f"[{time.time() - t0:7.2f}s] + HID {label(p)}", flush=True)
            if "vid_258a" in p.lower() or True:
                try:
                    h = HidHandle(p)
                    print(f"           usage_page=0x{h.caps.UsagePage:04X} "
                          f"usage=0x{h.caps.Usage:04X} feat={h.caps.FeatureReportByteLength}", flush=True)
                    h.close()
                except OSError:
                    pass
        for p in hidp - hidp2:
            print(f"[{time.time() - t0:7.2f}s] - HID {label(p)}", flush=True)
    print("fast watch ended.", flush=True)


if __name__ == "__main__":
    main()
