# AgriNova — Bug Fixes & Solutions

---

## Bug 1 — Firebase Data Latency (Online Mode)

**Problem:**
Every sensor poll on the dashboard had noticeable delay. Data was slow to update even though the ESP was pushing to Firebase fine.

**Root Cause:**
`detect_mode()` was called on every `/api/sensors` request. That function made a live HTTP GET to `192.168.4.1` with a 5-second timeout — so every 1-second dashboard poll was blocked waiting on an ESP roundtrip before even touching Firebase. On top of that, two separate Firebase requests were made per poll (one for sensors, one for controls), each with an 8-second timeout.

**Fix:**
- `detect_mode()` now reads a local cache instead of calling the ESP on every request.
- Cache is only refreshed by the `/api/mode` background thread (every 2s).
- Firebase timeouts reduced from 8s → 4s.
- Two Firebase reads merged into one root fetch per poll.

---

## Bug 2 — Pump Buttons Not Responding / Burst Firing

**Problem:**
Pressing the pump button did nothing for several clicks, then all queued commands fired at once.

**Root Cause:**
Each pump request also called `detect_mode()` (another ESP roundtrip, up to 5s). Multiple clicks stacked up waiting in Flask's thread pool behind that slow call. When the network cleared, all queued requests fired together.

**Fix:**
- Server side: a `threading.Lock()` per pump. If a command is already in-flight, new requests return `{"busy": true}` instantly instead of queuing.
- Client side: a `_pumpBusy` flag per pump in JS. If busy, the toggle snaps back visually and shows a toast — no duplicate request is sent.

---

## Bug 3 — AP → Online Mode Flapping (ESP Firmware)

**Problem:**
If the laptop's WiFi hiccuped briefly while connected to the ESP's AP, the ESP immediately switched back to online mode (resumed Firebase), then flipped back to offline when the laptop reconnected. Caused unstable mode switching.

**Root Cause:**
The ESP checked `softAPgetStationNum()` every 500ms with no debounce. A single 500ms window of zero clients triggered an immediate mode switch.

**Fix:**
Added a 15-second grace timer (`apEmptySince`). The AP must have zero clients continuously for 15 seconds before switching back to online. Reconnecting to offline is still immediate.

---

## Bug 4 — Second ESP (Sensor Node) Triggering Offline Mode

**Problem:**
Whenever the underground sensor node (second ESP) was powered on, the main ESP switched to offline mode and stopped Firebase and pump control.

**Root Cause 1 — WiFi scan caused AP association:**
`WiFi.scanNetworks()` was used to find the correct channel. During a scan, the ESP8266 radio sends probe frames and can briefly associate with any AP it finds — including the main ESP's `AgriNova` AP. The main ESP counted it as a client and went offline.

**Root Cause 2 — Hardcoded channel 1:**
`esp_now_add_peer(..., 1, ...)` locked ESP-NOW to channel 1. But when the main ESP's STA connects to the router, the radio locks to the router's channel (could be 6, 11, etc.). Packets sent on channel 1 never arrived.

**Fix:**
- Removed `WiFi.scanNetworks()` entirely.
- Used `wifi_set_channel()` — a raw Espressif SDK call that changes the radio channel with zero probe frames or association. Sensor node is completely invisible to the main ESP's AP client counter.
- Channel is now set via `#define ESPNOW_CHANNEL` — user sets it once to match their router's channel.

---

## Bug 5 — Online Mode Not Recovering After Offline (ESP Firmware)

**Problem:**
After switching from offline back to online, the ESP could not reconnect to Firebase even though WiFi reconnected fine.

**Root Cause:**
`goOnline()` called `espNowInit()` immediately after `WiFi.begin()`. On ESP8266, `esp_now_init()` resets internal WiFi task state and kills the ongoing STA connection attempt. So STA never connected, and `Firebase.ready()` returned false indefinitely.

**Fix:**
- Removed `espNowInit()` from `goOnline()`.
- Added `firebaseNeedsReinit` flag — set true when going online after offline.
- In `loop()`, `espNowInit()` and `initFirebase()` are called only **after** `WiFi.status() == WL_CONNECTED` is confirmed.
- `initFirebase()` calls `Firebase.begin()` again on reinit (not just once at boot) to refresh the stale SSL/token state.

---

## Bug 6 — Flask Stuck in Offline Mode After Laptop Rejoins Router

**Problem:**
After the laptop disconnected from the ESP's AP and reconnected to the router, the Flask dashboard stayed in offline mode permanently and could not reach Firebase.

**Root Cause:**
The "sticky mode" rule (never flip mode on ESP poll failure) was too aggressive. When the laptop left the AP, `192.168.4.1` became unreachable. The mode poll thread kept failing but never changed the cached mode from `"offline"`. Flask kept trying to proxy requests to the ESP (which was unreachable), returning 503 errors forever — even though Firebase was fully accessible.

**Fix:**
Added a timed fallback in the mode poll thread:
- If ESP is unreachable **and** current mode is `"offline"` → start an 8-second countdown.
- If still unreachable after 8 seconds → switch cached mode to `"online"` and resume Firebase reads.
- If ESP is unreachable **and** current mode is `"online"` → do nothing (Firebase works independently).

The asymmetry is intentional: being offline and unable to reach the ESP can only mean one thing — the laptop left the AP.

---

## Final Architecture

```
[Sensor Node ESP]  →  ESP-NOW (no AP join)  →  [Main ESP]
 DHT22 + Soil                                    Ultrasonic + Pumps
 underground                                          ↓ STA
                                                   Firebase
                                                      ↕
                                                  [Flask App]
                                                   Dashboard

Offline mode:  Laptop joins AgriNova AP
               Main ESP drops STA
               Flask proxies to 192.168.4.1

Online mode:   Laptop on router
               Main ESP on STA + Firebase
               Flask reads Firebase directly
```
