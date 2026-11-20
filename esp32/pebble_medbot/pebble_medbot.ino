/*
  pebble(a) MedBot - ESP32-S3-WROOM-1 (N16R8)
  เดิม: เมนูทดสอบผ่าน Serial ของทีม (jjamaycode/jetbordin #1)
  เพิ่ม: ลิงก์กับ Raspberry Pi 5 เป็น JSON ทีละบรรทัด (ดู ../PROTOCOL.md)
         บรรทัดที่ขึ้นต้นด้วย { = คำสั่งจาก Pi, อย่างอื่น = เมนูทดสอบแบบเดิม (ใช้ได้เหมือนเดิมทุกอย่าง)

  ตั้งค่า Arduino IDE (เมนู Tools):
    Board           : ESP32S3 Dev Module
    USB CDC On Boot : Enabled   <-- จำเป็น เพราะ Serial ใช้ USB ตรง (GPIO19/20) = สายที่ต่อกับ Pi
    USB Mode        : Hardware CDC and JTAG
    PSRAM           : OPI PSRAM (N16R8)
    Flash Size      : 16MB
    Serial Monitor  : 115200 baud, Line ending = "Newline"
    (ต้องใช้ Arduino IDE 2.x เพื่อให้ภาษาไทยแสดงถูกต้อง)

  ไลบรารี (Library Manager):
    Adafruit PWM Servo Driver Library (PCA9685)
    Adafruit NeoPixel (WS2812B)
    Adafruit VL53L0X (เซ็นเซอร์วัดระยะ ใช้เมื่อ USE_TOF_SENSOR = 1)

  แผนผังขา (ตาม schematic):
    GPIO4  TOUCH_SENSOR1 TTP223 -> เปิด/ปิดกล่องรับยา (ตอนมียารอ: ปิดกล่อง = กินยาแล้ว)
    GPIO5  TOUCH_SENSOR2 TTP223 -> เปลี่ยนหน้าจอ LED (ตอนต่อกับ Pi: คุยกับ Pebbi)
    GPIO8  I2C_SDA  GPIO9 I2C_SCL (PCA9685 ที่ 0x40, เซ็นเซอร์ระยะที่ 0x29)
    GPIO10..13 XSHUT_1..4 (เซ็นเซอร์ระยะ ใช้เฉพาะ XSHUT_1)
    GPIO38 RGB_FRONT  GPIO39 RGB_BACK (WS2812B ผ่าน 74AHCT125)
           RGB_FRONT ขับแผง 16x16 เล่น GIF Mochi (mochi_frames.h ต้องอยู่โฟลเดอร์เดียวกัน)
    GPIO40 ESP_BUZZER  GPIO41 ESP_FAN (สวิตช์ AO3400A ฝั่งลบ, HIGH = เปิด)
    GPIO1  BATT_SENSE
    USB (GPIO19/20) ต่อ Raspberry Pi (Pi เห็นเป็น /dev/ttyACM0)
    GPIO43/44 (TXD0/RXD0) เว้นไว้ (ถ้าจะต่อ Pi ทาง UART แทน USB)

  ช่องของ PCA9685:
    0 WHEEL_FL  1 WHEEL_FR  2 WHEEL_RL  3 WHEEL_RR
    4 MED_1 (กล่องรับยาเลื่อนออก, MG996R 360)
    5 MED_2 (ที่ดันยา, MG90S 180)
    6 ARM   (แขนหุ่นยนต์, MG90S 180)
    7 SPARE
*/

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <Adafruit_NeoPixel.h>

// เฟรม GIF Mochi 16x16 (RGB565) ต้องอยู่ข้างไฟล์ .ino นี้
// ถ้ายังไม่มีไฟล์ จะใช้หน้าตาสำรอง (ตากะพริบ) แทน เพื่อให้ compile ผ่าน
#if __has_include("mochi_frames.h")
#include "mochi_frames.h"
#define HAVE_MOCHI 1
#else
#define HAVE_MOCHI 0
#define MOCHI_SIZE 16
struct MochiAnim { const char* name; uint16_t start; uint16_t count; };
static uint16_t MOCHI_PIXELS[3][MOCHI_SIZE * MOCHI_SIZE];
static const uint16_t MOCHI_DELAY[3] = {2600, 140, 900};
static const MochiAnim MOCHI_ANIMS[] = {{"content", 0, 2}, {"happy", 2, 1}};
static const int MOCHI_ANIM_COUNT = 2;
static void buildFallbackFace() {  // ตาสีฟ้า 2 ข้าง: เปิด / หลับ / ยิ้ม (^ ^)
  const uint16_t eye = 0x5D9F;
  for (int f = 0; f < 3; f++)
    for (int i = 0; i < MOCHI_SIZE * MOCHI_SIZE; i++) MOCHI_PIXELS[f][i] = 0;
  for (int side = 0; side < 2; side++) {
    int x0 = side ? 9 : 4;
    for (int y = 5; y <= 10; y++)
      for (int x = x0; x < x0 + 3; x++) MOCHI_PIXELS[0][y * 16 + x] = eye;
    for (int x = x0; x < x0 + 3; x++) MOCHI_PIXELS[1][9 * 16 + x] = eye;
    MOCHI_PIXELS[2][8 * 16 + x0] = eye; MOCHI_PIXELS[2][7 * 16 + x0 + 1] = eye; MOCHI_PIXELS[2][8 * 16 + x0 + 2] = eye;
  }
}
#endif

#define USE_TOF_SENSOR 1  // 1 = มีเซ็นเซอร์ VL53L0X ทาง I2C, 0 = ไม่มี (ระยะ = ไกลเสมอ)
#if USE_TOF_SENSOR
#include <Adafruit_VL53L0X.h>
#endif

#define PEBBLE_FW "medbot-2.0.0"

// ============================ ขา ============================
const int PIN_TOUCH_BOX = 4;
const int PIN_TOUCH_SCREEN = 5;
const int PIN_I2C_SDA = 8;
const int PIN_I2C_SCL = 9;
const int PIN_XSHUT[4] = {10, 11, 12, 13};
const int PIN_RGB_FRONT = 38;
const int PIN_RGB_BACK = 39;
const int PIN_BUZZER = 40;
const int PIN_FAN = 41;
const int PIN_BATT_SENSE = 1;

const int TOUCH_ACTIVE = HIGH;  // TTP223 ค่าปกติ: แตะแล้วเป็น HIGH

// แบตเตอรี่: ปรับ 3 ค่านี้ให้ตรงกับแบตและตัวแบ่งแรงดันจริง (ดูค่าดิบได้ที่เมนู 9)
const float BATT_DIVIDER = 2.0;     // แรงดันแบต = ค่าที่ขา x BATT_DIVIDER
const int BATT_EMPTY_MV = 3300;     // แบตหมด
const int BATT_FULL_MV = 4150;      // แบตเต็ม

