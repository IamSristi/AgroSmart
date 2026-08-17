/*
 * =====================================================================
 *  AgriNova UNIFIED — ESP8266 Main Node  (v3 — correct mode logic)
 * =====================================================================
 *
 *  MODE LOGIC (the correct design):
 *  ─────────────────────────────────────────────────────────────────
 *
 *  POWER-ON:
 *    → Start WIFI_AP_STA — AP always on for ESP-NOW
 *    → Try STA connect to router
 *    → STA connected   → ONLINE  mode (Firebase active)
 *    → STA failed       → OFFLINE mode (AP-only, HTTP)
 *
 *  RUNTIME STATE MACHINE (500 ms tick):
 *
 *    ONLINE (STA up, Firebase running, AP open for ESP-NOW):
 *      → A real device joins AP (softAPgetStationNum() > 0)
 *          WiFi.mode(WIFI_AP)  — STA is TURNED OFF
 *          Firebase paused
 *          HTTP server handles everything
 *          → Now OFFLINE
 *
 *    OFFLINE (AP-only, HTTP mode):
 *      → AP client count == 0  for 3 consecutive checks (1.5 s)
 *          WiFi.mode(WIFI_AP_STA)
 *          WiFi.begin() — reconnect router
 *          Firebase resumes when STA connects
 *          → Now ONLINE
 *
 *  KEY DESIGN POINTS:
 *    - Sensor node uses ESP-NOW ONLY — never joins AP, never counted
 *    - ESP-NOW is re-inited after every WiFi.mode() change
 *    - 3-sample debounce prevents false mode flaps
 *    - STA is actually stopped in offline mode (radio fully for AP)
 *
 *  HARDWARE:
 *    D1 (GPIO5)  → HC-SR04 TRIG
 *    D2 (GPIO4)  → HC-SR04 ECHO
 *    D3 (GPIO0)  → Calibration Button (active LOW, INPUT_PULLUP)
 *    D5 (GPIO14) → Pump1 IN2  (Drain)
 *    D6 (GPIO12) → Pump1 IN1
 *    D7 (GPIO13) → Pump2 IN1  (Distribute)
 *    D8 (GPIO15) → Pump2 IN2
 *
 *  LIBRARIES:
 *    - ESP8266WiFi, ESP8266WebServer  (built-in)
 *    - ArduinoJson v6.x  (Benoit Blanchon)
 *    - Firebase_ESP_Client  (Mobizt)
 * =====================================================================
 */

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include <Firebase_ESP_Client.h>
#include <espnow.h>
#include "secrets.h"

// ── Pins ──────────────────────────────────────────────────────────────
#define TRIG_PIN   5    // D1
#define ECHO_PIN   4    // D2
#define BUTTON_PIN 0    // D3
#define PUMP1_IN1  12   // D6
#define PUMP1_IN2  14   // D5
#define PUMP2_IN1  13   // D7
#define PUMP2_IN2  15   // D8

// ── ESP-NOW packet (must match sensor_node byte-for-byte) ─────────────
struct __attribute__((packed)) SensorPacket {
  uint8_t nodeType; // 1 = Env Node, 2 = pH Node
  float   temperature;
  float   humidity;
  uint8_t moisture;
  float   phValue;
  uint8_t msgId;
};

volatile float   espTemperature = 0.0;
volatile float   espHumidity    = 0.0;
volatile float   soilPh         = 7.0; // Default neutral pH
volatile int     soilMoisture   = 0;
volatile uint8_t lastMsgId      = 255;
volatile unsigned long lastEspNowMs = 0;
#define ESPNOW_STALE_MS 10000UL

void onEspNowReceive(uint8_t *mac, uint8_t *data, uint8_t len) {
  if (len != sizeof(SensorPacket)) return;
  SensorPacket pkt;
  memcpy(&pkt, data, sizeof(pkt));
  if (pkt.msgId == lastMsgId) return;
  lastMsgId = pkt.msgId;

  if (pkt.nodeType == 1) {
    // Environmental Node (Temp/Hum/Moisture)
    if (!isnan(pkt.temperature)) espTemperature = pkt.temperature;
    if (!isnan(pkt.humidity))    espHumidity    = pkt.humidity;
    soilMoisture = pkt.moisture;
  } else if (pkt.nodeType == 2) {
    // pH Node
    if (!isnan(pkt.phValue)) soilPh = pkt.phValue;
  }

  lastEspNowMs = millis();
  Serial.printf("[ESP-NOW] Node:%d T:%.1f H:%.0f Soil:%d pH:%.1f msgId:%d\n",
    pkt.nodeType, espTemperature, espHumidity, soilMoisture, soilPh, pkt.msgId);
}

