#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

#include <M5Unified.h>
#include <M5EchoBase.h>
#include <pgmspace.h>
#include <cstring>

#include "reja_abierta_wav.h"

// --------------------------------------------------
// Wi-Fi configuration
// --------------------------------------------------
const char* WIFI_SSID = "x1";
const char* WIFI_PASSWORD = ".Luc2121Luc.";

// --------------------------------------------------
// AtomS3R + Atomic Audio-3.5 Base pins
// --------------------------------------------------
#define PIN_I2C_SDA 38
#define PIN_I2C_SCL 39
#define PIN_I2S_DIN 7
#define PIN_I2S_WS 6
#define PIN_I2S_DOUT 5
#define PIN_I2S_BCK 8

// --------------------------------------------------
// Forward declarations to avoid Arduino .ino issues
// --------------------------------------------------
struct WavInfo;
struct AudioResource;

bool parseWav(const uint8_t* wav, size_t len, WavInfo& out);
const AudioResource* findAudioResource(const String& name);
bool playResource(const AudioResource* resource);
bool startPlayback(const AudioResource* resource);
void playbackTask(void* param);

void showMessage(const char* msg);
bool initAudioIfNeeded(uint32_t sampleRate);
void connectWiFi();
void setupHttpServer();

void handleRoot();
void handleHealth();
void handleResources();
void handlePlayByPath();
void handleNotFound();
void sendJson(int statusCode, const String& json);

// --------------------------------------------------
// Global objects
// --------------------------------------------------
WebServer server(80);

// This works with current M5Stack AtomS3R Arduino setup.
M5EchoBase echobase;

// --------------------------------------------------
// WAV parsing
// --------------------------------------------------
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

    // WAV chunks are word-aligned.
    if (chunkSize & 1) {
      pos++;
    }
  }

  return foundFmt && foundData;
}

// --------------------------------------------------
// Audio resource map
// --------------------------------------------------
struct AudioResource {
  const char* name;
  const uint8_t* wav;
  size_t wavLen;
};

// IMPORTANT:
// These names must match what xxd generated in reja_abierta_wav.h.
//
// If your header contains different names, adjust them here.
// Example generated names usually look like:
//   unsigned char reja_abierta_atom_wav[] = { ... };
//   unsigned int reja_abierta_atom_wav_len = ...;
AudioResource AUDIO_RESOURCES[] = {
  {
    "reja_abierta",
    reja_abierta_atom_wav,
    reja_abierta_atom_wav_len
  }
};

const size_t AUDIO_RESOURCE_COUNT =
  sizeof(AUDIO_RESOURCES) / sizeof(AUDIO_RESOURCES[0]);

const AudioResource* findAudioResource(const String& name) {
  for (size_t i = 0; i < AUDIO_RESOURCE_COUNT; i++) {
    if (name == AUDIO_RESOURCES[i].name) {
      return &AUDIO_RESOURCES[i];
    }
  }

  return nullptr;
}

// --------------------------------------------------
// Playback state
// --------------------------------------------------
volatile bool isPlaying = false;
TaskHandle_t playbackTaskHandle = nullptr;
uint32_t currentSampleRate = 0;

// --------------------------------------------------
// UI helpers
// --------------------------------------------------
void showMessage(const char* msg) {
  Serial.println(msg);

  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(5, 20);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(WHITE);
  M5.Display.println(msg);
}

// --------------------------------------------------
// Audio helpers
// --------------------------------------------------
bool initAudioIfNeeded(uint32_t sampleRate) {
  if (currentSampleRate == sampleRate) {
    return true;
  }

  Serial.printf("Initializing EchoBase at %lu Hz\n", sampleRate);

  bool ok = echobase.init(
    sampleRate,
    PIN_I2C_SDA,
    PIN_I2C_SCL,
    PIN_I2S_DIN,
    PIN_I2S_WS,
    PIN_I2S_DOUT,
    PIN_I2S_BCK,
    Wire
  );

  if (!ok) {
    Serial.println("EchoBase init failed");
    currentSampleRate = 0;
    return false;
  }

  echobase.setSpeakerVolume(70);
  echobase.setMute(false);

  currentSampleRate = sampleRate;
  return true;
}

bool playResource(const AudioResource* resource) {
  if (resource == nullptr) {
    return false;
  }

  WavInfo wav;

  if (!parseWav(resource->wav, resource->wavLen, wav)) {
    Serial.println("Invalid WAV");
    return false;
  }

  Serial.printf(
    "Playing resource '%s': %lu Hz, %u ch, %u bits, %lu bytes\n",
    resource->name,
    wav.sampleRate,
    wav.channels,
    wav.bitsPerSample,
    wav.dataSize
  );

  if (wav.audioFormat != 1) {
    Serial.println("Unsupported WAV: must be PCM");
    return false;
  }

  if (wav.bitsPerSample != 16) {
    Serial.println("Unsupported WAV: must be 16-bit");
    return false;
  }

  if (wav.sampleRate < 16000 || wav.sampleRate > 64000) {
    Serial.println("Unsupported sample rate");
    return false;
  }

  if (!initAudioIfNeeded(wav.sampleRate)) {
    return false;
  }

  char msg[64];
  snprintf(msg, sizeof(msg), "Playing: %s", resource->name);
  showMessage(msg);

  echobase.setMute(false);
  echobase.play(wav.data, wav.dataSize);

  showMessage("Ready");
  return true;
}

// Runs outside the HTTP handler, so the server stays responsive.
void playbackTask(void* param) {
  const AudioResource* resource = (const AudioResource*)param;

  bool ok = playResource(resource);

  if (!ok) {
    Serial.println("Playback failed");
  }

  isPlaying = false;
  playbackTaskHandle = nullptr;

  vTaskDelete(nullptr);
}