// ========================= PCA9685 / เซอร์โว =====================
const uint8_t CH_WHEEL_FL = 0;
const uint8_t CH_WHEEL_FR = 1;
const uint8_t CH_WHEEL_RL = 2;
const uint8_t CH_WHEEL_RR = 3;
const uint8_t CH_BOX = 4;
const uint8_t CH_PUSHER = 5;
const uint8_t CH_ARM = 6;
const uint8_t CH_SPARE = 7;

Adafruit_PWMServoDriver pwm(0x40, Wire);

// เซอร์โวหมุนต่อเนื่อง (360): จุดหยุดของแต่ละตัวไม่เท่ากัน ปรับได้ด้วยคำสั่ง z ในเมนู
uint16_t stopUs[8] = {1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500};
const int SPEED_SPAN_US = 500;  // ความเร็ว 100% = จุดหยุด +/- 500us (1000..2000us เต็มสปีด MG996R)

// ช่วงพัลส์ของเซอร์โวแบบกำหนดมุม MG90S
const uint16_t SERVO_MIN_US = 500;
const uint16_t SERVO_MAX_US = 2400;

// ล้อ: ฝั่งขวาติดตั้งกลับด้าน จึงต้องกลับทิศ
const int WHEEL_L_DIR = +1;
const int WHEEL_R_DIR = -1;
const bool DRIVE_REAR_WHEELS = false;  // true = ล้อหลังซ้าย/ขวาหมุนตามล้อหน้า

int wheelSpeed = 100;          // % (0..100) ปรับตอนทดสอบด้วยคำสั่ง v
uint32_t driveRunMs = 1000;    // เวลาวิ่งต่อหนึ่งคำสั่งตอนทดสอบ
uint32_t driveUntil = 0;
bool driving = false;
bool drivingForward = false;

// กล่องรับยา
enum BoxState { BOX_CLOSED, BOX_OPENING, BOX_OPEN, BOX_CLOSING };
BoxState boxState = BOX_CLOSED;
const int BOX_OPEN_DIR = +1;  // เปลี่ยนเป็น -1 ถ้ากล่องวิ่งผิดทิศ
const int BOX_SPEED = 100;    // %
uint32_t boxTravelMs = 1500;  // เวลาที่เลื่อนออก/เข้าจนสุด
uint32_t boxMoveUntil = 0;
uint32_t boxOpenedAt = 0;
uint32_t boxAlarmMs = 10UL * 60UL * 1000UL;  // 10 นาที
bool boxAlarmActive = false;

// ที่ดันยา (MG90S)
int pusherHomeAngle = 20;
int pusherPushAngle = 100;
uint32_t pusherHoldMs = 600;
enum PusherState { PUSH_IDLE, PUSH_PUSHING };
PusherState pusherState = PUSH_IDLE;
uint32_t pusherUntil = 0;

// แขน (MG90S)
int armHomeAngle = 90;
int armAngle = 90;
const int ARM_WAVE_SEQ[] = {60, 120, 60, 120, 60, 90};
const int ARM_WAVE_LEN = sizeof(ARM_WAVE_SEQ) / sizeof(ARM_WAVE_SEQ[0]);
int armSeqIdx = -1;
uint32_t armSeqNext = 0;

// ============================ เซ็นเซอร์ระยะ =========================
const uint8_t TOF_SENSOR_INDEX = 0;  // ใช้ XSHUT ตัวไหน (0 = XSHUT_1)
int obstacleMm = 200;                // หยุดเมื่อใกล้กว่านี้ ขณะเดินหน้า
int distMm = 9999;
bool tofOk = false;
uint32_t lastTof = 0;
#if USE_TOF_SENSOR
Adafruit_VL53L0X lox;
#endif

// ============================ BUZZER / พัดลม =====================
enum BuzzMode { BUZ_OFF, BUZ_BEEP, BUZ_DOUBLE, BUZ_ALARM };
BuzzMode buzzMode = BUZ_OFF;
uint32_t buzzStart = 0;
bool fanOn = false;
const bool FAN_ON_AT_BOOT = true;  // พัดลมระบายความร้อนทำงานตั้งแต่เปิดเครื่อง สลับได้ที่เมนู 6

// ============================ จอ LED =======================
// แผง WS2812B 16x16 เล่น GIF Mochi จาก mochi_frames.h
const uint16_t PANEL_W = 16;
const uint16_t PANEL_H = 16;
const uint16_t LED_COUNT_FRONT = PANEL_W * PANEL_H;
const uint16_t LED_COUNT_BACK = 0;  // ตั้งมากกว่า 0 ถ้าต่อแถบไฟด้านหลัง
uint8_t ledBrightness = 25;         // 0..255 (ตั้งต่ำไว้ แผงกินกระแส 5V สูง)

// รูปแบบการเดินสายในแผง (ค่าเดียวกับสเก็ตช์ Mochi ต้นฉบับ)
const bool SERPENTINE = true;
const bool START_BOTTOM = false;
const bool START_RIGHT = false;
const bool COLUMN_MAJOR = false;