// ── Firebase ──────────────────────────────────────────────────────────
FirebaseData   fbdo;
FirebaseData   fbdoPump;
FirebaseAuth   auth;
FirebaseConfig fbConfig;
bool           firebaseBegun = false;

// ── HTTP server ───────────────────────────────────────────────────────
ESP8266WebServer server(80);

// ── Sensor state ──────────────────────────────────────────────────────
float       tankEmptyCm   = 30.0;
const float tankMinCm     = 2.0;
long        distanceCm    = 0;
int         waterLevelPct = 0;
bool        pump1State    = false;
bool        pump2State    = false;
bool        lastButtonState = HIGH;

// ── Mode state ────────────────────────────────────────────────────────
bool    offlineMode    = false;
uint8_t noClientCount  = 0;
#define NO_CLIENT_THRESH 3   // 3 × 500 ms = 1.5 s debounce

// ── Timing ────────────────────────────────────────────────────────────
unsigned long lastSensorMs = 0;
unsigned long lastPumpMs   = 0;
unsigned long lastModeMs   = 0;

// =====================================================================
//  HELPERS
// =====================================================================

long measureDistanceCM() {
  for (int i = 0; i < 3; i++) {
    digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
    digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);
    long dur = pulseIn(ECHO_PIN, HIGH, 30000);
    if (dur > 0) return (long)((dur * 0.0343f) / 2.0f);
    delay(10);
  }
  return -1;
}

int calcWaterPct(long dist) {
  if (dist < 0)            return waterLevelPct;
  if (dist >= tankEmptyCm) return 0;
  if (dist <= tankMinCm)   return 100;
  float pct = 100.0f - (((dist - tankMinCm) / (tankEmptyCm - tankMinCm)) * 100.0f);
  return (int)constrain(pct, 0, 100);
}

void setPump1(bool on) {
  pump1State = on;
  digitalWrite(PUMP1_IN1, on ? HIGH : LOW);
  digitalWrite(PUMP1_IN2, LOW);
  Serial.printf("[PUMP1] %s\n", on ? "ON" : "OFF");
}

void setPump2(bool on) {
  pump2State = on;
  digitalWrite(PUMP2_IN1, on ? HIGH : LOW);
  digitalWrite(PUMP2_IN2, LOW);
  Serial.printf("[PUMP2] %s\n", on ? "ON" : "OFF");
}

