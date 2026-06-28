# chimer

Arduino project that plays audio alerts (WAV files) on an **M5Stack AtomS3R** paired with the **Atomic Audio-3.5 Base** (`M5EchoBase`). Supports both a simple button-triggered player and a Wi-Fi REST server.

---

## Hardware

| Component | Notes |
|---|---|
| M5Stack AtomS3R | Main MCU (ESP32-S3) |
| Atomic Audio-3.5 Base | Speaker amp + I2S DAC via `M5EchoBase` library |

### Pin mapping (fixed)

| Signal | GPIO |
|---|---|
| I2C SDA | 38 |
| I2C SCL | 39 |
| I2S DIN | 7 |
| I2S WS | 6 |
| I2S DOUT | 5 |
| I2S BCK | 8 |

---

## Sketches

### `PlayRejaAbierta.ino`
Minimal standalone player.
- Initialises `M5EchoBase` from the embedded WAV's own sample rate.
- Plays `reja_abierta` once at boot, then on every **BtnA** press.
- No Wi-Fi, no REST server.

### `GateMonitor.ino`
Wi-Fi REST server **and** periodic gate-state poller.
- Polls `GATE_SERVICE_URL` (`http://<host>:8080/gate`) every **10 s**.
- Parses `{"gate":"open"|"closed"}` from the response; any connection error or unparseable body enters the **error** state.
- **State machine:**

| State | Display | Audio |
|---|---|---|
| OPEN | Yellow warning triangle + red "Reja Abierta" (blinking 500 ms) | `reja_abierta` played immediately on entry, then every **30 s** |
| CLOSED | Green circle + green "Reja Cerrada" | None |
| ERROR / unreachable | Orange warning triangle + "Sin conexion" | None |

- Keeps all original REST endpoints **plus** `GET /status` (reports current polled gate state).
- **BtnA** still triggers manual playback.
- Set `GATE_SERVICE_URL` at the top of the sketch to your PC's LAN IP before flashing.

#### REST endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Lists available endpoints |
| GET | `/health` | `{"status":"ok","ip":"...","isPlaying":bool}` |
| GET | `/status` | `{"gate":"open|closed|error|unknown","isPlaying":bool}` |
| GET | `/resources` | Lists embedded audio resource names |
| GET | `/play/<name>` | Starts playback; 202 on success, 429 if busy |

---

### `AudioRestServer.ino`
Full Wi-Fi REST server.
- Connects to Wi-Fi (SSID/password in `WIFI_SSID` / `WIFI_PASSWORD` at the top of the file).
- Plays audio in a **FreeRTOS task** (`audio-playback`, 8 kB stack, priority 1) so the HTTP server stays responsive during playback.
- **BtnA** also triggers playback when the server is idle.
- No queueing: a second play request while busy returns HTTP 429.

#### REST endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Lists available endpoints |
| GET | `/health` | `{"status":"ok","ip":"...","isPlaying":bool}` |
| GET | `/resources` | Lists embedded audio resource names |
| GET | `/play/<name>` | Starts playback; 202 on success, 429 if busy |

Example:
```bash
curl http://<device-ip>/play/reja_abierta
```

---

## Gate detector service (`detector/`)

Python service that polls a Dahua RTSP camera every 4 s and exposes gate state over HTTP.

```bash
cd detector
uv run python app.py --port 9090    # serves on :9090
```

**Endpoints:** `GET /gate` → `{"gate":"open"|"closed","confidence":float}`, `GET /health`, `GET /debug`, `GET /reset`

### Detection approach

Retroreflective markers are mounted on the gate bar and post. The detector uses three zones (native 2304×1296 coordinates):

| Zone | Rect (x,y,w,h) | Purpose |
|---|---|---|
| `ZONE_LATCH` | (448, 499, 70, 73) | Tight overlap of both markers when gate is **closed**; primary decision zone |
| `ZONE_CLOSED` | (387, 455, 207, 261) | Wider area around closed position |
| `ZONE_OPEN` | (160, 85, 590, 140) | Gate bar travel path when fully open |