Adafruit_NeoPixel ledFront(LED_COUNT_FRONT, PIN_RGB_FRONT, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ledBack(LED_COUNT_BACK ? LED_COUNT_BACK : 1, PIN_RGB_BACK, NEO_GRB + NEO_KHZ800);

int ledAnim = -1;          // ลำดับใน MOCHI_ANIMS, -1 = ปิดจอ
uint16_t ledFrame = 0;
uint16_t ledLoopsLeft = 0; // 0 = เล่นวนไม่หยุด
uint32_t ledNextAt = 0;
int ledIdleAnim = 0;       // เล่นเมื่ออนิเมชันแบบกำหนดรอบจบ
int ledAlertAnim = 0;      // เล่นระหว่างเตือนกล่องเปิดค้าง
void ledPlay(int idx, uint16_t loops);

// ============================ ทัช ============================
struct Touch {
  int pin;
  bool stable;
  bool lastRaw;
  uint32_t changedAt;
};
Touch touchBox = {PIN_TOUCH_BOX, false, false, 0};
Touch touchScreen = {PIN_TOUCH_SCREEN, false, false, 0};
bool autoMode = true;  // เซ็นเซอร์ทัชสั่งงานหุ่นยนต์

// ============================ ลิงก์กับ Raspberry Pi =========================
const uint32_t PI_LINK_TIMEOUT_MS = 6000;   // Pi ส่ง ping ทุก 2 วิ เงียบเกินนี้ = หลุด -> หยุดล้อ
const uint32_t PI_MOVE_TIMEOUT_MS = 10000;  // หยุดล้อเองถ้าไม่ได้รับ stop
const uint32_t STATUS_EVERY_MS = 10000;
uint32_t lastPiAt = 0;
uint32_t lastStatusAt = 0;
bool piDriving = false;
// จ่ายยา: ดันยาลงกล่อง -> เลื่อนกล่องออก -> แจ้ง Pi
enum DispStage { DS_IDLE, DS_PUSHING, DS_OPENING };
DispStage dispStage = DS_IDLE;
String dispId;
int dispSlot = 0;
char boxBy[8] = "menu";  // ใครสั่งกล่อง: touch / pi / menu
void sendBox(const char* state);
void sendStatus();

bool piLinked() { return lastPiAt && (millis() - lastPiAt) < PI_LINK_TIMEOUT_MS; }

// ============================ เมนู =============================
enum MenuState { M_MAIN, M_WHEELS, M_BOX, M_PUSHER, M_ARM, M_BUZZER, M_FAN, M_LED, M_SENSOR };
MenuState menuState = M_MAIN;
String inLine;
bool watchSensors = false;
uint32_t lastWatch = 0;

// ============================================================
// ฟังก์ชันพื้นฐาน
// ============================================================
void servoUs(uint8_t ch, uint16_t us) { pwm.writeMicroseconds(ch, us); }

void contRun(uint8_t ch, int speedPct) {  // -100..100
  speedPct = constrain(speedPct, -100, 100);
  servoUs(ch, stopUs[ch] + (speedPct * SPEED_SPAN_US) / 100);
}

void contStop(uint8_t ch) { servoUs(ch, stopUs[ch]); }

void servoAngle(uint8_t ch, int angle) {  // 0..180
  angle = constrain(angle, 0, 180);
  servoUs(ch, map(angle, 0, 180, SERVO_MIN_US, SERVO_MAX_US));
}

bool touchPressedEdge(Touch& t) {  // คืน true หนึ่งครั้งต่อการแตะ
  bool raw = (digitalRead(t.pin) == TOUCH_ACTIVE);
  uint32_t now = millis();
  if (raw != t.lastRaw) { t.lastRaw = raw; t.changedAt = now; }
  if ((now - t.changedAt) > 40 && raw != t.stable) {
    t.stable = raw;
    if (raw) return true;
  }
  return false;
}

int batteryMv() { return (int)(analogReadMilliVolts(PIN_BATT_SENSE) * BATT_DIVIDER); }

int batteryPercent() {
  int pct = map(batteryMv(), BATT_EMPTY_MV, BATT_FULL_MV, 0, 100);
  return constrain(pct, 0, 100);
}

// ============================================================
// ล้อ
// ============================================================
void wheelsSet(int l, int r) {
  contRun(CH_WHEEL_FL, l * WHEEL_L_DIR);
  contRun(CH_WHEEL_FR, r * WHEEL_R_DIR);
  if (DRIVE_REAR_WHEELS) {
    contRun(CH_WHEEL_RL, l * WHEEL_L_DIR);
    contRun(CH_WHEEL_RR, r * WHEEL_R_DIR);
  }
}

void wheelsStop() {
  contStop(CH_WHEEL_FL);
  contStop(CH_WHEEL_FR);
  contStop(CH_WHEEL_RL);
  contStop(CH_WHEEL_RR);
  driving = false;
  drivingForward = false;
  piDriving = false;
}

void sendObstacle() {
  if (piLinked()) Serial.printf("{\"evt\":\"obstacle\",\"mm\":%d}\n", distMm);
}

void wheelsMove(int l, int r, bool forward) {
  if (forward && distMm < obstacleMm) {
    Serial.printf("ไม่ออกตัว: มีสิ่งกีดขวางที่ %d mm (ระยะหยุด %d mm)\n", distMm, obstacleMm);
    buzzMode = BUZ_DOUBLE; buzzStart = millis();
    sendObstacle();
    return;
  }
  wheelsSet(l, r);
  driving = true;
  drivingForward = forward;
  driveUntil = millis() + driveRunMs;
}

void wheelsUpdate() {
  if (!driving) return;
  if (drivingForward && distMm < obstacleMm) {
    wheelsStop();
    buzzMode = BUZ_DOUBLE; buzzStart = millis();
    Serial.printf("เจอสิ่งกีดขวางที่ %d mm -> หยุด\n", distMm);
    sendObstacle();
    return;
  }
  if ((int32_t)(millis() - driveUntil) >= 0) wheelsStop();
}

// ============================================================
// กล่องรับยา
// ============================================================
void boxStart(bool open) {
  if (open && (boxState == BOX_OPEN || boxState == BOX_OPENING)) return;
  if (!open && (boxState == BOX_CLOSED || boxState == BOX_CLOSING)) return;
  int dir = open ? BOX_OPEN_DIR : -BOX_OPEN_DIR;
  contRun(CH_BOX, dir * BOX_SPEED);
  boxState = open ? BOX_OPENING : BOX_CLOSING;
  boxMoveUntil = millis() + boxTravelMs;
  Serial.println(open ? "กล่อง: กำลังเปิด" : "กล่อง: กำลังปิด");
  sendBox(open ? "opening" : "closing");
}

void boxToggle() {
  if (boxState == BOX_CLOSED || boxState == BOX_CLOSING) boxStart(true);
  else boxStart(false);
}

void boxUpdate() {
  uint32_t now = millis();
  if ((boxState == BOX_OPENING || boxState == BOX_CLOSING) &&
      (int32_t)(now - boxMoveUntil) >= 0) {
    contStop(CH_BOX);
    if (boxState == BOX_OPENING) {
      boxState = BOX_OPEN;
      boxOpenedAt = now;
      Serial.println("กล่อง: เปิดแล้ว");
      sendBox("open");
    } else {
      boxState = BOX_CLOSED;
      boxAlarmActive = false;
      if (buzzMode == BUZ_ALARM) buzzMode = BUZ_OFF;
      if (ledAnim == ledAlertAnim) ledPlay(ledIdleAnim, 0);
      Serial.println("กล่อง: ปิดแล้ว");
      sendBox("closed");
    }
  }
  if (boxState == BOX_OPEN && !boxAlarmActive && (now - boxOpenedAt) >= boxAlarmMs) {
    boxAlarmActive = true;
    buzzMode = BUZ_ALARM; buzzStart = now;
    ledPlay(ledAlertAnim, 0);
    Serial.println("กล่องเปิดค้างนานเกินกำหนด -> แจ้งเตือน");
    strcpy(boxBy, "alarm");
    sendBox("open");
  }
  if (boxState == BOX_OPENING || boxState == BOX_CLOSING) boxAlarmActive = false;
}

// ============================================================
// ที่ดันยา
// ============================================================
void pusherPush() {
  servoAngle(CH_PUSHER, pusherPushAngle);
  pusherState = PUSH_PUSHING;
  pusherUntil = millis() + pusherHoldMs;
  Serial.println("ที่ดันยา: ดัน");
}

void pusherUpdate() {
  if (pusherState == PUSH_PUSHING && (int32_t)(millis() - pusherUntil) >= 0) {
    servoAngle(CH_PUSHER, pusherHomeAngle);
    pusherState = PUSH_IDLE;
    Serial.println("ที่ดันยา: กลับตำแหน่งเดิม");
  }
}

// ============================================================
// แขน
// ============================================================
void armGo(int angle) {
  armAngle = constrain(angle, 0, 180);
  servoAngle(CH_ARM, armAngle);
}

void armWave() { armSeqIdx = 0; armSeqNext = 0; }

void armUpdate() {
  if (armSeqIdx < 0) return;
  uint32_t now = millis();
  if ((int32_t)(now - armSeqNext) < 0) return;
  if (armSeqIdx >= ARM_WAVE_LEN) { armSeqIdx = -1; return; }
  armGo(ARM_WAVE_SEQ[armSeqIdx++]);
  armSeqNext = now + 350;
}

// ============================================================
// BUZZER / พัดลม
// ============================================================
void buzzerUpdate() {
  uint32_t t = millis() - buzzStart;
  bool on = false;
  switch (buzzMode) {
    case BUZ_OFF: on = false; break;
    case BUZ_BEEP: on = (t < 150); if (t >= 150) buzzMode = BUZ_OFF; break;
    case BUZ_DOUBLE: on = (t < 120) || (t >= 240 && t < 360); if (t >= 360) buzzMode = BUZ_OFF; break;
    case BUZ_ALARM: on = ((t % 800) < 400); break;
  }
  digitalWrite(PIN_BUZZER, on ? HIGH : LOW);
}

void fanSet(bool on) {
  fanOn = on;
  digitalWrite(PIN_FAN, on ? HIGH : LOW);
}

// ทดสอบฮาร์ดแวร์ (หยุดรอ): สลับขา Gate 3 ครั้ง แล้วพิมพ์ค่าที่อ่านกลับ
// ใช้มัลติมิเตอร์วัดที่ Gate หรือขา _MINUS ระหว่างทดสอบ
void gpioSelfTest(int pin, const char* name) {
  bool before = (pin == PIN_FAN) ? fanOn : false;
  Serial.printf("ทดสอบ %s ที่ GPIO%d: กะพริบ 3 ครั้ง (ติด 1 วิ / ดับ 1 วิ)\n", name, pin);
  for (int i = 0; i < 3; i++) {
    digitalWrite(pin, HIGH); delay(1000);
    Serial.printf("  ติด -> อ่านกลับได้ %d\n", digitalRead(pin));
    digitalWrite(pin, LOW); delay(1000);
    Serial.printf("  ดับ -> อ่านกลับได้ %d\n", digitalRead(pin));
  }
  if (pin == PIN_FAN) fanSet(before);
}

// ============================================================
// จอ LED
// ============================================================
int findAnim(const char* name) {
  for (int i = 0; i < MOCHI_ANIM_COUNT; i++)
    if (strcmp(MOCHI_ANIMS[i].name, name) == 0) return i;
  return -1;
}

int panelXY(int x, int y) {
  if (START_RIGHT) x = PANEL_W - 1 - x;
  if (START_BOTTOM) y = PANEL_H - 1 - y;
  if (COLUMN_MAJOR) {
    int row = y;
    if (SERPENTINE && (x & 1)) row = PANEL_H - 1 - row;
    return x * PANEL_H + row;
  }
  int col = x;
  if (SERPENTINE && (y & 1)) col = PANEL_W - 1 - col;
  return y * PANEL_W + col;
}

uint32_t rgb565to888(uint16_t c) {
  uint8_t r = (c >> 11) & 0x1F, g = (c >> 5) & 0x3F, b = c & 0x1F;
  return Adafruit_NeoPixel::Color((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2));
}

void ledDrawFrame(uint16_t f) {
  for (int y = 0; y < MOCHI_SIZE && y < PANEL_H; y++)
    for (int x = 0; x < MOCHI_SIZE && x < PANEL_W; x++)
      ledFront.setPixelColor(panelXY(x, y), rgb565to888(MOCHI_PIXELS[f][y * MOCHI_SIZE + x]));
  ledFront.show();
}

// idx < 0 = ปิดจอ, loops = 0 -> เล่นวนไม่หยุด, มากกว่า 0 -> เล่น N รอบแล้วกลับไปท่าปกติ
void ledPlay(int idx, uint16_t loops) {
  if (idx >= MOCHI_ANIM_COUNT) idx = 0;
  ledAnim = idx;
  ledFrame = 0;
  ledLoopsLeft = loops;
  ledNextAt = 0;
  if (idx < 0) {
    ledFront.clear();
    ledFront.show();
    Serial.println("จอ LED: ปิด");
  } else {
    Serial.printf("จอ LED: %d %s\n", idx, MOCHI_ANIMS[idx].name);
  }
}

void ledUpdate() {
  if (ledAnim < 0) return;
  uint32_t now = millis();
  if ((int32_t)(now - ledNextAt) < 0) return;
  const MochiAnim& an = MOCHI_ANIMS[ledAnim];
  uint16_t f = an.start + ledFrame;
  ledDrawFrame(f);
  uint32_t nextAt = now + MOCHI_DELAY[f];
  ledFrame++;
  if (ledFrame >= an.count) {
    ledFrame = 0;
    if (ledLoopsLeft && --ledLoopsLeft == 0) ledPlay(ledIdleAnim, 0);
  }
  ledNextAt = nextAt;
}

void ledNext() {  // ข้าม "intro"
  int n = (ledAnim < 0) ? 1 : (ledAnim + 1) % MOCHI_ANIM_COUNT;
  if (n == 0) n = 1;
  ledPlay(n, 0);
}

// อารมณ์ของหุ่นจาก Pi -> ชื่ออนิเมชัน Mochi (ใช้ชื่อแรกที่มีใน mochi_frames.h)
// แก้รายชื่อให้ตรงกับไฟล์ของทีมได้เลย
struct MoodFace { const char* mood; const char* anims[4]; };
const MoodFace MOOD_FACES[] = {
  {"idle",      {"content", "idle", nullptr}},
  {"happy",     {"happy", "excited_2", "excited", nullptr}},
  {"listening", {"curious", "listening", "content", nullptr}},
  {"thinking",  {"thinking", "curious", "content", nullptr}},
  {"speaking",  {"talking", "talk", "happy", nullptr}},
  {"concerned", {"sad", "worried", "content", nullptr}},
  {"alert",     {"excited_2", "excited", "angry", nullptr}},
  {"pairing",   {"intro", "content", nullptr}},
  {"offline",   {"sad", "sleepy", "content", nullptr}},
  {"sleepy",    {"sleepy", "sleep", "tired", nullptr}},
  {"boot",      {"intro", "content", nullptr}},
};

void faceForMood(const String& mood) {
  if (boxAlarmActive) return;  // ระหว่างเตือนกล่องเปิดค้าง ให้หน้าเตือนค้างไว้
  for (const MoodFace& m : MOOD_FACES) {
    if (mood != m.mood) continue;
    for (int i = 0; i < 4 && m.anims[i]; i++) {
      int idx = findAnim(m.anims[i]);
      if (idx >= 0) { if (idx != ledAnim) ledPlay(idx, 0); return; }
    }
    return;
  }
}

// ============================================================
// เซ็นเซอร์ระยะ
// ============================================================
void tofInit() {
  for (int i = 0; i < 4; i++) {  // กดรีเซ็ตเซ็นเซอร์ทุกตัวไว้ก่อน
    pinMode(PIN_XSHUT[i], OUTPUT);
    digitalWrite(PIN_XSHUT[i], LOW);
  }
#if USE_TOF_SENSOR
  delay(10);
  pinMode(PIN_XSHUT[TOF_SENSOR_INDEX], INPUT);  // ปล่อยขา -> pull-up บนโมดูลปลุกเซ็นเซอร์
  delay(20);
  tofOk = lox.begin(0x29, false, &Wire);
  if (tofOk) Serial.println("เซ็นเซอร์ระยะ: พร้อมใช้งาน");
  else Serial.println("เซ็นเซอร์ระยะ: ไม่พบ! ล้อจะไม่หยุดเองเมื่อเจอสิ่งกีดขวาง (เช็กสาย I2C / XSHUT)");
#else
  Serial.println("เซ็นเซอร์ระยะ: ปิดใช้งาน (USE_TOF_SENSOR=0)");
#endif
}

void tofUpdate() {
#if USE_TOF_SENSOR
  if (!tofOk) return;
  uint32_t now = millis();
  if (now - lastTof < 100) return;
  lastTof = now;
  VL53L0X_RangingMeasurementData_t m;
  lox.rangingTest(&m, false);
  distMm = (m.RangeStatus != 4) ? (int)m.RangeMilliMeter : 9999;  // 4 = เกินระยะวัด
#endif
}

// ============================================================
// การทำงานรวม
// ============================================================
void stopAll() {
  wheelsStop();
  contStop(CH_BOX);
  if (boxState == BOX_OPENING) { boxState = BOX_OPEN; boxOpenedAt = millis(); }
  if (boxState == BOX_CLOSING) boxState = BOX_CLOSED;
  buzzMode = BUZ_OFF;
  armSeqIdx = -1;
  Serial.println("หยุดทุกอย่าง");
}

void dispenseDemo() {
  if (boxState != BOX_CLOSED) { Serial.println("กรุณาปิดกล่องก่อน"); return; }
  pusherPush();
  ledPlay(findAnim("excited_2"), 2);
  buzzMode = BUZ_DOUBLE; buzzStart = millis();
  Serial.println("ยาตกลงกล่องแล้ว แตะเซ็นเซอร์ทัช 1 (หรือใช้เมนู) เพื่อเลื่อนกล่องออก");
}

// ============================================================
// ลิงก์กับ Raspberry Pi: JSON ทีละบรรทัด (ไม่ต้องใช้ไลบรารีเพิ่ม)
// ============================================================
// อ่านค่า "key":"value" หรือ "key":123 จากบรรทัด JSON แบบแบน (Pi ส่งรูปแบบนี้เท่านั้น)
String jStr(const String& s, const char* key, const char* def = "") {
  String k = String("\"") + key + "\":";
  int i = s.indexOf(k);
  if (i < 0) return def;
  i += k.length();
  while (i < (int)s.length() && s[i] == ' ') i++;
  if (i < (int)s.length() && s[i] == '"') {
    int j = s.indexOf('"', i + 1);
    return j < 0 ? String(def) : s.substring(i + 1, j);
  }
  int j = i;
  while (j < (int)s.length() && s[j] != ',' && s[j] != '}') j++;
  return s.substring(i, j);
}

long jInt(const String& s, const char* key, long def) {
  String v = jStr(s, key, "");
  return v.length() ? v.toInt() : def;
}

void sendBox(const char* state) {
  bool done = !strcmp(state, "open") || !strcmp(state, "closed");
  if (piLinked()) Serial.printf("{\"evt\":\"box\",\"state\":\"%s\",\"by\":\"%s\"}\n", state, boxBy);
  if (done) strcpy(boxBy, "menu");  // จบการเลื่อนแล้ว: ครั้งหน้าค่อยบอกใหม่ว่าใครสั่ง
}

void sendStatus() {
  const char* bs[] = {"closed", "opening", "open", "closing"};
  int mv = batteryMv();
  Serial.print("{\"evt\":\"status\",");
  if (mv > 500) Serial.printf("\"battery\":%d,\"battMv\":%d,", batteryPercent(), mv);
  Serial.printf("\"charging\":false,\"box\":\"%s\",\"dist\":%d,\"tof\":%s,\"fan\":%s,\"anim\":\"%s\"}\n",
                bs[boxState], distMm, tofOk ? "true" : "false", fanOn ? "true" : "false",
                ledAnim < 0 ? "off" : MOCHI_ANIMS[ledAnim].name);
}

void sendHello() {
  Serial.printf("{\"evt\":\"hello\",\"fw\":\"%s\",\"board\":\"esp32s3-medbot\",\"slots\":1,\"mochi\":%s,"
                "\"caps\":[\"wheels\",\"box\",\"pusher\",\"arm\",\"face\",\"buzzer\",\"fan\",\"tof\"]}\n",
                PEBBLE_FW, HAVE_MOCHI ? "true" : "false");
}

void sendDispensed(bool ok, const char* why) {
  Serial.printf("{\"evt\":\"dispensed\",\"slot\":%d,\"id\":\"%s\",\"ok\":%s%s%s%s}\n", dispSlot, dispId.c_str(),
                ok ? "true" : "false", why[0] ? ",\"msg\":\"" : "", why, why[0] ? "\"" : "");
}

void piMove(const String& dir, int speed) {
  speed = constrain(speed, 0, 100);
  if (dir == "stop") { wheelsStop(); return; }
  bool fwd = dir == "forward";
  int l = 0, r = 0;
  if (dir == "forward") { l = speed; r = speed; }
  else if (dir == "backward") { l = -speed; r = -speed; }
  else if (dir == "left") { l = -speed; r = speed; }
  else if (dir == "right") { l = speed; r = -speed; }
  else { wheelsStop(); return; }
  if (fwd && distMm < obstacleMm) {
    buzzMode = BUZ_DOUBLE; buzzStart = millis();
    sendObstacle();
    return;
  }
  wheelsSet(l, r);
  driving = true;
  drivingForward = fwd;
  piDriving = true;
  driveUntil = millis() + PI_MOVE_TIMEOUT_MS;  // ปกติ Pi ส่ง stop ก่อนถึงเวลานี้
}

void piDispense(int slot, const String& id) {
  dispSlot = slot;
  dispId = id;
  if (dispStage != DS_IDLE) { sendDispensed(false, "busy"); return; }
  if (boxState != BOX_CLOSED) { sendDispensed(false, "box not closed"); return; }
  pusherPush();
  int ex = findAnim("excited_2");
  if (ex >= 0) ledPlay(ex, 2);
  buzzMode = BUZ_DOUBLE; buzzStart = millis();
  dispStage = DS_PUSHING;
}

void dispenseUpdate() {
  if (dispStage == DS_PUSHING && pusherState == PUSH_IDLE) {
    strcpy(boxBy, "pi");
    boxStart(true);  // ยาตกลงกล่องแล้ว -> เลื่อนกล่องออกให้หยิบ
    dispStage = DS_OPENING;
  } else if (dispStage == DS_OPENING && boxState == BOX_OPEN) {
    sendDispensed(true, "");
    dispStage = DS_IDLE;
  } else if (dispStage == DS_OPENING && boxState == BOX_CLOSED) {
    sendDispensed(false, "box closed");
    dispStage = DS_IDLE;
  }
}

void handlePi(const String& s) {
  lastPiAt = millis();
  String cmd = jStr(s, "cmd");
  if (cmd == "ping") { Serial.println("{\"evt\":\"pong\"}"); }
  else if (cmd == "status") { sendStatus(); }
  else if (cmd == "hello") { sendHello(); sendStatus(); }
  else if (cmd == "move") { piMove(jStr(s, "dir", "stop"), jInt(s, "speed", 60)); }
  else if (cmd == "dispense") { piDispense(jInt(s, "slot", 1), jStr(s, "id")); }
  else if (cmd == "box") {
    String d = jStr(s, "do", "toggle");
    strcpy(boxBy, "pi");
    if (d == "open") boxStart(true);
    else if (d == "close") boxStart(false);
    else boxToggle();
  }
  else if (cmd == "arm") {
    String d = jStr(s, "do", "wave");
    if (d == "wave") armWave();
    else if (d == "home") armGo(armHomeAngle);
    else armGo(jInt(s, "angle", armHomeAngle));
  }
  else if (cmd == "face") {
    String anim = jStr(s, "anim");
    if (anim.length()) { int i = findAnim(anim.c_str()); if (i >= 0) ledPlay(i, jInt(s, "loops", 0)); }
    else faceForMood(jStr(s, "mood", "idle"));
  }
  else if (cmd == "brightness") { ledBrightness = constrain(jInt(s, "value", ledBrightness), 0, 255); ledFront.setBrightness(ledBrightness); }
  else if (cmd == "buzz" || cmd == "beep") {
    String m = jStr(s, "mode", "beep");
    buzzMode = m == "double" ? BUZ_DOUBLE : m == "alarm" ? BUZ_ALARM : m == "off" ? BUZ_OFF : BUZ_BEEP;
    buzzStart = millis();
  }
  else if (cmd == "fan") { fanSet(jStr(s, "on", "true") == "true"); }
  else if (cmd == "stop") { stopAll(); }
  else if (cmd == "head") { /* หุ่นรุ่นนี้ไม่มีหัวหมุน: เงียบไว้ */ }
  else { Serial.printf("{\"evt\":\"error\",\"msg\":\"unknown cmd %s\"}\n", cmd.c_str()); }
}

void piUpdate() {
  uint32_t now = millis();
  // ความปลอดภัย: Pi เงียบ (สายหลุด / Pi ค้าง) ระหว่างที่ Pi สั่งล้ออยู่ -> หยุด
  if (piDriving && lastPiAt && now - lastPiAt > PI_LINK_TIMEOUT_MS) {
    wheelsStop();
    Serial.println("{\"evt\":\"error\",\"msg\":\"lost the Pi: stopped\"}");
  }
  if (piDriving && !driving) piDriving = false;
  if (piLinked() && now - lastStatusAt > STATUS_EVERY_MS) {
    lastStatusAt = now;
    sendStatus();
  }
}

// ============================================================
// ข้อความเมนู
// ============================================================
void printStatus() {
  const char* bs[] = {"ปิด", "กำลังเปิด", "เปิด", "กำลังปิด"};
  Serial.println("---- สถานะ ----");
  Serial.printf("โหมดอัตโนมัติ (ทัช) : %s\n", autoMode ? "เปิด" : "ปิด");
  Serial.printf("Raspberry Pi        : %s\n", piLinked() ? "เชื่อมต่ออยู่" : "ไม่ได้ต่อ");
  Serial.printf("กล่องรับยา : %s  เวลาเลื่อน=%lu ms  เตือนหลัง=%lu วินาที\n", bs[boxState],
                (unsigned long)boxTravelMs, (unsigned long)(boxAlarmMs / 1000));
  Serial.printf("ระยะ : %d mm (ระยะหยุด %d)  เซ็นเซอร์ %s\n", distMm, obstacleMm, tofOk ? "ปกติ" : "ไม่พบ");
  Serial.printf("ทัช 1 / 2 : %d / %d\n", touchBox.stable, touchScreen.stable);
  Serial.printf("พัดลม : %s\n", fanOn ? "เปิด" : "ปิด");
  Serial.printf("จอ LED : %s  ความสว่าง %u%s\n", ledAnim < 0 ? "ปิด" : MOCHI_ANIMS[ledAnim].name, ledBrightness,
                HAVE_MOCHI ? "" : "  (ยังไม่มี mochi_frames.h ใช้หน้าสำรอง)");
  Serial.printf("มุมแขน : %d\n", armAngle);
  Serial.printf("ขาแบตเตอรี่ : %d mV ที่ขา -> แบต ~%d mV (~%d%%)  ปรับ BATT_* ให้ตรงแบตจริง\n",
                (int)analogReadMilliVolts(PIN_BATT_SENSE), batteryMv(), batteryPercent());
}

void printMenu() {
  switch (menuState) {
    case M_MAIN:
      Serial.println();
      Serial.println("=========== เมนู MEDBOT ===========");
      Serial.println(" 1 ล้อ");
      Serial.println(" 2 กล่องรับยา (เลื่อนออก/เข้า)");
      Serial.println(" 3 ที่ดันยา");
      Serial.println(" 4 แขนหุ่นยนต์");
      Serial.println(" 5 Buzzer (แจ้งเตือน)");
      Serial.println(" 6 พัดลม");
      Serial.println(" 7 จอ LED");
      Serial.println(" 8 เซ็นเซอร์ (ระยะ / ทัช)");
      Serial.println(" 9 สถานะ");
      Serial.println(" d ทดสอบจ่ายยา (ดันยาลงกล่อง แล้วรอแตะเซ็นเซอร์)");
      Serial.println(" a เปิด/ปิดโหมดอัตโนมัติ (เซ็นเซอร์ทัชทำงาน)");
      Serial.println(" x หยุดทุกอย่าง (ใช้ได้ทุกเมนู)");
      Serial.println(" m กลับเมนูหลัก (ใช้ได้ทุกเมนู)");
      break;
    case M_WHEELS:
      Serial.println("--- ล้อ ---");
      Serial.println(" f เดินหน้า | b ถอยหลัง | l เลี้ยวซ้าย | r เลี้ยวขวา | s หยุด");
      Serial.printf(" v <0-100> ความเร็ว (ตอนนี้ %d) | t เวลาวิ่งต่อคำสั่ง (ตอนนี้ %lu)\n", wheelSpeed, (unsigned long)driveRunMs);
      Serial.printf(" zl / zr ปรับจุดหยุดเซอร์โวซ้าย/ขวา (ตอนนี้ %u / %u)\n", stopUs[CH_WHEEL_FL], stopUs[CH_WHEEL_FR]);
      break;
    case M_BOX:
      Serial.println("--- กล่องรับยา ---");
      Serial.println(" o เปิด | c ปิด | t สลับ");
      Serial.printf(" d เวลาเลื่อน (ตอนนี้ %lu) | w <วินาที> เวลาเตือนเปิดค้าง (ตอนนี้ %lu)\n",
                    (unsigned long)boxTravelMs, (unsigned long)(boxAlarmMs / 1000));
      Serial.printf(" z ปรับจุดหยุดเซอร์โว (ตอนนี้ %u)\n", stopUs[CH_BOX]);
      break;
    case M_PUSHER:
      Serial.println("--- ที่ดันยา ---");
      Serial.println(" p ดันยา | a <องศา> ไปที่มุม");
      Serial.printf(" n <องศา> มุมดัน (ตอนนี้ %d) | h <องศา> มุมกลับ (ตอนนี้ %d)\n", pusherPushAngle, pusherHomeAngle);
      break;
    case M_ARM:
      Serial.println("--- แขน ---");
      Serial.printf(" a <0-180> ไปที่มุม (ตอนนี้ %d) | h ท่าเริ่มต้น (%d) | w โบกมือ | n <องศา> ตั้งท่าเริ่มต้น\n", armAngle, armHomeAngle);
      break;
    case M_BUZZER:
      Serial.println("--- Buzzer ---");
      Serial.println(" b ดังสั้น | d ดังสองครั้ง | a เสียงเตือนต่อเนื่อง | s หยุด | t ทดสอบขา (กะพริบ 3 ครั้ง)");
      break;
    case M_FAN:
      Serial.println("--- พัดลม ---");
      Serial.println(" 1 เปิด | 0 ปิด | t ทดสอบขา (กะพริบ 3 ครั้ง)");
      break;
    case M_LED:
      Serial.println("--- จอ LED ---");
      for (int i = 0; i < MOCHI_ANIM_COUNT; i++) Serial.printf(" %2d %s\n", i, MOCHI_ANIMS[i].name);
      Serial.println(" <เลข> เล่นอนิเมชัน | n ถัดไป | o ปิดจอ | br <0-255> ความสว่าง");
      break;
    case M_SENSOR:
      Serial.println("--- เซ็นเซอร์ ---");
      Serial.println(" r อ่านระยะครั้งเดียว | w เปิด/ปิดการแสดงค่าสด");
      Serial.printf(" o ระยะหยุดเมื่อเจอสิ่งกีดขวาง (ตอนนี้ %d)\n", obstacleMm);
      break;
  }
}

// ============================================================
// รับคำสั่งเมนู
// ============================================================
long argInt(const String& s, long def) {
  int sp = s.indexOf(' ');
  if (sp < 0) return def;
  return s.substring(sp + 1).toInt();
}

void enter(MenuState m) { menuState = m; printMenu(); }

void handleLine(String s) {
  s.trim();
  if (s.startsWith("{")) { handlePi(s); return; }  // คำสั่งจาก Raspberry Pi
  s.toLowerCase();
  if (s == "x" || s == "stop") { stopAll(); return; }
  if (s == "m") { enter(M_MAIN); return; }

  switch (menuState) {
    case M_MAIN:
      if (s == "1") enter(M_WHEELS);
      else if (s == "2") enter(M_BOX);
      else if (s == "3") enter(M_PUSHER);
      else if (s == "4") enter(M_ARM);
      else if (s == "5") enter(M_BUZZER);
      else if (s == "6") enter(M_FAN);
      else if (s == "7") enter(M_LED);
      else if (s == "8") enter(M_SENSOR);
      else if (s == "9") printStatus();
      else if (s == "d") dispenseDemo();
      else if (s == "a") { autoMode = !autoMode; Serial.printf("โหมดอัตโนมัติ %s\n", autoMode ? "เปิด" : "ปิด"); }
      else Serial.println("? ไม่รู้จักคำสั่ง พิมพ์ m เพื่อดูเมนู");
      break;

    case M_WHEELS:
      if (s == "f") wheelsMove(wheelSpeed, wheelSpeed, true);
      else if (s == "b") wheelsMove(-wheelSpeed, -wheelSpeed, false);
      else if (s == "l") wheelsMove(-wheelSpeed, wheelSpeed, false);
      else if (s == "r") wheelsMove(wheelSpeed, -wheelSpeed, false);
      else if (s == "s") wheelsStop();
      else if (s.startsWith("v")) { wheelSpeed = constrain(argInt(s, wheelSpeed), 0, 100); Serial.printf("ความเร็ว %d%%\n", wheelSpeed); }
      else if (s.startsWith("t")) { driveRunMs = constrain(argInt(s, driveRunMs), 100, 10000); Serial.printf("เวลาวิ่ง %lu ms\n", (unsigned long)driveRunMs); }
      else if (s.startsWith("zl")) { stopUs[CH_WHEEL_FL] = stopUs[CH_WHEEL_RL] = constrain(argInt(s, 1500), 1300, 1700); contStop(CH_WHEEL_FL); Serial.printf("จุดหยุดล้อซ้าย %u us\n", stopUs[CH_WHEEL_FL]); }
      else if (s.startsWith("zr")) { stopUs[CH_WHEEL_FR] = stopUs[CH_WHEEL_RR] = constrain(argInt(s, 1500), 1300, 1700); contStop(CH_WHEEL_FR); Serial.printf("จุดหยุดล้อขวา %u us\n", stopUs[CH_WHEEL_FR]); }
      else printMenu();
      break;

    case M_BOX:
      if (s == "o") boxStart(true);
      else if (s == "c") boxStart(false);
      else if (s == "t") boxToggle();
      else if (s.startsWith("d")) { boxTravelMs = constrain(argInt(s, boxTravelMs), 200, 10000); Serial.printf("เวลาเลื่อน %lu ms\n", (unsigned long)boxTravelMs); }
      else if (s.startsWith("w")) { boxAlarmMs = (uint32_t)constrain(argInt(s, 600), 3, 3600) * 1000UL; Serial.printf("เตือนเมื่อเปิดค้างเกิน %lu วินาที\n", (unsigned long)(boxAlarmMs / 1000)); }
      else if (s.startsWith("z")) { stopUs[CH_BOX] = constrain(argInt(s, 1500), 1300, 1700); contStop(CH_BOX); Serial.printf("จุดหยุดกล่อง %u us\n", stopUs[CH_BOX]); }
      else printMenu();
      break;

    case M_PUSHER:
      if (s == "p") pusherPush();
      else if (s.startsWith("a")) { servoAngle(CH_PUSHER, argInt(s, pusherHomeAngle)); }
      else if (s.startsWith("n")) { pusherPushAngle = constrain(argInt(s, pusherPushAngle), 0, 180); Serial.printf("มุมดัน %d\n", pusherPushAngle); }
      else if (s.startsWith("h")) { pusherHomeAngle = constrain(argInt(s, pusherHomeAngle), 0, 180); servoAngle(CH_PUSHER, pusherHomeAngle); Serial.printf("มุมกลับ %d\n", pusherHomeAngle); }
      else printMenu();
      break;

    case M_ARM:
      if (s == "h") armGo(armHomeAngle);
      else if (s == "w") armWave();
      else if (s.startsWith("a")) { armGo(argInt(s, armAngle)); Serial.printf("แขนที่มุม %d\n", armAngle); }
      else if (s.startsWith("n")) { armHomeAngle = constrain(argInt(s, armHomeAngle), 0, 180); Serial.printf("ท่าเริ่มต้นของแขน %d\n", armHomeAngle); }
      else printMenu();
      break;

    case M_BUZZER:
      if (s == "b") { buzzMode = BUZ_BEEP; buzzStart = millis(); }
      else if (s == "d") { buzzMode = BUZ_DOUBLE; buzzStart = millis(); }
      else if (s == "a") { buzzMode = BUZ_ALARM; buzzStart = millis(); }
      else if (s == "s") { buzzMode = BUZ_OFF; }
      else if (s == "t") { buzzMode = BUZ_OFF; gpioSelfTest(PIN_BUZZER, "Buzzer"); }
      else printMenu();
      break;

    case M_FAN:
      if (s == "1") { fanSet(true); Serial.println("พัดลม: เปิด"); }
      else if (s == "0") { fanSet(false); Serial.println("พัดลม: ปิด"); }
      else if (s == "t") gpioSelfTest(PIN_FAN, "พัดลม");
      else printMenu();
      break;

    case M_LED:
      if (s == "n") ledNext();
      else if (s == "o") ledPlay(-1, 0);
      else if (s.startsWith("br")) {
        ledBrightness = constrain(argInt(s, ledBrightness), 0, 255);
        ledFront.setBrightness(ledBrightness);
        Serial.printf("ความสว่าง %u\n", ledBrightness);
      } else if (s[0] >= '0' && s[0] <= '9') {
        int n = (int)s.toInt();
        if (n < MOCHI_ANIM_COUNT) ledPlay(n, 0); else printMenu();
      } else printMenu();
      break;

    case M_SENSOR:
      if (s == "r") Serial.printf("ระยะ %d mm | ทัช1 %d | ทัช2 %d\n", distMm, touchBox.stable, touchScreen.stable);
      else if (s == "w") { watchSensors = !watchSensors; Serial.printf("แสดงค่าสด %s\n", watchSensors ? "เปิด" : "ปิด"); }
      else if (s.startsWith("o")) { obstacleMm = constrain(argInt(s, obstacleMm), 30, 2000); Serial.printf("ระยะหยุด %d mm\n", obstacleMm); }
      else printMenu();
      break;
  }
}

void serialUpdate() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (inLine.length()) handleLine(inLine);
      inLine = "";
    } else if (inLine.length() < 300) {  // JSON จาก Pi ยาวกว่าคำสั่งเมนู
      inLine += c;
    }
  }
  if (watchSensors && millis() - lastWatch > 500) {
    lastWatch = millis();
    Serial.printf("ระยะ %5d mm | ทัช1 %d | ทัช2 %d\n", distMm, touchBox.stable, touchScreen.stable);
  }
}

