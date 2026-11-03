/*
  ESP32 read-only ICP flash dumper for SinoWealth SH68F89 / BYK816 (EvoFox Katana Pro)

  Port of the ICP read protocol from gashtaan/sinowealth-8051-dumper (GPL-3.0).
  This sketch NEVER writes or erases flash. It only reads and prints it.

  WIRING (keyboard USB cable UNPLUGGED the whole time, ESP32 is powered from the PC):

    Keyboard JTAG pad   ESP32
    -----------------   -------------------------------------------
    GND                 GND
    TMS                 GPIO18
    TCK                 GPIO19
    TDI                 GPIO21
    TDO                 GPIO22   (input)
    VDD                 GPIO23 directly (default, POWER_DIRECT 1, no MOSFET needed)
                        or a MOSFET-switched 3V3 (POWER_DIRECT 0, see below)

  Direct mode: before connecting, measure resistance VDD to GND on the
  keyboard (USB unplugged, multimeter). If it is below ~150 ohm, do NOT
  use direct mode.

  MOSFET mode (POWER_DIRECT 0): the chip must be power-cycled and talked to
  within a few tens of ms of power-up:

      ESP32 3V3 ---+------------ S
                   |            |   P-channel MOSFET (AO3401 / IRLML6401 / IRF9540 ...)
                [10k]           |
                   |            D ----> keyboard VDD pad
      GPIO23 ------+------------ G

  GPIO23 LOW = target powered, floating (input) = target off.
  The keyboard's own 5 V reading only exists while its USB is plugged in.

  STEP 1 (try first): source on ESP32 3V3 -> target runs at 3.3 V, all
  signals are 3.3 V logic, everything is safe for the ESP32.

  STEP 2 (only if STEP 1 never connects): run the target at 5 V.
    - MOSFET source to ESP32 5V/VIN pin (USB 5 V), same 10k gate pull-up to 5V.
    - TDO MUST go through a divider (1k in series, 2k to GND) before GPIO22.
    - TMS/TCK/TDI should go through a 74HCT125/74HCT245 powered from 5 V
      (3.3 V in, 5 V out) because 3.3 V is marginal for a 5 V part.
    Never connect the keyboard USB at the same time.

  USAGE: open serial monitor at 115200, send:
    t  connect test only
    r  connect + dump 64 KB flash as hex lines (save the log, convert with capture.py)
    i  connect + print JTAG ID
    f  like r, but dump even if address 0 reads blank (all 00/FF)
*/

#include <Arduino.h>
#include <soc/gpio_reg.h>

static const uint8_t PIN_TMS = 18;
static const uint8_t PIN_TCK = 19;
static const uint8_t PIN_TDI = 21;
static const uint8_t PIN_TDO = 22;
static const uint8_t PIN_PWR = 23;  // gate of P-MOSFET, LOW = on

static const uint32_t FLASH_SIZE = 65536;
static const uint8_t CHUNK = 16;

#define setBit(p) REG_WRITE(GPIO_OUT_W1TS_REG, 1UL << (p))
#define clrBit(p) REG_WRITE(GPIO_OUT_W1TC_REG, 1UL << (p))
#define getBit(p) ((REG_READ(GPIO_IN_REG) >> (p)) & 1)

enum Mode : uint8_t { M_ERROR = 0, M_READY = 1, M_ICP = 150, M_JTAG = 165 };
enum : uint8_t {
  ICP_SET_IB_OFFSET_L = 0x40,
  ICP_SET_IB_OFFSET_H = 0x41,
  ICP_GET_IB_OFFSET = 0x43,
  ICP_READ_FLASH = 0x44,
  ICP_PING = 0x49,
  ICP_SET_XPAGE = 0x4C,
};
static const uint8_t JTAG_IDCODE = 14;

static Mode mode = M_ERROR;
static uint8_t chipType = 2;  // 2 = plain 16-bit; 4 = also sends XPAGE

// POWER_DIRECT 1: VDD pad is fed straight from PIN_PWR (no MOSFET). Only OK
// because the idle chip draws ~10-20 mA; GPIO is set to maximum drive strength.
// POWER_DIRECT 0: PIN_PWR drives a P-MOSFET gate (see header comment).
#define POWER_DIRECT 1