void addCORS() {
  server.sendHeader("Access-Control-Allow-Origin",  "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

// ── ESP-NOW re-init (needed after every WiFi.mode change) ─────────────
void espNowInit() {
  esp_now_deinit();
  delay(10);
  if (esp_now_init() == 0) {
    esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
    esp_now_register_recv_cb(onEspNowReceive);
    Serial.println("[ESP-NOW] Ready");
  } else {
    Serial.println("[ESP-NOW] Init FAILED");
  }
}

// =====================================================================
//  FIREBASE
// =====================================================================

void initFirebase() {
  if (firebaseBegun) return;
  fbConfig.api_key      = API_KEY;
  fbConfig.database_url = DATABASE_URL;
  auth.user.email       = USER_EMAIL;
  auth.user.password    = USER_PASSWORD;
  Firebase.reconnectNetwork(true);
  fbdo.setBSSLBufferSize(4096, 1024);     fbdo.setResponseSize(2048);
  fbdoPump.setBSSLBufferSize(4096, 1024); fbdoPump.setResponseSize(1024);
  Firebase.begin(&fbConfig, &auth);
  Firebase.setDoubleDigits(2);
  fbConfig.timeout.serverResponse = 10000;
  firebaseBegun = true;
  Serial.println("[Firebase] Initialized");
}

void pushSensorsToFirebase() {
  if (!Firebase.ready()) return;
  FirebaseJson j;
  j.set("waterLevelDistance", (int)distanceCm);
  j.set("waterLevelPercent",  waterLevelPct);
  j.set("moisture",           soilMoisture);
  j.set("ph",                 soilPh); // Added pH
  bool nodeOk = (millis() - lastEspNowMs) < ESPNOW_STALE_MS;
  if (nodeOk) {
    j.set("temperature", espTemperature);
    j.set("humidity",    espHumidity);
  }
  j.set("sensorNodeOk", nodeOk);
  Firebase.RTDB.updateNode(&fbdo, "/sensors", &j);
}

void checkPumpsFromFirebase() {
  if (!Firebase.ready()) return;
  if (Firebase.RTDB.getInt(&fbdoPump, "/controls/pump1")) {
    int want = fbdoPump.intData();
    if (want == 1 && waterLevelPct > 80) {
      setPump1(false);
      Firebase.RTDB.setInt(&fbdoPump, "/controls/pump1", 0);
    } else {
      setPump1(want == 1);
    }
  } else { setPump1(false); }

  if (Firebase.RTDB.getInt(&fbdoPump, "/controls/pump2")) {
    int want = fbdoPump.intData();
    if (want == 1 && soilMoisture > 70) {
      setPump2(false);
      Firebase.RTDB.setBool(&fbdoPump, "/controls/pump2", false);
    } else {
      setPump2(want == 1);
    }
  } else { setPump2(false); }
}

// =====================================================================
//  MODE SWITCHING
// =====================================================================

void goOffline() {
  Serial.println("\n[MODE] Client joined AP → OFFLINE (AP-only, STA OFF)");
  offlineMode   = true;
  noClientCount = 0;

  // Stop STA — dedicate radio to AP + ESP-NOW
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASSWORD);
  Serial.printf("[MODE] AP IP: %s\n", WiFi.softAPIP().toString().c_str());

  espNowInit();   // re-init after mode change
  Serial.println("[MODE] Firebase PAUSED. HTTP serving offline dashboard.\n");
}

void goOnline() {
  Serial.println("\n[MODE] Client left AP → ONLINE (STA+AP, reconnecting)");
  offlineMode   = false;
  noClientCount = 0;

  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(AP_SSID, AP_PASSWORD);
  WiFi.begin(STA_SSID, STA_PASSWORD);

  espNowInit();   // re-init after mode change
  Serial.println("[MODE] STA reconnecting… Firebase resumes when STA is up.\n");
}

// =====================================================================
//  HTTP ROUTES
// =====================================================================

void handleGetSensors() {
  addCORS();
  bool nodeOk = (millis() - lastEspNowMs) < ESPNOW_STALE_MS;
  StaticJsonDocument<300> doc;
  doc["waterLevelPct"]   = waterLevelPct;
  doc["waterDistanceCm"] = distanceCm;
  doc["tankEmptyHeight"] = tankEmptyCm;
  doc["moisture"]        = soilMoisture;
  doc["temperature"]     = espTemperature;
  doc["humidity"]        = espHumidity;
  doc["ph"]              = soilPh; // Added pH
  doc["pump1"]           = pump1State;
  doc["pump2"]           = pump2State;
  doc["mode"]            = offlineMode ? "offline" : "online";
  doc["sensorNodeOk"]    = nodeOk;
  String json; serializeJson(doc, json);
  server.send(200, "application/json", json);
}

void handleGetMode() {
  addCORS();
  // Flask polls this — offlineMode is the ground truth (actual WiFi state)
  bool staUp = (WiFi.status() == WL_CONNECTED);
  String json = "{\"mode\":\"" + String(offlineMode ? "offline" : "online") +
                "\",\"clients\":" + String(WiFi.softAPgetStationNum()) +
                ",\"staUp\":"    + String(staUp ? "true" : "false") + "}";
  server.send(200, "application/json", json);
}

void handlePump1() {
  addCORS();
  if (server.method() == HTTP_OPTIONS) { server.send(204); return; }
  StaticJsonDocument<64> req;
  if (deserializeJson(req, server.arg("plain")) == DeserializationError::Ok) {
    bool want = req["state"].as<bool>();
    if (want && waterLevelPct > 80) {
      server.send(200, "application/json",
        "{\"ok\":false,\"reason\":\"Water >80%, drain blocked\"}");
      return;
    }
    setPump1(want);
  }
  server.send(200, "application/json",
    "{\"ok\":true,\"pump1\":" + String(pump1State ? "true" : "false") + "}");
}

void handlePump2() {
  addCORS();
  if (server.method() == HTTP_OPTIONS) { server.send(204); return; }
  StaticJsonDocument<64> req;
  if (deserializeJson(req, server.arg("plain")) == DeserializationError::Ok) {
    bool want = req["state"].as<bool>();
    if (want && soilMoisture > 70) {
      server.send(200, "application/json",
        "{\"ok\":false,\"reason\":\"Soil >70%, irrigation blocked\"}");
      return;
    }
    setPump2(want);
  }
  server.send(200, "application/json",
    "{\"ok\":true,\"pump2\":" + String(pump2State ? "true" : "false") + "}");
}

void handleCalibrate() {
  addCORS();
  if (server.method() == HTTP_OPTIONS) { server.send(204); return; }
  long dist = measureDistanceCM();
  if (dist > 0) {
    tankEmptyCm = (float)dist;
    Serial.printf("[CAL] New empty height = %.1f cm\n", tankEmptyCm);
    if (!offlineMode && Firebase.ready())
      Firebase.RTDB.setFloat(&fbdo, "/sensors/tankEmptyHeight", tankEmptyCm);
  }
  server.send(200, "application/json",
    "{\"ok\":" + String(dist > 0 ? "true" : "false") +
    ",\"tankEmptyHeight\":" + String(tankEmptyCm) + "}");
}

void handleSoilUpdate() {
  addCORS();
  if (server.method() == HTTP_OPTIONS) { server.send(204); return; }
  StaticJsonDocument<64> req;
  if (deserializeJson(req, server.arg("plain")) == DeserializationError::Ok) {
    int m = req["moisture"];
    if (m >= 0 && m <= 100) soilMoisture = m;
  }
  server.send(200, "application/json",
    "{\"ok\":true,\"moisture\":" + String(soilMoisture) + "}");
}

void handleOptions() { addCORS(); server.send(204); }

void checkButtonPress() {
  bool btn = digitalRead(BUTTON_PIN);
  if (lastButtonState == HIGH && btn == LOW) {
    long d = measureDistanceCM();
    if (d > 0) {
      tankEmptyCm = (float)d;
      Serial.printf("[BTN CAL] Empty height = %.1f cm\n", tankEmptyCm);
      if (!offlineMode && Firebase.ready())
        Firebase.RTDB.setFloat(&fbdo, "/sensors/tankEmptyHeight", tankEmptyCm);
    }
  }
  lastButtonState = btn;
}

// =====================================================================
//  SETUP
// =====================================================================

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== AgriNova UNIFIED v3 ===");

  pinMode(TRIG_PIN,   OUTPUT);
  pinMode(ECHO_PIN,   INPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(PUMP1_IN1,  OUTPUT); pinMode(PUMP1_IN2, OUTPUT);
  pinMode(PUMP2_IN1,  OUTPUT); pinMode(PUMP2_IN2, OUTPUT);
  setPump1(false); setPump2(false);

  WiFi.persistent(false);
  WiFi.setAutoReconnect(false);
  WiFi.mode(WIFI_AP_STA);

  WiFi.softAP(AP_SSID, AP_PASSWORD);
  Serial.printf("[AP] SSID: %s  IP: %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
  Serial.printf("[AP] Soft-AP MAC: %s  ← paste into sensor_node RECEIVER_MAC\n",
    WiFi.softAPmacAddress().c_str());

  WiFi.begin(STA_SSID, STA_PASSWORD);
  Serial.print("[STA] Connecting");
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 10000) {
    Serial.print(".");
    delay(300);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[STA] Connected  IP: %s\n", WiFi.localIP().toString().c_str());
    offlineMode = false;
    initFirebase();
    if (Firebase.RTDB.getFloat(&fbdo, "/sensors/tankEmptyHeight"))
      tankEmptyCm = fbdo.floatData();
    Serial.printf("[Firebase] tankEmptyCm restored: %.1f cm\n", tankEmptyCm);
  } else {
    Serial.println("\n[STA] Failed — OFFLINE mode (AP-only)");
    offlineMode = true;
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASSWORD);
  }

  espNowInit();

  server.on("/sensors",   HTTP_GET,     handleGetSensors);
  server.on("/mode",      HTTP_GET,     handleGetMode);
  server.on("/pump1",     HTTP_POST,    handlePump1);
  server.on("/pump2",     HTTP_POST,    handlePump2);
  server.on("/calibrate", HTTP_POST,    handleCalibrate);
  server.on("/soil",      HTTP_POST,    handleSoilUpdate);
  server.on("/pump1",     HTTP_OPTIONS, handleOptions);
  server.on("/pump2",     HTTP_OPTIONS, handleOptions);
  server.on("/calibrate", HTTP_OPTIONS, handleOptions);
  server.on("/soil",      HTTP_OPTIONS, handleOptions);
  server.on("/mode",      HTTP_OPTIONS, handleOptions);
  server.onNotFound([]() {
    addCORS();
    server.send(404, "application/json", "{\"error\":\"not found\"}");
  });
  server.begin();

  Serial.printf("[HTTP] Started. Mode: %s\n\n", offlineMode ? "OFFLINE" : "ONLINE");
}

