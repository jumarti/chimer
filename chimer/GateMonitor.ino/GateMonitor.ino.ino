#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>

#include <M5Unified.h>
#include <M5EchoBase.h>
#include <pgmspace.h>
#include <cstring>

#include "reja_abierta_wav.h"
#include "short_chime_atomic_wav.h"

// --------------------------------------------------
// Wi-Fi configuration
// --------------------------------------------------
const char* WIFI_SSID     = "x1";
const char* WIFI_PASSWORD = ".Luc2121Luc.";

// --------------------------------------------------
// Gate-service configuration
// --------------------------------------------------
// Set this to your PC's LAN IP before flashing.
const char* GATE_SERVICE_URL = "http://everest.lan:9090/gate";

// How often to poll the gate service (ms).
static const uint32_t POLL_INTERVAL_MS  = 5000;
// How often to repeat the short chime while gate is OPEN (ms).
static const uint32_t CHIME_REPEAT_MS   = 5000;
// How often to repeat the full reja_abierta announcement while gate is OPEN (ms).
static const uint32_t REJA_REPEAT_MS    = 30000;
// How fast the OPEN text blinks (ms per toggle).
static const uint32_t BLINK_MS          = 500;
// How long the button mutes audio when gate is OPEN (ms).
static const uint32_t MUTE_DURATION_MS  = 2UL * 60UL * 1000UL;  // 30 min

// --------------------------------------------------
// AtomS3R + Atomic Audio-3.5 Base pins
// --------------------------------------------------
#define PIN_I2C_SDA 38
#define PIN_I2C_SCL 39
#define PIN_I2S_DIN  7
#define PIN_I2S_WS   6
#define PIN_I2S_DOUT 5
#define PIN_I2S_BCK  8

// --------------------------------------------------
// Gate state
// --------------------------------------------------
enum GateState {
  GATE_UNKNOWN,
  GATE_OPEN,
  GATE_CLOSED,
  GATE_ERROR
};

// --------------------------------------------------
// Forward declarations
// --------------------------------------------------
struct WavInfo;
struct AudioResource;

bool parseWav(const uint8_t* wav, size_t len, WavInfo& out);
const AudioResource* findAudioResource(const String& name);
bool playResource(const AudioResource* resource);
bool startPlayback(const AudioResource* resource);
void playbackTask(void* param);

bool initAudioIfNeeded(uint32_t sampleRate);
void connectWiFi();
void setupHttpServer();

GateState pollGateState();
void applyState(GateState newState);
void renderState(bool blinkOn);
void drawWarningIcon(int cx, int cy, int size, uint16_t color);

void handleRoot();
void handleHealth();
void handleResources();
void handleStatus();
void handlePlayByPath();
void handleNotFound();
void sendJson(int statusCode, const String& json);

// --------------------------------------------------
// Global objects
// --------------------------------------------------
WebServer server(80);
M5EchoBase echobase;

// --------------------------------------------------
// WAV parsing
// --------------------------------------------------
struct WavInfo {
  const uint8_t* data;
  uint32_t       dataSize;
  uint32_t       sampleRate;
  uint16_t       channels;
  uint16_t       bitsPerSample;
  uint16_t       audioFormat;
};