#if POWER_DIRECT
static void targetOff() {
  digitalWrite(PIN_PWR, LOW);  // also discharges the target's caps
  pinMode(PIN_PWR, OUTPUT);
  clrBit(PIN_TMS);
  clrBit(PIN_TCK);
  clrBit(PIN_TDI);
}

static void targetOn() {
  gpio_set_drive_capability((gpio_num_t)PIN_PWR, GPIO_DRIVE_CAP_3);
  digitalWrite(PIN_PWR, HIGH);
}
#else
// Open-drain style: floating lets the 10k pull the gate to the MOSFET source
// (fully off even when the source is 5 V), driving LOW turns it on.
static void targetOff() {
  pinMode(PIN_PWR, INPUT);
  clrBit(PIN_TMS);
  clrBit(PIN_TCK);
  clrBit(PIN_TDI);
}

static void targetOn() {
  digitalWrite(PIN_PWR, LOW);
  pinMode(PIN_PWR, OUTPUT);
}
#endif

static void pulseClock() {
  delayMicroseconds(1);
  setBit(PIN_TCK);
  delayMicroseconds(1);
  clrBit(PIN_TCK);
}

static bool nextState(bool tms) {
  if (tms) setBit(PIN_TMS); else clrBit(PIN_TMS);
  setBit(PIN_TCK);
  delayMicroseconds(2);
  bool b = getBit(PIN_TDO);
  clrBit(PIN_TCK);
  delayMicroseconds(2);
  return b;
}

static bool nextState(bool tms, bool out) {
  if (out) setBit(PIN_TDI); else clrBit(PIN_TDI);
  return nextState(tms);
}

static void sendICP(uint8_t v) {
  for (uint8_t m = 0x80; m; m >>= 1) {
    if (v & m) setBit(PIN_TDI); else clrBit(PIN_TDI);
    pulseClock();
  }
  pulseClock();
  clrBit(PIN_TDI);
}

static uint8_t recvICP() {
  uint8_t v = 0;
  for (uint8_t m = 1; m; m <<= 1) {
    pulseClock();
    if (getBit(PIN_TDO)) v |= m;
  }
  pulseClock();
  return v;
}

static void startMode() {
  clrBit(PIN_TCK);
  delayMicroseconds(2);
  for (uint8_t m = 0x80; m; m >>= 1) {
    if ((uint8_t)mode & m) setBit(PIN_TDI); else clrBit(PIN_TDI);
    setBit(PIN_TCK); delayMicroseconds(2);
    clrBit(PIN_TCK); delayMicroseconds(2);
  }
  for (uint8_t i = 0; i < 2; i++) {
    setBit(PIN_TCK); delayMicroseconds(2);
    clrBit(PIN_TCK); delayMicroseconds(2);
  }
}

static void icpReset() {
  if (mode == M_ERROR) return;
  if (mode == M_JTAG) {
    for (uint8_t n = 0; n < 35; n++) nextState(1);
    setBit(PIN_TCK);
    clrBit(PIN_TMS);
  } else {
    setBit(PIN_TCK);
    setBit(PIN_TMS);
    delayMicroseconds(2);
    clrBit(PIN_TMS);
    delayMicroseconds(2);
  }
  mode = M_READY;
}

static void pingICP() {
  if (mode != M_ICP) return;
  sendICP(ICP_PING);
  sendICP(0xFF);
}

static void sendBits(uint8_t n, uint32_t value) {
  while (n-- > 0) {
    nextState(n == 0, value & 1);
    value >>= 1;
  }
  clrBit(PIN_TDI);
}

static uint32_t receiveBits(uint8_t n) {
  uint32_t value = 0;
  while (n-- > 0) {
    value <<= 1;
    value |= nextState(n == 0);
  }
  return value;
}

static void sendInstruction(uint8_t v) {
  nextState(0); nextState(1); nextState(1); nextState(0); nextState(0);
  sendBits(4, v);
  nextState(1); nextState(0);
}

static void sendData(uint8_t n, uint32_t v) {
  nextState(1); nextState(0); nextState(0);
  sendBits(n, v);
  nextState(1); nextState(0); nextState(0);
}

static uint32_t receiveData(uint8_t n) {
  nextState(1); nextState(0); nextState(0);
  uint32_t v = receiveBits(n);
  nextState(1); nextState(0);
  return v;
}

