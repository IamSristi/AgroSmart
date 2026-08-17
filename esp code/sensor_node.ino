/*
 * AgriNova — Sensor Node Firmware (ESP-NOW Sender) — FINAL FIX
 * ==============================================================
 * ROOT CAUSE OF PREVIOUS BUG:
 *   WiFi.scanNetworks() was called to find the main ESP's channel.
 *   During a scan, the ESP8266 radio sends probe request frames on every
 *   channel. On some firmware versions this briefly associates the STA
 *   interface with any AP it finds — including the main ESP's "AgriNova"
 *   AP. The main ESP then sees softAPgetStationNum() > 0 and switches to
 *   offline mode, killing Firebase and pump control.
 *
 * THE FIX:
 *   No WiFi scan. No WiFi.begin(). No WiFi.disconnect().
 *   We set the radio channel directly with wifi_set_channel() — raw SDK
 *   call that changes the channel with zero association or probe frames.
 *   The sensor node never appears as an AP client anywhere.
 *
 * HOW TO FIND YOUR ROUTER'S CHANNEL:
 *   Windows:  netsh wlan show networks mode=bssid  → look for your router's SSID
 *   Android:  WiFi Analyzer app
 *   Linux:    iwlist wlan0 scan | grep -A5 your router's SSID
 *   Router:   admin page → Wireless settings → Channel
 *   Set ESPNOW_CHANNEL below to match, then flash.
 *
 * WIRING:
 *   DHT11 DATA → D4 (GPIO2)  [10kΩ pull-up to 3.3V]
 *   Soil AO    → A0
 *
 * LIBRARIES: DHT sensor library (Adafruit) + Adafruit Unified Sensor
 */

#include <ESP8266WiFi.h>
#include <espnow.h>
#include <DHT.h>

// ── SET THIS to your router's WiFi channel ─────────────────────────────────
// Must match the channel your router is on. The main ESP's radio locks to
// this channel when its STA connects to your router.
#define ESPNOW_CHANNEL  6    // ← change to your router's actual channel

// ── Main ESP's SoftAP MAC ──────────────────────────────────────────────────
uint8_t RECEIVER_MAC[] = {0xCE, 0x50, 0xE3, 0x3D, 0x64, 0xB5};

// ── DHT11 ──────────────────────────────────────────────────────────────────
#define DHT_PIN  2
#define DHT_TYPE DHT11
DHT dht(DHT_PIN, DHT_TYPE);

// ── Soil moisture ──────────────────────────────────────────────────────────
#define SOIL_PIN  A0
#define SOIL_DRY  850
#define SOIL_WET  350

// ── Packet (must match main ESP byte-for-byte) ─────────────────────────────
struct __attribute__((packed)) SensorPacket {
  uint8_t nodeType; // 1 = Env Node, 2 = pH Node
  float   temperature;
  float   humidity;
  uint8_t moisture;
  float   phValue;
  uint8_t msgId;
};

SensorPacket packet;
uint8_t msgCounter = 0;
unsigned long lastSendMs = 0;
const unsigned long SEND_INTERVAL = 2000;

void onSent(uint8_t *mac, uint8_t status) {
  Serial.printf("[ESP-NOW] %s  ch:%d  msgId:%d\n",
    status == 0 ? "OK" : "FAIL", ESPNOW_CHANNEL, packet.msgId);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== AgriNova Sensor Node ===");

  dht.begin();

  // NO scan, NO connect, NO disconnect — STA mode only, never associated
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoConnect(false);
  WiFi.setAutoReconnect(false);

  Serial.printf("STA MAC: %s\n", WiFi.macAddress().c_str());

  // Set channel directly via SDK — no probe frames, no association
  wifi_set_channel(ESPNOW_CHANNEL);
  Serial.printf("Channel set to %d (raw SDK, no WiFi scan)\n", ESPNOW_CHANNEL);

  if (esp_now_init() != 0) {
    Serial.println("ESP-NOW init FAILED — restarting");
    delay(3000);
    ESP.restart();
  }
  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_register_send_cb(onSent);

  int r = esp_now_add_peer(RECEIVER_MAC, ESP_NOW_ROLE_SLAVE, ESPNOW_CHANNEL, NULL, 0);
  Serial.printf("Peer %s  MAC:%02X:%02X:%02X:%02X:%02X:%02X  ch:%d\n",
    r == 0 ? "OK" : "FAILED",
    RECEIVER_MAC[0], RECEIVER_MAC[1], RECEIVER_MAC[2],
    RECEIVER_MAC[3], RECEIVER_MAC[4], RECEIVER_MAC[5], ESPNOW_CHANNEL);

  Serial.println("Ready — sending every 2s, zero WiFi association\n");
}

void loop() {
  if (millis() - lastSendMs >= SEND_INTERVAL || lastSendMs == 0) {
    lastSendMs = millis();

    packet.nodeType = 1; // Environmental Node

    float t = dht.readTemperature();
    float h = dht.readHumidity();
    packet.temperature = isnan(t) ? 0.0f : t;
    packet.humidity    = isnan(h) ? 0.0f : h;

    int raw = analogRead(SOIL_PIN);
    packet.moisture = (uint8_t)constrain(map(raw, SOIL_DRY, SOIL_WET, 0, 100), 0, 100);
    packet.phValue  = 0.0f; // Not measured by this node
    packet.msgId    = ++msgCounter;

    Serial.printf("[READ] Temp:%.1f  Hum:%.0f  Soil:%d  ADC:%d\n",
      packet.temperature, packet.humidity, packet.moisture, raw);

    esp_now_send(RECEIVER_MAC, (uint8_t*)&packet, sizeof(packet));
  }
}