// =====================================================================
//  LOOP
// =====================================================================

void loop() {
  server.handleClient();
  checkButtonPress();

  // ── Mode state machine — 500 ms tick ─────────────────────────────────
  if (millis() - lastModeMs >= 500) {
    lastModeMs = millis();
    uint8_t clients = WiFi.softAPgetStationNum();
    bool    staUp   = (WiFi.status() == WL_CONNECTED);

    if (!offlineMode) {
      // ── ONLINE: watch for a client joining the AP ────────────────────
      if (clients > 0) {
        goOffline();    // immediately drop STA, serve HTTP
      } else if (!staUp) {
        // STA dropped, no client — quietly try reconnect
        WiFi.begin(STA_SSID, STA_PASSWORD);
      }

    } else {
      // ── OFFLINE: watch for client leaving ────────────────────────────
      if (clients == 0) {
        noClientCount++;
        Serial.printf("[MODE] No client — check %d/%d\n", noClientCount, NO_CLIENT_THRESH);
        if (noClientCount >= NO_CLIENT_THRESH) {
          goOnline();   // restore STA+AP, resume Firebase
        }
      } else {
        noClientCount = 0;   // client still here — reset debounce
      }
    }

    // If we're online but Firebase hasn't started yet (edge case)
    if (!offlineMode && staUp && !firebaseBegun) {
      initFirebase();
    }
  }

  // ── Sensor reading — 500 ms ───────────────────────────────────────────
  if (millis() - lastSensorMs >= 500) {
    lastSensorMs = millis();
    long d = measureDistanceCM();
    if (d > 0) distanceCm = d;
    waterLevelPct = calcWaterPct(distanceCm);

    if (pump1State && waterLevelPct > 80) {
      Serial.println("[SAFETY] Water >80% → Pump1 OFF");
      setPump1(false);
      if (!offlineMode && Firebase.ready())
        Firebase.RTDB.setInt(&fbdoPump, "/controls/pump1", 0);
    }
    if (pump2State && soilMoisture > 70) {
      Serial.printf("[SAFETY] Soil %d%% >70%% → Pump2 OFF\n", soilMoisture);
      setPump2(false);
      if (!offlineMode && Firebase.ready())
        Firebase.RTDB.setBool(&fbdoPump, "/controls/pump2", false);
    }

    Serial.printf("[%s] Dist:%ldcm Water:%d%% Soil:%d%% T:%.1f H:%.0f Node:%s P1:%s P2:%s\n",
      offlineMode ? "OFFLINE" : "ONLINE",
      distanceCm, waterLevelPct, soilMoisture,
      espTemperature, espHumidity,
      (millis() - lastEspNowMs) < ESPNOW_STALE_MS ? "OK" : "LOST",
      pump1State ? "ON" : "OFF", pump2State ? "ON" : "OFF");
  }

  // ── Firebase push + pump poll — ONLINE only, 500 ms ──────────────────
  if (!offlineMode && millis() - lastPumpMs >= 500) {
    lastPumpMs = millis();
    if (WiFi.status() == WL_CONNECTED) {
      pushSensorsToFirebase();
      checkPumpsFromFirebase();
    }
  }

  delay(10);
}