Classification logic (latch-primary mode):
1. If `ZONE_LATCH` has a bright blob ≥ 200 px → **closed** (conf 1.0)
2. IR fallback: if night and `ZONE_LATCH` at lower threshold (120) has blob ≥ 200 px → **closed** (conf 0.7)
3. If `ZONE_OPEN` has a bright blob → **open**
4. Otherwise → **open** by absence (conf 0.45)

Guards: headlight saturation (`ZONE_OPEN` > 4000 px → uncertain), both-zones-dark (AGC washout → uncertain).

Temporal aggregator: 5-frame window, 4-of-5 majority to flip state (~16 s worst-case latency).

### Stable baseline — tag `detector-v1.0`

Validated 2026-06-27 against 28 real debug bundles captured during a full day of operation (daylight + dusk + IR night). **28/28 bundles correct**, 38/38 unit tests pass.

| Period | Result |
|---|---|
| Daylight (18:00–18:10) | 6/6 ✅ |
| Dusk / early IR (18:35–18:40) | 6/6 ✅ |
| Night IR (18:56–20:29) | 16/16 ✅ |

8 false positives from earlier code versions are suppressed. To return to this state: `git checkout detector-v1.0`.

### Running tests

```bash
cd detector && uv run pytest -q
```

---

## Gate mock service (`tools/gate_service.py`)

Zero-dependency Python server that emulates the gate-state REST service so you can test all three device states without real hardware.

```bash
python3 tools/gate_service.py           # listens on port 8080
python3 tools/gate_service.py --port 9090
```

**Endpoints:** `GET /gate` → `{"gate":"open"|"closed"}`, `GET /health`

**Switching state at runtime** (writes to `tools/state.txt`):
```bash
echo open   > tools/state.txt   # device enters OPEN state within 10 s
echo closed > tools/state.txt   # device enters CLOSED state within 10 s
```

**Quick test with curl:**
```bash
curl http://localhost:8080/gate     # {"gate":"closed"}
echo open > tools/state.txt
curl http://localhost:8080/gate     # {"gate":"open"}
```

---

## Audio resources

WAV files are compiled into flash as C arrays using `xxd`:

```bash
xxd -i my_sound.wav > my_sound_wav.h
```

The header exposes two symbols used in the sketch:
```c
const unsigned char my_sound_wav[] PROGMEM = { ... };
unsigned int my_sound_wav_len = ...;
```

Register new resources in the `AUDIO_RESOURCES[]` table in the sketch:
```cpp
AudioResource AUDIO_RESOURCES[] = {
  { "reja_abierta", reja_abierta_atom_wav, reja_abierta_atom_wav_len },
  { "my_sound",     my_sound_wav,          my_sound_wav_len          },
};
```

### WAV requirements

- Format: **PCM** (audioFormat == 1)
- Bit depth: **16-bit**
- Sample rate: **16 000 – 64 000 Hz**
- Channels: mono or stereo (both work)

### `audio/record.sh`

Captures audio from the default PulseAudio sink monitor (i.e. whatever is playing on the desktop) and saves it as a 44.1 kHz stereo 16-bit WAV, suitable for conversion with `xxd`:

```bash
bash audio/record.sh   # Ctrl+C to stop; outputs webpage_audio.wav
```

---

## Arduino IDE / PlatformIO setup

Required libraries:
- `M5Unified`
- `M5EchoBase`
- `WebServer` (bundled with ESP32 Arduino core)

Board: **M5Stack AtomS3R** (ESP32-S3, Arduino ESP32 core ≥ 2.x)

---

## Security note

Wi-Fi credentials are hard-coded in `AudioRestServer.ino`. For any deployment beyond a local test network, move them to a `secrets.h` file (excluded from version control) or use NVS/Preferences.
