/**
 * pebble(a) robot firmware — ESP32
 *
 *  1. Wi-Fi → POST /robot/auth (serial + secret) → device token
 *  2. No owner yet: get a pairing code and advertise it over Bluetooth LE
 *     (the app's "Finding your pebble" screen reads it; see docs/robot-bluetooth.md in the app repo)
 *  3. Paired: heartbeat every minute, listen on Socket.IO /realtime for
 *       "robot:command" {action: wake|forward|backward|stop}  and  "dose:due"
 *     "Time for your meds": light + beep until the button is pressed → POST /robot/doses/:id/take
 *  4. Telemetry + events to ThingsBoard over MQTT (shown on the admin log site)
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <SocketIOclient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "config.h"
#include "secrets.h"

// ---------------------------------------------------------------------------------------------
// Bluetooth (same UUIDs as the app: apps/mobile/src/lib/robot-protocol.ts)
static const char *PEBBLE_SERVICE = "b7a1e000-5e6b-4c3a-9f0e-7065626c6561";
static const char *PEBBLE_INFO = "b7a1e001-5e6b-4c3a-9f0e-7065626c6561";
static const char *PEBBLE_PAIR = "b7a1e002-5e6b-4c3a-9f0e-7065626c6561";

// ---------------------------------------------------------------------------------------------
// State
static String token;              // device JWT (1 h)
static uint32_t tokenAt = 0;
static bool paired = false;
static String ownerName;
static String pairCode;
static uint32_t pairCodeUntil = 0; // millis

struct DueDose {
  String id;
  uint32_t since = 0;   // millis when it became due
  uint32_t lastBeep = 0;
};
static DueDose due;                // the dose waiting for the button, id "" = none

static int8_t moving = 0;          // -1 backward, 0 stopped, 1 forward
static uint32_t movedAt = 0;

static WiFiClient tbNet;
static PubSubClient tb(tbNet);
static SocketIOclient io;
static bool ioUp = false;

static BLECharacteristic *pairChar = nullptr;
static bool bleOn = false;

// ---------------------------------------------------------------------------------------------
// Small helpers

static void log(const String &msg) { Serial.printf("[%lu] %s\n", millis() / 1000, msg.c_str()); }

/** ThingsBoard: one telemetry message. Silently skipped without a token or connection. */
static void tbSend(JsonDocument &doc) {
  if (!strlen(TB_TOKEN) || !tb.connected()) return;
  String out;
  serializeJson(doc, out);
  tb.publish("v1/devices/me/telemetry", out.c_str());
}

/** A short event line ("boot", "paired", "dose_taken", "cmd_wake" …) in the ThingsBoard log. */
static void tbEvent(const char *event, const String &detail = "") {
  JsonDocument d;
  d["event"] = event;
  if (detail.length()) d["detail"] = detail;
  tbSend(d);
  log(String("event: ") + event + (detail.length() ? " " + detail : ""));
}

static int batteryPercent() {
  uint32_t mv = analogReadMilliVolts(PIN_BATTERY) * 2;  // 1:2 divider
  int pct = map((long)mv, BATTERY_EMPTY_MV, BATTERY_FULL_MV, 0, 100);
  return constrain(pct, 0, 100);
}

static bool charging() { return PIN_CHARGING >= 0 && digitalRead(PIN_CHARGING) == HIGH; }

static void beep(int ms = 120, int freq = 2200) {
  tone(PIN_BUZZER, freq, ms);
  delay(ms + 30);
}

static void motor(int8_t dir) {
  moving = dir;
  movedAt = millis();
  digitalWrite(PIN_MOTOR_A1, dir > 0 ? HIGH : LOW);
  digitalWrite(PIN_MOTOR_A2, dir < 0 ? HIGH : LOW);
}

// ---------------------------------------------------------------------------------------------
// HTTP to the Pebble backend