static uint16_t read16(const uint8_t* p) {
  return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read32(const uint8_t* p) {
  return (uint32_t)p[0] |
         ((uint32_t)p[1] <<  8) |
         ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}

bool parseWav(const uint8_t* wav, size_t len, WavInfo& out) {
  if (len < 44) return false;
  if (memcmp(wav, "RIFF", 4) != 0) return false;
  if (memcmp(wav + 8, "WAVE", 4) != 0) return false;

  bool foundFmt  = false;
  bool foundData = false;
  size_t pos = 12;

  while (pos + 8 <= len) {
    const uint8_t* chunk = wav + pos;
    uint32_t chunkSize = read32(chunk + 4);
    pos += 8;
    if (pos + chunkSize > len) return false;

    if (memcmp(chunk, "fmt ", 4) == 0) {
      out.audioFormat   = read16(wav + pos + 0);
      out.channels      = read16(wav + pos + 2);
      out.sampleRate    = read32(wav + pos + 4);
      out.bitsPerSample = read16(wav + pos + 14);
      foundFmt = true;
    }
    if (memcmp(chunk, "data", 4) == 0) {
      out.data     = wav + pos;
      out.dataSize = chunkSize;
      foundData    = true;
    }

    pos += chunkSize;
    if (chunkSize & 1) pos++; // word-align
  }

  return foundFmt && foundData;
}

// --------------------------------------------------
// Audio resource map
// --------------------------------------------------
struct AudioResource {
  const char*    name;
  const uint8_t* wav;
  size_t         wavLen;
};

AudioResource AUDIO_RESOURCES[] = {
  {
    "reja_abierta",
    reja_abierta_atom_wav,
    reja_abierta_atom_wav_len
  },
  {
    "short_chime",
    short_chime_atomic_wav,
    short_chime_atomic_wav_len
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
volatile bool  isPlaying         = false;
TaskHandle_t   playbackTaskHandle = nullptr;
uint32_t       currentSampleRate  = 0;

// --------------------------------------------------
// Gate monitor state
// --------------------------------------------------
GateState currentGateState = GATE_UNKNOWN;
GateState lastRenderedState = GATE_UNKNOWN;

uint32_t lastPollMs        = 0;  // millis of last poll
uint32_t lastChimeMs       = 0;  // millis of last short-chime trigger
uint32_t lastRejaMs        = 0;  // millis of last reja_abierta trigger
uint32_t lastBlinkMs       = 0;  // millis of last blink toggle
bool     blinkOn           = true;
bool     lastBlinkOn       = false;

// Audio mute (button silences OPEN alerts for MUTE_DURATION_MS).
bool     audioMuted        = false;
uint32_t muteUntilMs       = 0;

// --------------------------------------------------
// Audio helpers
// --------------------------------------------------
bool initAudioIfNeeded(uint32_t sampleRate) {
  if (currentSampleRate == sampleRate) return true;

  Serial.printf("Initializing EchoBase at %lu Hz\n", sampleRate);

  bool ok = echobase.init(
    sampleRate,
    PIN_I2C_SDA, PIN_I2C_SCL,
    PIN_I2S_DIN, PIN_I2S_WS, PIN_I2S_DOUT, PIN_I2S_BCK,
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
  if (!resource) return false;

  WavInfo wav;
  if (!parseWav(resource->wav, resource->wavLen, wav)) {
    Serial.println("Invalid WAV");
    return false;
  }
  if (wav.audioFormat   != 1)     { Serial.println("WAV must be PCM");    return false; }
  if (wav.bitsPerSample != 16)    { Serial.println("WAV must be 16-bit"); return false; }
  if (wav.sampleRate < 16000 || wav.sampleRate > 64000) {
    Serial.println("Unsupported sample rate"); return false;
  }

  if (!initAudioIfNeeded(wav.sampleRate)) return false;

  Serial.printf("Playing '%s': %lu Hz, %u ch, %u bits, %lu bytes\n",
    resource->name, wav.sampleRate, wav.channels,
    wav.bitsPerSample, wav.dataSize);

  echobase.setMute(false);
  echobase.play(wav.data, wav.dataSize);
  return true;
}

void playbackTask(void* param) {
  const AudioResource* resource = (const AudioResource*)param;
  if (!playResource(resource)) {
    Serial.println("Playback failed");
  }
  isPlaying         = false;
  playbackTaskHandle = nullptr;
  vTaskDelete(nullptr);
}

bool startPlayback(const AudioResource* resource) {
  if (isPlaying) return false;

  isPlaying = true;
  BaseType_t ok = xTaskCreate(
    playbackTask, "audio-playback",
    8192, (void*)resource, 1, &playbackTaskHandle
  );
  if (ok != pdPASS) {
    isPlaying         = false;
    playbackTaskHandle = nullptr;
    Serial.println("Failed to create playback task");
    return false;
  }
  return true;
}

// --------------------------------------------------
// Display helpers
// --------------------------------------------------

// Draws a small muted-speaker icon (body + horn + diagonal slash).
// Top-left corner at (x, y); total footprint ~16×16 px.
void drawMutedSpeaker(int x, int y) {
  uint16_t col = TFT_DARKGREY;
  // Speaker body
  M5.Display.fillRect(x, y + 4, 5, 8, col);
  // Horn (quadrilateral drawn as two triangles)
  M5.Display.fillTriangle(x + 5, y + 4, x + 13, y,      x + 13, y + 16, col);
  M5.Display.fillTriangle(x + 5, y + 4, x +  5, y + 12, x + 13, y + 16, col);
  // Mute slash (red diagonal)
  M5.Display.drawLine(x,     y + 15, x + 14, y,      RED);
  M5.Display.drawLine(x + 1, y + 15, x + 15, y,      RED);
}

// Draws a filled equilateral-ish warning triangle with a "!" inside.
void drawWarningIcon(int cx, int cy, int size, uint16_t color) {
  int h = (size * 3) / 4;
  M5.Display.fillTriangle(
    cx,          cy - h,          // top
    cx - size/2, cy + h/2,        // bottom-left
    cx + size/2, cy + h/2,        // bottom-right
    color
  );
  // Draw "!" in black on top
  M5.Display.setTextColor(BLACK);
  M5.Display.setTextSize(2);
  int tw = 6 * 2; // rough char width for size-2
  M5.Display.setCursor(cx - tw/2, cy - h/2 + 4);
  M5.Display.print("!");
}

void renderState(bool blink) {
  bool changed = (currentGateState != lastRenderedState);

  // For OPEN state also re-draw when blink toggled.
  if (!changed && !(currentGateState == GATE_OPEN && blink != lastBlinkOn)) {
    return;
  }

  M5.Display.fillScreen(BLACK);

  int screenW = M5.Display.width();
  int screenH = M5.Display.height();
  int cx = screenW / 2;

  switch (currentGateState) {

    case GATE_OPEN: {
      drawWarningIcon(cx, screenH / 3, 36, YELLOW);

      if (audioMuted) {
        // --- Muted: show "Reja Abierta" in orange (no red alarm colour)
        //     plus speaker-mute icon and countdown. ---
        if (blink) {
          M5.Display.setTextColor(TFT_ORANGE);
          M5.Display.setTextSize(1);
          const char* line1 = "Reja Abierta";
          int lw1 = strlen(line1) * 6;
          M5.Display.setCursor(cx - lw1/2, screenH / 2 + 2);
          M5.Display.print(line1);
        }
        // Speaker mute icon + countdown
        uint32_t now_ms  = millis();
        uint32_t rem_ms  = (muteUntilMs > now_ms) ? (muteUntilMs - now_ms) : 0;
        uint32_t rem_min = (rem_ms + 59999UL) / 60000UL;
        int iconX = cx - 20;
        int iconY = screenH * 3 / 4 - 8;
        drawMutedSpeaker(iconX, iconY);
        char buf[8];
        snprintf(buf, sizeof(buf), "%lum", rem_min);
        M5.Display.setTextColor(TFT_DARKGREY);
        M5.Display.setTextSize(1);
        M5.Display.setCursor(iconX + 18, iconY + 4);
        M5.Display.print(buf);
      } else {
        // --- Normal OPEN: blinking red "Reja Abierta" ---
        if (blink) {
          M5.Display.setTextColor(RED);
          M5.Display.setTextSize(2);
          const char* line1 = "Reja";
          const char* line2 = "Abierta";
          int lw1 = strlen(line1) * 6 * 2;
          int lw2 = strlen(line2) * 6 * 2;
          M5.Display.setCursor(cx - lw1/2, screenH * 2/3);
          M5.Display.print(line1);
          M5.Display.setCursor(cx - lw2/2, screenH * 2/3 + 18);
          M5.Display.print(line2);
        }
      }
      break;
    }

    case GATE_CLOSED: {
      // Green filled circle
      M5.Display.fillCircle(cx, screenH / 3, 18, GREEN);

      M5.Display.setTextColor(GREEN);
      M5.Display.setTextSize(2);
      const char* line1 = "Reja";
      const char* line2 = "Cerrada";
      int lw1 = strlen(line1) * 6 * 2;
      int lw2 = strlen(line2) * 6 * 2;
      M5.Display.setCursor(cx - lw1/2, screenH * 2/3);
      M5.Display.print(line1);
      M5.Display.setCursor(cx - lw2/2, screenH * 2/3 + 18);
      M5.Display.print(line2);
      break;
    }

    case GATE_ERROR:
    default: {
      // Orange warning triangle — service unreachable
      drawWarningIcon(cx, screenH / 3, 36, 0xFD20 /* orange */);

      M5.Display.setTextColor(0xFD20 /* orange */);
      M5.Display.setTextSize(1);
      const char* line1 = "Sin conexion";
      int lw1 = strlen(line1) * 6;
      M5.Display.setCursor(cx - lw1/2, screenH * 2/3);
      M5.Display.print(line1);
      break;
    }

    case GATE_UNKNOWN: {
      // Grey question mark — service reached but detector is uncertain (dusk, blind, warmup)
      drawWarningIcon(cx, screenH / 3, 36, TFT_DARKGREY);

      M5.Display.setTextColor(TFT_DARKGREY);
      M5.Display.setTextSize(1);
      const char* line1u = "Estado de";
      const char* line2u = "Reja desconocido";
      int lw1u = strlen(line1u) * 6;
      int lw2u = strlen(line2u) * 6;
      M5.Display.setCursor(cx - lw1u/2, screenH * 2/3);
      M5.Display.print(line1u);
      M5.Display.setCursor(cx - lw2u/2, screenH * 2/3 + 10);
      M5.Display.print(line2u);
      break;
    }
  }

  lastRenderedState = currentGateState;
  lastBlinkOn       = blink;
}

// --------------------------------------------------
// Gate polling
// --------------------------------------------------
GateState pollGateState() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("poll: WiFi not connected");
    return GATE_ERROR;
  }

  HTTPClient http;
  http.begin(GATE_SERVICE_URL);
  http.setTimeout(5000);

  int code = http.GET();

  if (code <= 0) {
    Serial.printf("poll: HTTP error %d (%s)\n", code,
                  http.errorToString(code).c_str());
    http.end();
    return GATE_ERROR;
  }

  if (code != 200) {
    Serial.printf("poll: unexpected HTTP status %d\n", code);
    http.end();
    return GATE_ERROR;
  }

  String body = http.getString();
  http.end();

  Serial.printf("poll: body = %s\n", body.c_str());

  // Simple substring check — no JSON library required.
  if (body.indexOf("\"open\"") >= 0) {
    return GATE_OPEN;
  }
  if (body.indexOf("\"closed\"") >= 0) {
    return GATE_CLOSED;
  }
  if (body.indexOf("\"uncertain\"") >= 0) {
    return GATE_UNKNOWN;
  }

  Serial.println("poll: could not parse gate state");
  return GATE_ERROR;
}

// Apply a newly polled state and trigger side-effects.
void applyState(GateState newState) {
  bool stateChanged = (newState != currentGateState);
  currentGateState  = newState;

  if (stateChanged) {
    Serial.printf("Gate state -> %s\n",
      newState == GATE_OPEN    ? "OPEN"    :
      newState == GATE_CLOSED  ? "CLOSED"  :
      newState == GATE_UNKNOWN ? "UNKNOWN" : "ERROR");

    // Entering OPEN: always clear any leftover mute so a fresh open event
    // is never silenced by a mute from a previous episode.
    if (newState == GATE_OPEN) {
      audioMuted  = false;
      muteUntilMs = 0;
      startPlayback(&AUDIO_RESOURCES[0]); // reja_abierta
      uint32_t now = millis();
      lastRejaMs  = now;
      lastChimeMs = now;
    }
  }
}

// --------------------------------------------------
// HTTP helpers / handlers
// --------------------------------------------------
void sendJson(int statusCode, const String& json) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(statusCode, "application/json", json);
}

void handleRoot() {
  String json = "{";
  json += "\"message\":\"GateMonitor REST Server\",";
  json += "\"endpoints\":[";
  json += "\"GET /health\",";
  json += "\"GET /status\",";
  json += "\"GET /resources\",";
  json += "\"GET /play/reja_abierta\"";
  json += "]}";
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

void handleStatus() {
  const char* stateStr =
    currentGateState == GATE_OPEN   ? "open"    :
    currentGateState == GATE_CLOSED ? "closed"  :
    currentGateState == GATE_ERROR  ? "error"   : "unknown";

  String json = "{";
  json += "\"gate\":\"";
  json += stateStr;
  json += "\",\"isPlaying\":";
  json += isPlaying ? "true" : "false";
  json += "}";
  sendJson(200, json);
}

void handleResources() {
  String json = "{\"resources\":[";
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

  if (!resource) {
    String json = "{\"error\":\"unknown_resource\",\"resource\":\"";
    json += resourceName;
    json += "\"}";
    sendJson(404, json);
    return;
  }

  if (isPlaying) {
    sendJson(429, "{\"error\":\"audio_busy\",\"message\":\"audio_is_already_playing\"}");
    return;
  }

  if (!startPlayback(resource)) {
    sendJson(500, "{\"error\":\"playback_start_failed\"}");
    return;
  }

  String json = "{\"status\":\"playing\",\"resource\":\"";
  json += resourceName;
  json += "\"}";
  sendJson(202, json);
}

void handleNotFound() {
  String uri = server.uri();
  if (uri.startsWith("/play/")) {
    handlePlayByPath();
    return;
  }
  String json = "{\"error\":\"not_found\",\"path\":\"";
  json += uri;
  json += "\"}";
  sendJson(404, json);
}

// --------------------------------------------------
// Wi-Fi setup
// --------------------------------------------------
void connectWiFi() {
  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(5, 20);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(WHITE);
  M5.Display.println("Connecting WiFi...");

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
    M5.Display.println("WiFi failed");
    Serial.println("WiFi connection failed");
    while (true) delay(1000);
  }

  IPAddress ip = WiFi.localIP();
  Serial.printf("WiFi connected. IP: %s\n", ip.toString().c_str());

  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(5, 10);
  M5.Display.println("WiFi OK");
  M5.Display.println(ip.toString());
}

void setupHttpServer() {
  server.on("/",         HTTP_GET, handleRoot);
  server.on("/health",   HTTP_GET, handleHealth);
  server.on("/status",   HTTP_GET, handleStatus);
  server.on("/resources",HTTP_GET, handleResources);
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
  Serial.println("Starting GateMonitor");

  // Validate the embedded WAV.
  WavInfo wav;
  if (!parseWav(reja_abierta_atom_wav, reja_abierta_atom_wav_len, wav)) {
    M5.Display.println("Invalid WAV");
    while (true) delay(1000);
  }
  Serial.printf("Loaded WAV: %lu Hz, %u ch, %u bits\n",
                wav.sampleRate, wav.channels, wav.bitsPerSample);

  if (!initAudioIfNeeded(wav.sampleRate)) {
    M5.Display.println("Audio init failed");
    while (true) delay(1000);
  }

  connectWiFi();
  setupHttpServer();

  // Kick off first poll immediately.
  GateState initial = pollGateState();
  applyState(initial);
  lastPollMs = millis();
  blinkOn    = true;
  lastBlinkMs = millis();
  renderState(blinkOn);

  Serial.printf("Device IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("Gate service: %s\n", GATE_SERVICE_URL);
}

void loop() {
  M5.update();
  server.handleClient();

  uint32_t now = millis();

  // ---- Poll timer ----
  if (now - lastPollMs >= POLL_INTERVAL_MS) {
    lastPollMs = now;
    GateState polled = pollGateState();
    applyState(polled);
  }

  // ---- Mute expiry ----
  if (audioMuted && (int32_t)(now - muteUntilMs) >= 0) {
    audioMuted  = false;
    muteUntilMs = 0;
    lastRenderedState = GATE_UNKNOWN;  // force display refresh
    Serial.println("Audio mute expired — sound restored");
  }

  // ---- Repeat reja_abierta every 30 s while OPEN and not muted ----
  if (currentGateState == GATE_OPEN && !isPlaying && !audioMuted) {
    if (now - lastRejaMs >= REJA_REPEAT_MS) {
      lastRejaMs = now;
      startPlayback(&AUDIO_RESOURCES[0]); // reja_abierta
    }
  }

  // ---- Repeat short chime every 5 s while OPEN and not muted ----
  if (currentGateState == GATE_OPEN && !isPlaying && !audioMuted) {
    if (now - lastChimeMs >= CHIME_REPEAT_MS) {
      lastChimeMs = now;
      startPlayback(&AUDIO_RESOURCES[1]); // short_chime
    }
  }

  // ---- Blink toggle for OPEN ----
  if (now - lastBlinkMs >= BLINK_MS) {
    lastBlinkMs = now;
    if (currentGateState == GATE_OPEN) {
      blinkOn = !blinkOn;
    } else {
      blinkOn = true; // keep it consistent for non-OPEN states
    }
    renderState(blinkOn);
  }

  // ---- Physical button: mute audio for MUTE_DURATION_MS while gate is OPEN ----
  if (M5.BtnA.wasPressed()) {
    if (currentGateState == GATE_OPEN && !audioMuted) {
      audioMuted        = true;
      muteUntilMs       = now + MUTE_DURATION_MS;
      lastRenderedState = GATE_UNKNOWN;  // force display refresh
      Serial.printf("Audio muted for %lu min\n", MUTE_DURATION_MS / 60000UL);
    } else {
      Serial.println("Button ignored (gate not open or already muted)");
    }
  }

  delay(5);
}