static void switchMode(Mode m) {
  if (mode == m) return;
  if (mode != M_READY) icpReset();
  mode = m;
  startMode();
  if (mode == M_ICP) {
    delayMicroseconds(800);
    pingICP();
  } else if (mode == M_JTAG) {
    for (uint8_t n = 0; n < 8; n++) nextState(1);
    sendInstruction(2);
    sendData(4, 4);
    sendInstruction(3);
    sendData(23, 0x403000);
    delayMicroseconds(50);
    sendData(23, 0x402000);
    sendData(23, 0x400000);
    static const uint32_t bp[] = {0x630000, 0x670000, 0x6B0000, 0x6F0000,
                                  0x730000, 0x770000, 0x7B0000, 0x7F0000};
    for (uint32_t v : bp) sendData(23, v);
    sendInstruction(2);
    sendData(4, 1);
    sendInstruction(12);
  }
}

// Must run right after targetOn(): the chip only accepts the entry sequence
// for a short window after power-up.
static void icpEnterSequence() {
  setBit(PIN_TCK); setBit(PIN_TDI); setBit(PIN_TMS);
  delayMicroseconds(500);

  clrBit(PIN_TCK); delayMicroseconds(1);
  setBit(PIN_TCK); delayMicroseconds(50);

  for (uint8_t n = 0; n < 165; n++) {
    clrBit(PIN_TMS); delayMicroseconds(2);
    setBit(PIN_TMS); delayMicroseconds(2);
  }
  for (uint8_t n = 0; n < 105; n++) {
    clrBit(PIN_TDI); delayMicroseconds(2);
    setBit(PIN_TDI); delayMicroseconds(2);
  }
  for (uint8_t n = 0; n < 90; n++) {
    clrBit(PIN_TCK); delayMicroseconds(2);
    setBit(PIN_TCK); delayMicroseconds(2);
  }
  for (uint16_t n = 0; n < 25600; n++) {
    clrBit(PIN_TMS); delayMicroseconds(2);
    setBit(PIN_TMS); delayMicroseconds(2);
  }
  delayMicroseconds(8);
  clrBit(PIN_TMS);

  mode = M_ICP;
  startMode();

  for (uint16_t n = 0; n < 25600; n++) {
    setBit(PIN_TCK); delayMicroseconds(2);
    clrBit(PIN_TCK); delayMicroseconds(2);
  }
  icpReset();
}

static bool checkICP() {
  switchMode(M_ICP);
  sendICP(ICP_SET_IB_OFFSET_L);
  sendICP(0x69);
  sendICP(ICP_SET_IB_OFFSET_H);
  sendICP(0xFF);
  sendICP(ICP_GET_IB_OFFSET);
  uint8_t b = recvICP();
  (void)recvICP();
  return b == 0x69;
}

static uint16_t getJtagID() {
  switchMode(M_JTAG);
  sendInstruction(JTAG_IDCODE);
  return (uint16_t)receiveData(16);
}

static void readFlashICP(uint8_t *buf, uint8_t n, uint32_t addr) {
  switchMode(M_ICP);
  if (chipType != 1) {
    sendICP(0x46); sendICP(0xFE); sendICP(0xFF);
  }
  sendICP(ICP_SET_IB_OFFSET_L); sendICP(addr & 0xFF);
  sendICP(ICP_SET_IB_OFFSET_H); sendICP((addr >> 8) & 0xFF);
  if (chipType == 4 || chipType == 7) {
    sendICP(ICP_SET_XPAGE); sendICP((addr >> 16) & 0xFF);
  }
  sendICP(ICP_READ_FLASH);
  for (uint8_t i = 0; i < n; i++) buf[i] = recvICP();
  icpReset();
}

// Opcodes/flow ported from carlossless/sinodude firmware (icp_write_region),
// chip type 2 (SH68F89). Programs bytes in place, no erase: it can only clear bits.
static uint8_t exchangeICP(uint8_t out) {
  uint8_t got = 0;
  for (uint8_t i = 0; i < 8; i++) {
    if (out & (0x80 >> i)) setBit(PIN_TDI); else clrBit(PIN_TDI);
    delayMicroseconds(2);
    setBit(PIN_TCK); delayMicroseconds(2);
    clrBit(PIN_TCK);
    if (getBit(PIN_TDO)) got |= 1 << i;
  }
  delayMicroseconds(2);
  setBit(PIN_TCK); delayMicroseconds(2);
  clrBit(PIN_TCK);
  clrBit(PIN_TDI);
  return got;
}