/** Calls the backend. Returns the HTTP status (or <0 on network error); body goes to `out`. */
static int api(const char *method, const String &path, const String &body, String &out) {
  WiFiClientSecure tls;
  tls.setInsecure();  // ngrok / dev certificates. For production, pin the server's root CA instead.
  HTTPClient http;
  String url = String(API_BASE) + path;
  if (!http.begin(tls, url)) return -1;
  http.addHeader("Content-Type", "application/json");
  if (token.length()) http.addHeader("Authorization", "Bearer " + token);
  http.setTimeout(10000);
  int code = strcmp(method, "GET") == 0 ? http.GET() : http.sendRequest(method, body);
  out = code > 0 ? http.getString() : "";
  http.end();
  return code;
}

static bool login() {
  JsonDocument req;
  req["serialNumber"] = DEVICE_SERIAL;
  req["secret"] = DEVICE_SECRET;
  String body, out;
  serializeJson(req, body);
  String saved = token;
  token = "";
  int code = api("POST", "/robot/auth", body, out);
  if (code != 200) {
    token = saved;
    log("login failed: HTTP " + String(code));
    return false;
  }
  JsonDocument res;
  deserializeJson(res, out);
  token = res["accessToken"].as<String>();
  paired = res["paired"] | false;
  tokenAt = millis();
  log(String("logged in, paired=") + (paired ? "yes" : "no"));
  return true;
}

static void heartbeat() {
  JsonDocument req;
  req["batteryLevel"] = batteryPercent();
  req["status"] = charging() ? "charging" : "online";
  req["firmwareVersion"] = FW_VERSION;
  String body, out;
  serializeJson(req, body);
  int code = api("POST", "/robot/heartbeat", body, out);
  if (code == 401) login();
  else if (code == 200) {
    JsonDocument res;
    if (!deserializeJson(res, out)) paired = res["paired"] | paired;
  }
}

/** Owner name etc. Returns true once the robot has an owner. */
static bool fetchMe() {
  String out;
  int code = api("GET", "/robot/me", "", out);
  if (code == 401) { login(); return false; }
  if (code != 200) return false;
  JsonDocument res;
  if (deserializeJson(res, out)) return false;
  JsonVariant owner = res["owner"];
  if (owner.isNull()) return false;
  ownerName = owner["displayName"] | "";
  return true;
}

static void markTaken(const String &doseId) {
  JsonDocument req;
  req["via"] = "robot_button";
  String body, out;
  serializeJson(req, body);
  int code = api("POST", "/robot/doses/" + doseId + "/take", body, out);
  tbEvent("dose_taken", code == 200 ? doseId : "failed HTTP " + String(code));
}