// ============================================================
// ทัช (โหมดอัตโนมัติ)
// ============================================================
void touchUpdate() {
  bool t1 = touchPressedEdge(touchBox);
  bool t2 = touchPressedEdge(touchScreen);
  if (!autoMode) return;
  if (t1) {
    Serial.println("แตะเซ็นเซอร์ 1: กล่องรับยา");
    bool opening = (boxState == BOX_CLOSED || boxState == BOX_CLOSING);
    strcpy(boxBy, "touch");
    boxToggle();
    buzzMode = BUZ_BEEP; buzzStart = millis();
    if (opening) { int h = findAnim("happy"); if (h >= 0) ledPlay(h, 2); }
  }
  if (t2) {
    if (piLinked()) {  // ต่อกับ Pi: ทัช 2 = ปุ่มคุยกับ Pebbi
      Serial.println("{\"evt\":\"button\",\"press\":\"short\",\"by\":\"touch2\"}");
      buzzMode = BUZ_BEEP; buzzStart = millis();
    } else {
      Serial.println("แตะเซ็นเซอร์ 2: เปลี่ยนหน้าจอ");
      ledNext();
    }
  }
}

// ============================================================
// SETUP / LOOP
// ============================================================
void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  pinMode(PIN_BUZZER, OUTPUT); digitalWrite(PIN_BUZZER, LOW);
  pinMode(PIN_FAN, OUTPUT); fanSet(FAN_ON_AT_BOOT);
  pinMode(PIN_TOUCH_BOX, INPUT);
  pinMode(PIN_TOUCH_SCREEN, INPUT);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(400000);

  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(50);
  delay(10);
  for (uint8_t ch = 0; ch <= CH_BOX; ch++) contStop(ch);  // เซอร์โว 360 ทุกตัวหยุด
  servoAngle(CH_PUSHER, pusherHomeAngle);                  // เซอร์โวมุมไปท่าเริ่มต้นตรง ๆ ไม่ให้กระตุก
  servoAngle(CH_ARM, armHomeAngle);

#if !HAVE_MOCHI
  buildFallbackFace();
#endif
  ledFront.begin();
  ledFront.setBrightness(ledBrightness);
  ledFront.clear();
  ledFront.show();
  if (LED_COUNT_BACK) { ledBack.begin(); ledBack.clear(); ledBack.show(); }
  ledIdleAnim = max(findAnim("content"), 0);
  ledAlertAnim = max(findAnim("angry"), 0);
  ledPlay(max(findAnim("intro"), 0), 1);  // เล่น intro หนึ่งรอบ แล้วเข้าท่าปกติ

  tofInit();

  Serial.println("\nMedBot พร้อมใช้งาน (ต่อ Raspberry Pi ทาง USB หรือทดสอบผ่านเมนูนี้)");
  sendHello();
  printMenu();
}

void loop() {
  serialUpdate();
  tofUpdate();
  touchUpdate();
  wheelsUpdate();
  boxUpdate();
  pusherUpdate();
  dispenseUpdate();
  armUpdate();
  buzzerUpdate();
  ledUpdate();
  piUpdate();
}