static bool programICP(uint32_t addr, const uint8_t *d, uint16_t n) {
  if (n < 3) return false;
  switchMode(M_ICP);
  sendICP(0x46); sendICP(0xF0); sendICP(0xFF);
  sendICP(ICP_SET_IB_OFFSET_L); sendICP(addr & 0xFF);
  sendICP(ICP_SET_IB_OFFSET_H); sendICP((addr >> 8) & 0xFF);
  sendICP(0x42); sendICP(d[0]);
  sendICP(0x6E); sendICP(0x15); sendICP(0x0A); sendICP(0x09); sendICP(0x06);
  sendICP(d[1]);
  delayMicroseconds(10);
  sendICP(0x00);
  if (!getBit(PIN_TDO)) Serial.printf("ack low@%d\n", __LINE__);
  for (uint16_t i = 2; i < n; i++) {
    { uint8_t e = exchangeICP(d[i]); if (e != d[i - 2]) { if (i < 6) Serial.printf("echo i=%u got %02X want %02X\n", i, e, d[i - 2]); } }
    delayMicroseconds(5);
    sendICP(0x00);
    if (!getBit(PIN_TDO)) Serial.printf("ack low@%d\n", __LINE__);
  }
  sendICP(0x00);
  sendICP(0xAA);
  if (!getBit(PIN_TDO)) Serial.printf("ack low@%d\n", __LINE__);
  sendICP(0x00);
  sendICP(0x00);
  delayMicroseconds(5);
  return true;
}

static uint8_t eraseSector(uint32_t addr) {
  switchMode(M_ICP);
  sendICP(0x46); sendICP(0xF0); sendICP(0xFF);
  sendICP(ICP_SET_IB_OFFSET_L); sendICP(addr & 0xFF);
  sendICP(ICP_SET_IB_OFFSET_H); sendICP((addr >> 8) & 0xFF);
  sendICP(0x42); sendICP(0x00);
  sendICP(0xE6); sendICP(0x15); sendICP(0x0A); sendICP(0x09); sendICP(0x06); sendICP(0xFF);
  delay(150);
  uint8_t st = 0;
  setBit(PIN_TDI);
  for (uint8_t i = 0; i < 8; i++) {
    delayMicroseconds(2); setBit(PIN_TCK); delayMicroseconds(2); clrBit(PIN_TCK);
    if (getBit(PIN_TDO)) st |= 1 << i;
  }
  delayMicroseconds(2); setBit(PIN_TCK); delayMicroseconds(2); clrBit(PIN_TCK);
  clrBit(PIN_TDI);
  return st;
}

static void forceIspMode() {
  chipType = 2;
  uint8_t b[16];
  readFlashICP(b, 8, 0xEFFB);
  Serial.printf("0xEFFB before: %02X %02X %02X\n", b[0], b[1], b[2]);
  if (b[0] != 0x02) { Serial.println("Unexpected value, aborting"); return; }
  static const uint8_t v[3] = {0x03, 0x86, 0x58};
  for (uint8_t attempt = 1; attempt <= 3; attempt++) {
    programICP(0xEFFB, v, 3);
    icpReset();
    readFlashICP(b, 8, 0xEFFB);
    Serial.printf("attempt %u readback: %02X %02X %02X\n", attempt, b[0], b[1], b[2]);
    if (b[0] == 0x03) { Serial.println("ISP-FORCE-OK"); return; }
  }
  Serial.println("ISP-FORCE-FAILED");
}