/** Backup for the realtime "dose:due": the first pending dose whose time has come. */
static void pollDoses() {
  if (due.id.length()) return;
  String out;
  int code = api("GET", "/robot/doses/today", "", out);
  if (code == 401) { login(); return; }
  if (code != 200) return;
  JsonDocument res;
  if (deserializeJson(res, out)) return;
  time_t now = time(nullptr);
  for (JsonObject d : res["doses"].as<JsonArray>()) {
    String status = d["status"] | "";
    if (status != "pending") continue;
    struct tm t = {};
    String at = d["scheduledAt"] | "";
    if (!strptime(at.c_str(), "%Y-%m-%dT%H:%M:%S", &t)) continue;
    if (now > 100000 && mktime(&t) <= now) {  // clock set by NTP (UTC) and the time has come
      due.id = d["id"].as<String>();
      due.since = millis();
      due.lastBeep = 0;
      tbEvent("dose_due", due.id);
      return;
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Pairing over Bluetooth LE

static void startBle() {
  if (bleOn) return;
  BLEDevice::init("pebble(a)");
  BLEServer *server = BLEDevice::createServer();
  BLEService *svc = server->createService(PEBBLE_SERVICE);

  JsonDocument info;
  info["serial"] = DEVICE_SERIAL;
  info["name"] = "pebble(a)";
  info["model"] = ROBOT_MODEL;
  info["color"] = ROBOT_COLOR;
  info["fw"] = FW_VERSION;
  String infoText;
  serializeJson(info, infoText);

  BLECharacteristic *infoChar = svc->createCharacteristic(PEBBLE_INFO, BLECharacteristic::PROPERTY_READ);
  infoChar->setValue(infoText.c_str());
  pairChar = svc->createCharacteristic(PEBBLE_PAIR, BLECharacteristic::PROPERTY_READ);
  pairChar->setValue(pairCode.c_str());
  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(PEBBLE_SERVICE);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  bleOn = true;
  log("BLE: advertising, waiting for the app");
}

static void stopBle() {
  if (!bleOn) return;
  BLEDevice::stopAdvertising();
  BLEDevice::deinit(true);  // frees the BLE memory for TLS
  pairChar = nullptr;
  bleOn = false;
  log("BLE: stopped");
}

/** Keep a fresh 6-digit code in the PAIR characteristic (and on the screen, if there is one). */
static void refreshPairCode() {
  if (pairCode.length() && millis() < pairCodeUntil) return;
  String out;
  int code = api("POST", "/robot/pairing-code", "{}", out);
  if (code == 401) { login(); return; }
  if (code != 200 && code != 201) return;
  JsonDocument res;
  if (deserializeJson(res, out)) return;
  pairCode = res["code"].as<String>();
  pairCodeUntil = millis() + 4UL * 60UL * 1000UL;  // ask again before it expires
  if (pairChar) pairChar->setValue(pairCode.c_str());
  log("pairing code: " + pairCode + "  (also type-able in the app)");
}

// ---------------------------------------------------------------------------------------------
// Realtime (Socket.IO /realtime)

static void onCommand(const String &action) {
  tbEvent("cmd", action);
  if (action == "wake") {
    for (int i = 0; i < 3; i++) { digitalWrite(PIN_LED, HIGH); beep(90, 2600); digitalWrite(PIN_LED, LOW); delay(90); }
  } else if (action == "forward") {
    motor(1);
  } else if (action == "backward") {
    motor(-1);
  } else if (action == "stop") {
    motor(0);
  }
}

static void onSocket(socketIOmessageType_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case sIOtype_CONNECT: {
      // Socket.IO v4: join the /realtime namespace and log in with the device token
      // (the server reads handshake.auth.token).
      ioUp = true;
      String join = "/realtime,{\"token\":\"" + token + "\"}";
      io.send(sIOtype_CONNECT, join);
      log("realtime: connected");
      break;
    }
    case sIOtype_DISCONNECT:
      ioUp = false;
      log("realtime: disconnected");
      break;
    case sIOtype_EVENT: {
      // "/realtime,["event",{...}]": skip the namespace prefix
      const char *json = (const char *)memchr(payload, '[', length);
      if (!json) return;
      JsonDocument msg;  // ["event", {payload}]
      if (deserializeJson(msg, json, length - (json - (const char *)payload))) return;
      String event = msg[0] | "";
      if (event == "robot:command") {
        onCommand(msg[1]["action"] | "");
      } else if (event == "dose:due") {
        due.id = msg[1]["doseId"].as<String>();
        due.since = millis();
        due.lastBeep = 0;
        tbEvent("dose_due", due.id);
        if (PIN_DISPENSER >= 0) {  // hand out the pill
          digitalWrite(PIN_DISPENSER, HIGH);
          delay(400);
          digitalWrite(PIN_DISPENSER, LOW);
          String out;
          api("POST", "/robot/doses/" + due.id + "/dispensed", "{}", out);
        }
      } else if (event == "error") {
        login();  // token expired: next reconnect uses the new one
      }
      break;
    }
    default:
      break;
  }
}

static void startRealtime() {
  // API_BASE is https://host[:port]
  String host = String(API_BASE);
  host.replace("https://", "");
  host.replace("http://", "");
  int slash = host.indexOf('/');
  if (slash >= 0) host = host.substring(0, slash);
  bool secure = String(API_BASE).startsWith("https");
  if (secure) io.beginSSL(host.c_str(), 443, "/socket.io/?EIO=4");
  else io.begin(host.c_str(), 80, "/socket.io/?EIO=4");
  io.onEvent(onSocket);
  io.setReconnectInterval(5000);
}

// ---------------------------------------------------------------------------------------------
// ThingsBoard

static void tbConnect() {
  if (!strlen(TB_TOKEN) || tb.connected()) return;
  static uint32_t lastTry = 0;
  if (millis() - lastTry < 10000 && lastTry) return;
  lastTry = millis();
  tb.setServer(TB_HOST, TB_PORT);
  if (tb.connect(DEVICE_SERIAL, TB_TOKEN, nullptr)) {
    JsonDocument attrs;  // shows up under the device's client attributes
    attrs["serial"] = DEVICE_SERIAL;
    attrs["model"] = ROBOT_MODEL;
    attrs["firmware"] = FW_VERSION;
    String out;
    serializeJson(attrs, out);
    tb.publish("v1/devices/me/attributes", out.c_str());
    log("ThingsBoard: connected");
  }
}

static void tbTelemetry() {
  JsonDocument d;
  d["battery"] = batteryPercent();
  d["charging"] = charging();
  d["paired"] = paired;
  d["rssi"] = WiFi.RSSI();
  d["uptime"] = millis() / 1000;
  d["heap"] = ESP.getFreeHeap();
  d["realtime"] = ioUp;
  d["doseWaiting"] = due.id.length() > 0;
  tbSend(d);
}

// ---------------------------------------------------------------------------------------------

static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  log("Wi-Fi: connecting to " + String(WIFI_SSID));
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
  if (WiFi.status() == WL_CONNECTED) log("Wi-Fi: " + WiFi.localIP().toString());
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_MOTOR_A1, OUTPUT);
  pinMode(PIN_MOTOR_A2, OUTPUT);
  if (PIN_CHARGING >= 0) pinMode(PIN_CHARGING, INPUT);
  if (PIN_DISPENSER >= 0) pinMode(PIN_DISPENSER, OUTPUT);
  analogReadResolution(12);
  motor(0);
  log("pebble(a) firmware " FW_VERSION " · " DEVICE_SERIAL);

  connectWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC, for dose times
  while (!login()) { delay(5000); connectWifi(); }

  tbConnect();
  tbEvent("boot", FW_VERSION);

  if (!paired) {
    refreshPairCode();
    startBle();
  } else {
    fetchMe();
  }
  startRealtime();
  heartbeat();
}