bool startPlayback(const AudioResource* resource) {
  if (isPlaying) {
    return false;
  }

  isPlaying = true;

  BaseType_t ok = xTaskCreate(
    playbackTask,
    "audio-playback",
    8192,
    (void*)resource,
    1,
    &playbackTaskHandle
  );

  if (ok != pdPASS) {
    isPlaying = false;
    playbackTaskHandle = nullptr;
    Serial.println("Failed to create playback task");
    return false;
  }

  return true;
}

// --------------------------------------------------
// HTTP helpers
// --------------------------------------------------
void sendJson(int statusCode, const String& json) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(statusCode, "application/json", json);
}

// --------------------------------------------------
// HTTP handlers
// --------------------------------------------------
void handleRoot() {
  String json = "{";
  json += "\"message\":\"AtomS3R Audio REST Server\",";
  json += "\"endpoints\":[";
  json += "\"GET /health\",";
  json += "\"GET /resources\",";
  json += "\"GET /play/reja_abierta\"";
  json += "]";
  json += "}";

  sendJson(200, json);
}

void handleHealth() {
  String json = "{";
  json += "\"status\":\"ok\",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"isPlaying\":";
  json += isPlaying ? "true" : "false";
  json += "}";

  sendJson(200, json);
}

void handleResources() {
  String json = "{";
  json += "\"resources\":[";

  for (size_t i = 0; i < AUDIO_RESOURCE_COUNT; i++) {
    if (i > 0) json += ",";
    json += "\"";
    json += AUDIO_RESOURCES[i].name;
    json += "\"";
  }

  json += "]}";

  sendJson(200, json);
}

void handlePlayByPath() {
  String uri = server.uri();

  const String prefix = "/play/";

  if (!uri.startsWith(prefix)) {
    sendJson(404, "{\"error\":\"not_found\"}");
    return;
  }

  String resourceName = uri.substring(prefix.length());

  const AudioResource* resource = findAudioResource(resourceName);

  if (resource == nullptr) {
    String json = "{";
    json += "\"error\":\"unknown_resource\",";
    json += "\"resource\":\"" + resourceName + "\"";
    json += "}";
    sendJson(404, json);
    return;
  }

  // No queueing. If currently playing, reject immediately.
  if (isPlaying) {
    sendJson(
      429,
      "{\"error\":\"audio_busy\",\"message\":\"audio_is_already_playing\"}"
    );
    return;
  }

  if (!startPlayback(resource)) {
    sendJson(
      500,
      "{\"error\":\"playback_start_failed\"}"
    );
    return;
  }

  String json = "{";
  json += "\"status\":\"playing\",";
  json += "\"resource\":\"" + resourceName + "\"";
  json += "}";

  sendJson(202, json);
}

void handleNotFound() {
  String uri = server.uri();

  if (uri.startsWith("/play/")) {
    handlePlayByPath();
    return;
  }

  String json = "{";
  json += "\"error\":\"not_found\",";
  json += "\"path\":\"" + uri + "\"";
  json += "}";

  sendJson(404, json);
}

// --------------------------------------------------
// Wi-Fi setup
// --------------------------------------------------
void connectWiFi() {
  showMessage("Connecting WiFi...");

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);

  int attempts = 0;

  while (WiFi.status() != WL_CONNECTED && attempts < 60) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    showMessage("WiFi failed");
    Serial.println("WiFi connection failed");

    while (true) {
      delay(1000);
    }
  }

  IPAddress ip = WiFi.localIP();

  Serial.print("WiFi connected. IP: ");
  Serial.println(ip);

  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(5, 10);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(WHITE);
  M5.Display.println("WiFi OK");
  M5.Display.println(ip.toString());
  M5.Display.println("Ready");
}

void setupHttpServer() {
  server.on("/", HTTP_GET, handleRoot);
  server.on("/health", HTTP_GET, handleHealth);
  server.on("/resources", HTTP_GET, handleResources);

  // Dynamic /play/<resource> route handled here.
  server.onNotFound(handleNotFound);

  server.begin();

  Serial.println("HTTP server started");
}

// --------------------------------------------------
// Arduino setup / loop
// --------------------------------------------------
void setup() {
  auto cfg = M5.config();
  cfg.serial_baudrate = 115200;
  M5.begin(cfg);

  M5.Display.setRotation(0);
  M5.Display.fillScreen(BLACK);
  M5.Display.setTextColor(WHITE);
  M5.Display.setTextSize(1);

  Serial.println();
  Serial.println("Starting AtomS3R Audio REST Server");

  // Validate the embedded WAV at startup.
  WavInfo wav;

  if (!parseWav(reja_abierta_atom_wav, reja_abierta_atom_wav_len, wav)) {
    showMessage("Invalid WAV header");

    while (true) {
      delay(1000);
    }
  }

  Serial.printf(
    "Loaded WAV: %lu Hz, %u ch, %u bits\n",
    wav.sampleRate,
    wav.channels,
    wav.bitsPerSample
  );

  if (!initAudioIfNeeded(wav.sampleRate)) {
    showMessage("Audio init failed");

    while (true) {
      delay(1000);
    }
  }

  connectWiFi();
  setupHttpServer();

  showMessage("Ready");

  Serial.println("Try:");
  Serial.print("curl http://");
  Serial.print(WiFi.localIP());
  Serial.println("/play/reja_abierta");
}

void loop() {
  M5.update();
  server.handleClient();

  // Optional physical button test.
  // Also no queueing: button is ignored while audio is playing.
  if (M5.BtnA.wasPressed()) {
    if (!isPlaying) {
      startPlayback(&AUDIO_RESOURCES[0]);
    } else {
      Serial.println("Button ignored: audio busy");
    }
  }

  delay(5);
}