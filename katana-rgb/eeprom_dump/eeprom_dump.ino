// Scan + full AT24C02 dump. Reports which addresses ACK, a 16-byte signature
// from each, then two full 256-byte reads from the first address.
// ESP32 WROOM-32: SDA = GPIO21, SCL = GPIO22.

#include <Wire.h>
#include <string.h>

static const int SDA_PIN = 21;
static const int SCL_PIN = 22;

uint8_t buf1[256];
uint8_t buf2[256];

bool readChunk(uint8_t addr, uint8_t memAddr, uint8_t *dst, size_t len) {
  Wire.beginTransmission(addr);
  Wire.write(memAddr);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, (int)len) != (int)len) return false;
  for (size_t i = 0; i < len; i++) dst[i] = Wire.read();
  return true;
}

bool readImage(uint8_t addr, uint8_t *dst) {
  for (int a = 0; a < 256; a += 16) {
    if (!readChunk(addr, (uint8_t)a, dst + a, 16)) return false;
  }
  return true;
}

void dumpHex(const char *label, const uint8_t *p) {
  Serial.printf("\n--- %s ---\n", label);
  for (int i = 0; i < 256; i++) {
    if (i % 16 == 0) Serial.printf("%02X: ", i);
    Serial.printf("%02X ", p[i]);
    if (i % 16 == 15) Serial.println();
  }
}

void runDump() {
  Serial.println("\n=== Katana EEPROM ===");

  uint8_t found[8];
  int nfound = 0;
  for (uint8_t a = 0x50; a <= 0x57; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) found[nfound++] = a;
  }

  Serial.printf("ACK addresses: %d\n", nfound);
  for (int i = 0; i < nfound; i++) {
    uint8_t sig[16];
    if (readChunk(found[i], 0, sig, 16)) {
      Serial.printf("0x%02X: ", found[i]);
      for (int j = 0; j < 16; j++) Serial.printf("%02X ", sig[j]);
      Serial.println();
    } else {
      Serial.printf("0x%02X: read failed\n", found[i]);
    }
  }

  if (nfound == 0) {
    Serial.println("No EEPROM answered.");
    return;
  }

  if (!readImage(found[0], buf1)) { Serial.println("Full read #1 FAILED"); return; }
  delay(20);
  if (!readImage(found[0], buf2)) { Serial.println("Full read #2 FAILED"); return; }

  dumpHex("DUMP 1", buf1);
  dumpHex("DUMP 2", buf2);
  Serial.println(memcmp(buf1, buf2, 256) == 0 ? "MATCH" : "MISMATCH");
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  Serial.println("\nESP32 EEPROM reader ready (read-only)");
}

void loop() {
  runDump();
  delay(5000);
}
