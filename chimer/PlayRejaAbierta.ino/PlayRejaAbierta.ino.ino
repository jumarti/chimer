#include <Arduino.h>
#include <M5Unified.h>
#include <M5EchoBase.h>
#include <pgmspace.h>
#include <cstring>

#include "reja_abierta_wav.h"

// AtomS3R + Atomic Audio-3.5 Base pins
#define PIN_I2C_SDA 38
#define PIN_I2C_SCL 39
#define PIN_I2S_DIN 7
#define PIN_I2S_WS 6
#define PIN_I2S_DOUT 5
#define PIN_I2S_BCK 8

M5EchoBase echobase;

struct WavInfo {
  const uint8_t* data;
  uint32_t dataSize;
  uint32_t sampleRate;
  uint16_t channels;
  uint16_t bitsPerSample;
  uint16_t audioFormat;
};

static uint16_t read16(const uint8_t* p) {
  return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read32(const uint8_t* p) {
  return (uint32_t)p[0] |
         ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}

bool parseWav(const uint8_t* wav, size_t len, WavInfo& out) {
  if (len < 44) return false;
  if (memcmp(wav, "RIFF", 4) != 0) return false;
  if (memcmp(wav + 8, "WAVE", 4) != 0) return false;

  bool foundFmt = false;
  bool foundData = false;

  size_t pos = 12;

  while (pos + 8 <= len) {
    const uint8_t* chunk = wav + pos;
    uint32_t chunkSize = read32(chunk + 4);
    pos += 8;

    if (pos + chunkSize > len) return false;

    if (memcmp(chunk, "fmt ", 4) == 0) {
      out.audioFormat = read16(wav + pos + 0);
      out.channels = read16(wav + pos + 2);
      out.sampleRate = read32(wav + pos + 4);
      out.bitsPerSample = read16(wav + pos + 14);
      foundFmt = true;
    }

    if (memcmp(chunk, "data", 4) == 0) {
      out.data = wav + pos;
      out.dataSize = chunkSize;
      foundData = true;
    }

    pos += chunkSize;

    // WAV chunks are word-aligned
    if (chunkSize & 1) {
      pos++;
    }
  }

  return foundFmt && foundData;
}

void showMessage(const char* msg) {
  Serial.println(msg);

  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(5, 20);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(WHITE);
  M5.Display.println(msg);
}

void playAlert() {
  WavInfo wav;

  if (!parseWav(reja_abierta_atom_wav, reja_abierta_atom_wav_len, wav)) {
    showMessage("Invalid WAV");
    return;
  }

  Serial.printf("WAV: %lu Hz, %u ch, %u bits, %lu bytes\n",
                wav.sampleRate,
                wav.channels,
                wav.bitsPerSample,
                wav.dataSize);

  if (wav.audioFormat != 1) {
    showMessage("WAV must be PCM");
    return;
  }

  if (wav.bitsPerSample != 16) {
    showMessage("WAV must be 16-bit");
    return;
  }

  if (wav.sampleRate < 16000 || wav.sampleRate > 64000) {
    showMessage("Bad sample rate");
    return;
  }

  showMessage("Playing...");
  echobase.setMute(false);
  echobase.play(wav.data, wav.dataSize);
  showMessage("Press button");
}

void setup() {
  auto cfg = M5.config();
  cfg.serial_baudrate = 115200;
  M5.begin(cfg);

  M5.Display.setRotation(0);
  M5.Display.fillScreen(BLACK);
  M5.Display.setTextColor(WHITE);
  M5.Display.setTextSize(1);

  Serial.println("Starting AtomS3R Atomic Audio WAV player");

  WavInfo wav;
  if (!parseWav(reja_abierta_atom_wav, reja_abierta_atom_wav_len, wav)) {
    showMessage("Invalid WAV file");
    while (true) {
      delay(1000);
    }
  }

  Serial.printf("Detected WAV: %lu Hz, %u channels, %u bits\n",
                wav.sampleRate,
                wav.channels,
                wav.bitsPerSample);

  bool ok = echobase.init(
    wav.sampleRate,
    PIN_I2C_SDA,
    PIN_I2C_SCL,
    PIN_I2S_DIN,
    PIN_I2S_WS,
    PIN_I2S_DOUT,
    PIN_I2S_BCK,
    Wire
  );

  if (!ok) {
    showMessage("EchoBase init failed");
    while (true) {
      delay(1000);
    }
  }

  echobase.setSpeakerVolume(70);
  echobase.setMute(false);

  showMessage("Press button");

  // Optional: play once immediately at boot
  delay(500);
  playAlert();
}

void loop() {
  M5.update();

  if (M5.BtnA.wasPressed()) {
    playAlert();
  }

  delay(10);
}