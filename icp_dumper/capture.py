"""Capture the ESP32 ICP dump over serial and save it as a .bin.

Usage: python capture.py COM6 flash_dump.bin
Requires: pip install pyserial
The script sends 'r' itself and waits for DUMP-END.
"""
import sys
import time

import serial

port = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else "flash_dump.bin"
cmd = sys.argv[3] if len(sys.argv) > 3 else "r"

ser = serial.Serial(port, 115200, timeout=1)
ser.dtr = False
ser.rts = False
time.sleep(2)
ser.reset_input_buffer()
ser.write(cmd.encode())

data = {}
deadline = time.time() + 600
while time.time() < deadline:
    line = ser.readline().decode(errors="replace").strip()
    if not line:
        continue
    print(line)
    if line == "DUMP-END":
        break
    if len(line) > 5 and line[4] == ":":
        try:
            addr = int(line[:4], 16)
            data[addr] = bytes.fromhex(line[5:])
        except ValueError:
            pass

if not data:
    sys.exit("No dump data received")

size = max(data) + 16
buf = bytearray(b"\xff" * size)
missing = 0
for a in range(0, size, 16):
    if a in data and len(data[a]) == 16:
        buf[a:a + 16] = data[a]
    else:
        missing += 1
open(out, "wb").write(buf)
print(f"Saved {out}: {size} bytes, {missing} missing chunks")