// Restores the reset vector (LJMP 0xF000 -> ISP bootloader) at 0x0000.
static void patchResetVector() {
  static const uint8_t vec[3] = {0x02, 0xF0, 0x00};
  chipType = 2;
  uint8_t b[16];
  readFlashICP(b, 16, 0);
  Serial.print("Before:");
  for (uint8_t i = 0; i < 16; i++) Serial.printf(" %02X", b[i]);
  Serial.println();
  if (!memcmp(b, vec, 3)) {
    Serial.println("Reset vector already 02 F0 00, nothing to do");
    return;
  }
  uint8_t st = eraseSector(0);
  icpReset();
  readFlashICP(b, 16, 0);
  Serial.printf("Erase status %02X, after:", st);
  for (uint8_t i = 0; i < 16; i++) Serial.printf(" %02X", b[i]);
  Serial.println();
  for (uint8_t attempt = 1; attempt <= 3; attempt++) {
    uint8_t pg[64]; memset(pg, 0xFF, sizeof pg); memcpy(pg, vec, 3); bool ok = programICP(0, pg, 64);
    icpReset();
    readFlashICP(b, 16, 0);
    Serial.printf("Program attempt %u (%s), readback:", attempt, ok ? "ack" : "no ack");
    for (uint8_t i = 0; i < 16; i++) Serial.printf(" %02X", b[i]);
    Serial.println();
    if (!memcmp(b, vec, 3)) {
      Serial.println("PATCH-OK");
      return;
    }
  }
  Serial.println("PATCH-FAILED");
}

static void releasePins() {
  pinMode(PIN_TMS, INPUT);
  pinMode(PIN_TCK, INPUT);
  pinMode(PIN_TDI, INPUT);
  pinMode(PIN_PWR, INPUT);
}

static bool connectTarget() {
  for (uint8_t attempt = 1; attempt <= 5; attempt++) {
    targetOff();
    mode = M_ERROR;
    delay(500);  // let target caps fully discharge
    targetOn();
    icpEnterSequence();
    if (checkICP()) {
      Serial.printf("Connected (attempt %u)\n", attempt);
      return true;
    }
    Serial.printf("Attempt %u failed\n", attempt);
  }
  return false;
}

static bool selectChipType() {
  static const uint8_t types[] = {2, 4};
  uint8_t b[16];
  for (uint8_t t : types) {
    chipType = t;
    readFlashICP(b, 16, 0);
    Serial.printf("type %u, first bytes:", t);
    for (uint8_t i = 0; i < 16; i++) Serial.printf(" %02X", b[i]);
    Serial.println();
    bool allZero = true, allFF = true;
    for (uint8_t i = 0; i < 16; i++) {
      if (b[i] != 0x00) allZero = false;
      if (b[i] != 0xFF) allFF = false;
    }
    if (!allZero && !allFF) return true;
  }
  return false;
}

static void dumpFlash(bool force) {
  if (!selectChipType()) {
    Serial.println("Flash reads as all 00 / all FF: blank, protected, or wrong chip type");
    if (!force) return;
    chipType = 2;
  }
  Serial.printf("DUMP-BEGIN type=%u size=%lu\n", chipType, (unsigned long)FLASH_SIZE);
  uint8_t b[CHUNK];
  for (uint32_t a = 0; a < FLASH_SIZE; a += CHUNK) {
    readFlashICP(b, CHUNK, a);
    Serial.printf("%04X:", (unsigned)a);
    for (uint8_t i = 0; i < CHUNK; i++) Serial.printf("%02X", b[i]);
    Serial.println();
  }
  Serial.println("DUMP-END");
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TMS, OUTPUT);
  pinMode(PIN_TCK, OUTPUT);
  pinMode(PIN_TDI, OUTPUT);
  pinMode(PIN_TDO, INPUT);
  targetOff();
  delay(500);
  Serial.println("\nSinoWealth ICP read-only dumper. Send: t=test  r=dump  i=JTAG ID");
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c != 't' && c != 'r' && c != 'i' && c != 'f' && c != 'p' && c != 'e') return;

  if (!connectTarget()) {
    Serial.println("Connection failed. Check wiring, USB unplugged, MOSFET polarity, 3.3 V.");
    targetOff();
    return;
  }
  if (c == 'i') {
    uint16_t id = getJtagID();
    Serial.printf("JTAG ID: %04X\n", id);
  } else if (c == 'r' || c == 'f') {
    dumpFlash(c == 'f');
  } else if (c == 'e') {
    forceIspMode();
    targetOff();
    releasePins();
    Serial.println("Pins released. Disconnect ALL wires from the keyboard before plugging USB.");
    return;
  } else if (c == 'p') {
    patchResetVector();
    targetOff();
    releasePins();
    Serial.println("Pins released. Disconnect ALL wires from the keyboard before plugging USB.");
    return;
  } else {
    Serial.println("ICP link OK");
  }
  targetOff();
  Serial.println("Done (target powered off)");
}
