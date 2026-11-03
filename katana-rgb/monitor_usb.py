r"""
USB watcher: prints any USB or HID device that appears/disappears.
Run it, then replug the keyboard while it is running.
Catches even brief appearances (crash-reboot loops, bootloader modes).
"""

import re
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from hidwin import enumerate_hid_paths, enumerate_usb_paths  # noqa: E402

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0


def snapshot():
    usb = set(enumerate_usb_paths())
    hidp = set(enumerate_hid_paths())
    return usb, hidp


def label(path):
    m = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", path.lower())
    vid, pid = (m.group(1), m.group(2)) if m else ("?", "?")
    short = path.split("#")
    tag = short[1] if len(short) > 1 else path
    return f"VID_{vid.upper()}:PID_{pid.upper()} {tag}"


def main():
    print(f"watching USB + HID for {DURATION:.0f}s ... (replug the keyboard now)")
    usb, hidp = snapshot()
    print(f"initial: usb={len(usb)} hid={len(hidp)}  258a devices: "
          f"{sum(1 for p in list(usb) + list(hidp) if 'vid_258a' in p.lower())}")
    t0 = time.time()
    while time.time() - t0 < DURATION:
        time.sleep(0.25)
        usb2, hidp2 = snapshot()
        for p in usb2 - usb:
            print(f"[{time.time() - t0:6.1f}s] + USB  {label(p)}")
        for p in usb - usb2:
            print(f"[{time.time() - t0:6.1f}s] - USB  {label(p)}")
        for p in hidp2 - hidp:
            print(f"[{time.time() - t0:6.1f}s] + HID  {label(p)}")
        for p in hidp - hidp2:
            print(f"[{time.time() - t0:6.1f}s] - HID  {label(p)}")
        usb, hidp = usb2, hidp2
    print("watch ended.")
    n = sum(1 for p in list(usb) + list(hidp) if "vid_258a" in p.lower())
    print(f"258A devices currently present: {n}")


if __name__ == "__main__":
    main()