void loop() {
  static uint32_t lastBeat = 0, lastTele = 0, lastDosePoll = 0, lastPairPoll = 0;
  uint32_t now = millis();

  connectWifi();
  io.loop();
  tbConnect();
  tb.loop();

  if (now - tokenAt > TOKEN_REFRESH_MS) login();

  // waiting for an owner: keep the code fresh, and check whether the app has paired us
  if (!paired && now - lastPairPoll > PAIR_POLL_MS) {
    lastPairPoll = now;
    refreshPairCode();
    if (fetchMe()) {
      paired = true;
      pairCode = "";
      stopBle();
      tbEvent("paired", ownerName);
      beep(80, 1800); beep(80, 2400); beep(120, 3000);
    }
  }

  if (now - lastBeat > HEARTBEAT_MS || !lastBeat) { lastBeat = now; heartbeat(); }
  if (now - lastTele > TELEMETRY_MS || !lastTele) { lastTele = now; tbTelemetry(); }
  if (paired && now - lastDosePoll > DOSE_POLL_MS) { lastDosePoll = now; pollDoses(); }

  // a dose is waiting: light on, beep now and then, the button confirms
  if (due.id.length()) {
    digitalWrite(PIN_LED, (now / 500) % 2);
    if (now - due.lastBeep > REMINDER_BEEP_MS || !due.lastBeep) { due.lastBeep = now; beep(); beep(); }
    if (digitalRead(PIN_BUTTON) == LOW) {
      delay(30);  // debounce
      if (digitalRead(PIN_BUTTON) == LOW) {
        markTaken(due.id);
        due.id = "";
        digitalWrite(PIN_LED, LOW);
        beep(60, 3000);
        while (digitalRead(PIN_BUTTON) == LOW) delay(10);
      }
    }
  }

  // safety: never keep driving if "stop" got lost
  if (moving && now - movedAt > MOVE_TIMEOUT_MS) motor(0);

  delay(5);
}
