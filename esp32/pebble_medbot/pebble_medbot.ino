/*
 * MedBot - ESP32-S3-WROOM-1 (N16R8)  -  ทำงานเองอัตโนมัติ (ไม่มีเมนู, ยังไม่ใช้ WiFi)
 *
 * pebble(a): โค้ดทีม (เวอร์ชันอัตโนมัติ) + ลิงก์คุยกับ Raspberry Pi 5
 *   สาย RX/TX ที่ GPIO43/44 (UART) : Pi ขา 8 (TX) -> GPIO44 (RXD0), Pi ขา 10 (RX) <- GPIO43 (TXD0), GND-GND
 *   หรือสาย USB ก็ได้ ข้อความเหมือนกัน: JSON ทีละบรรทัด 115200 (ดู ../PROTOCOL.md)
 *   - Pi สั่ง: ล้อ, จ่ายยา, กล่อง, แขน, หน้าจอตามอารมณ์, เช็กอินความรู้สึก (เล่นอนิเมชัน Mochi), buzzer, พัดลม
 *   - ESP32 แจ้ง Pi: สถานะ/แบต/ระยะ, กล่องเปิด-ปิด (แตะปิดหลังจ่ายยา = กินยาแล้ว), ทัช 2 = ปุ่มคุยกับ Pebbi
 *   - ไม่ได้ต่อ Pi: ทำงานเองเหมือนเดิมทุกอย่าง
 *
 * พฤติกรรม:
 *   - เปิดเครื่อง: พัดลมติด, จอ Mochi เล่น intro แล้วเข้าท่าปกติ (content)
 *   - แตะเซ็นเซอร์ทัช 1 (GPIO4) ตอนกล่องปิด : ที่ดันยาปล่อยยาลงกล่อง แล้วกล่องรับยาเลื่อนออก
 *   - แตะเซ็นเซอร์ทัช 1 ตอนกล่องเปิด        : กล่องเลื่อนกลับเข้า
 *   - กล่องเปิดค้างเกิน 10 นาที              : Buzzer ดัง + จอเล่น angry จนกว่าจะปิดกล่อง
 *   - แตะเซ็นเซอร์ทัช 2 (GPIO5)              : เปลี่ยนอนิเมชันบนจอถัดไป
 *   - ล้อและแขน: ยังไม่มีตัวสั่ง (รอ WiFi / Raspberry Pi) มีฟังก์ชันพร้อมเรียกใช้
 *     ฟังก์ชันล้อมีระบบหยุดเองเมื่อเซ็นเซอร์ระยะเจอสิ่งกีดขวาง
 *   - Serial (USB) พิมพ์บันทึกเหตุการณ์และสถานะทุก 5 วินาที ปิดได้ที่ DEBUG_SERIAL = 0
 *
 * ตั้งค่า Arduino IDE (เมนู Tools):
 *   Board              : ESP32S3 Dev Module
 *   USB CDC On Boot    : Enabled      <-- จำเป็น เพราะ Serial ใช้ USB ตรง (GPIO19/20)
 *   USB Mode           : Hardware CDC and JTAG
 *   PSRAM              : OPI PSRAM    (N16R8)
 *   Flash Size         : 16MB
 *   Serial Monitor     : 115200 baud
 *
 * ไลบรารี (Library Manager):
 *   - Adafruit PWM Servo Driver Library   (PCA9685)
 *   - Adafruit NeoPixel                   (WS2812B)
 *   - Adafruit VL53L0X                    (เซ็นเซอร์วัดระยะ ใช้เมื่อ USE_TOF_SENSOR = 1)
 *
 * แผนผังขา (ตาม schematic):
 *   GPIO4  TOUCH_SENSOR1  TTP223  -> กล่องรับยา
 *   GPIO5  TOUCH_SENSOR2  TTP223  -> เปลี่ยนหน้าจอ LED
 *   GPIO8  I2C_SDA        GPIO9  I2C_SCL   (PCA9685 ที่ 0x40, เซ็นเซอร์ระยะที่ 0x29)
 *   GPIO10..13 XSHUT_1..4 (เซ็นเซอร์ระยะ สแกนหาเองทั้ง 4 ขา)
 *   GPIO38 RGB_FRONT      GPIO39 RGB_BACK  (WS2812B ผ่าน 74AHCT125)
 *          RGB_FRONT ขับแผง 16x16 เล่น GIF Mochi (mochi_frames.h ต้องอยู่โฟลเดอร์เดียวกัน)
 *   GPIO40 ESP_BUZZER     GPIO41 ESP_FAN   (AO3400A สวิตช์ฝั่งลบ, HIGH = เปิด)
 *   GPIO1  BATT_SENSE     (R24 100k / R27 10k หาร 11 เท่า)
 *   GPIO43/44 (TXD0/RXD0) เว้นไว้สำหรับต่อ Raspberry Pi
 *
 * ช่องของ PCA9685:
 *   0 WHEEL_FL   1 WHEEL_FR   2 WHEEL_RL   3 WHEEL_RR
 *   4 MED_1 (กล่องรับยาเลื่อนออก, MG996R 360)
 *   5 MED_2 (ที่ดันยา,            MG90S 180)
 *   6 ARM   (แขนหุ่นยนต์,         MG90S 180)
 *   7 SPARE
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <Adafruit_NeoPixel.h>
#include "mochi_frames.h"      // เฟรม GIF Mochi 16x16 (RGB565) ต้องอยู่ข้างไฟล์ .ino นี้

#define USE_TOF_SENSOR 1   // 1 = มีเซ็นเซอร์ VL53L0X ทาง I2C, 0 = ไม่มี (ระยะ = ไกลเสมอ)
#define DEBUG_SERIAL   1   // 1 = พิมพ์บันทึกทาง USB Serial, 0 = ปิด
#if USE_TOF_SENSOR
#include <Adafruit_VL53L0X.h>
#endif

#define PEBBLE_FW "medbot-3.0.0"

// ===================== สาย RX/TX ไป Raspberry Pi (GPIO43/44) =====================
#define PI_UART 1                 // 0 = คุยกับ Pi ผ่านสาย USB อย่างเดียว
const int PIN_PI_RX = 44;         // RXD0 <- Pi ขา 8 (GPIO14 TX)
const int PIN_PI_TX = 43;         // TXD0 -> Pi ขา 10 (GPIO15 RX)
const uint32_t PI_UART_BAUD = 115200;

// ข้อความ JSON ถึง Pi: ส่งทั้ง USB และสาย RX/TX
class PiTee : public Print {
 public:
  size_t write(uint8_t c) override {
    Serial.write(c);
#if PI_UART
    Serial1.write(c);
#endif
    return 1;
  }
  size_t write(const uint8_t* buf, size_t n) override {
    Serial.write(buf, n);
#if PI_UART
    Serial1.write(buf, n);
#endif
    return n;
  }
};
PiTee PiOut;

#define LOGLN(x)  do { if (DEBUG_SERIAL) Serial.println(x); } while (0)
#define LOGF(...) do { if (DEBUG_SERIAL) Serial.printf(__VA_ARGS__); } while (0)

// ============================ ขา ============================
const int PIN_TOUCH_BOX    = 4;
const int PIN_TOUCH_SCREEN = 5;
const int PIN_I2C_SDA      = 8;
const int PIN_I2C_SCL      = 9;
const int PIN_XSHUT[4]     = {10, 11, 12, 13};
const int PIN_RGB_FRONT    = 38;
const int PIN_RGB_BACK     = 39;
const int PIN_BUZZER       = 40;
const int PIN_FAN          = 41;
const int PIN_BATT_SENSE   = 1;
const float BATT_DIV       = 11.0f;  // แรงดันแบต = แรงดันที่ขา x 11
// เปอร์เซ็นต์แบตที่ส่งให้ Pi/แอป: ปรับ 2 ค่านี้ให้ตรงกับแบตจริง (ค่าเริ่มต้น = Li-ion 2 ก้อนอนุกรม)
const int BATT_EMPTY_MV    = 6600;
const int BATT_FULL_MV     = 8400;

const int TOUCH_ACTIVE     = HIGH;   // TTP223 ค่าปกติ: แตะแล้วเป็น HIGH

// ========================= PCA9685 / เซอร์โว =====================
const uint8_t CH_WHEEL_FL = 0;
const uint8_t CH_WHEEL_FR = 1;
const uint8_t CH_WHEEL_RL = 2;
const uint8_t CH_WHEEL_RR = 3;
const uint8_t CH_BOX      = 4;
const uint8_t CH_PUSHER   = 5;
const uint8_t CH_ARM      = 6;
const uint8_t CH_SPARE    = 7;

Adafruit_PWMServoDriver pwm(0x40, Wire);

// เซอร์โวหมุนต่อเนื่อง (360): จุดหยุดของแต่ละตัวไม่เท่ากัน ปรับค่าในอาร์เรย์นี้ได้
uint16_t stopUs[8] = {1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500};
const bool CONT_STOP_RELEASE = true;  // true = ตอนหยุดเลิกส่งพัลส์ (เซอร์โว 360 ไม่คืบเอง)
const int SPEED_SPAN_US = 500;        // ความเร็ว 100% = จุดหยุด +/- 500us

// ช่วงพัลส์ของเซอร์โวแบบกำหนดมุม MG90S
const uint16_t SERVO_MIN_US = 500;
const uint16_t SERVO_MAX_US = 2400;

// ล้อ: ฝั่งขวาติดตั้งกลับด้าน จึงต้องกลับทิศ
const int  WHEEL_L_DIR = +1;
const int  WHEEL_R_DIR = -1;
const bool DRIVE_REAR_WHEELS = false; // true = ล้อหลังซ้าย/ขวาหมุนตามล้อหน้า
int  wheelSpeed   = 100;              // % (0..100)
uint32_t driveRunMs = 1000;           // เวลาวิ่งต่อหนึ่งคำสั่ง
uint32_t driveUntil = 0;
bool driving = false;
bool drivingForward = false;

// กล่องรับยา
enum BoxState { BOX_CLOSED, BOX_OPENING, BOX_OPEN, BOX_CLOSING };
BoxState boxState = BOX_CLOSED;
const int BOX_OPEN_DIR = +1;          // เปลี่ยนเป็น -1 ถ้ากล่องวิ่งผิดทิศ
const int BOX_SPEED    = 100;         // %
const uint32_t boxTravelMs = 1500;    // เวลาที่เลื่อนออก/เข้าจนสุด
const uint32_t boxAlarmMs  = 10UL * 60UL * 1000UL;   // เตือนเมื่อเปิดค้าง 10 นาที
uint32_t boxMoveUntil  = 0;
uint32_t boxOpenedAt   = 0;
bool boxAlarmActive    = false;

// ที่ดันยา (MG90S)
const bool DISPENSE_ON_TOUCH = true;  // true = แตะตอนกล่องปิด ให้ปล่อยยาก่อนเลื่อนกล่องออก
const int pusherHomeAngle = 20;
const int pusherPushAngle = 100;
const uint32_t pusherHoldMs = 600;
enum PusherState { PUSH_IDLE, PUSH_PUSHING };
PusherState pusherState = PUSH_IDLE;
uint32_t pusherUntil = 0;
bool openAfterPush = false;

// แขน (MG90S)
const int armHomeAngle = 90;
int armAngle = 90;
const int ARM_WAVE_SEQ[] = {60, 120, 60, 120, 60, 90};
const int ARM_WAVE_LEN = sizeof(ARM_WAVE_SEQ) / sizeof(ARM_WAVE_SEQ[0]);
int armSeqIdx = -1;
uint32_t armSeqNext = 0;

// ============================ เซ็นเซอร์ระยะ =========================
const uint8_t TOF_SENSOR_INDEX = 0;   // เริ่มสแกนจาก XSHUT ตัวไหน (0 = XSHUT_1)
const int obstacleMm = 200;           // หยุดเมื่อใกล้กว่านี้ ขณะเดินหน้า
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
const bool FAN_ON_AT_BOOT = true;     // พัดลมระบายความร้อนทำงานตั้งแต่เปิดเครื่อง

// ============================ จอ LED =======================
// แผง WS2812B 16x16 เล่น GIF Mochi จาก mochi_frames.h
const uint16_t PANEL_W = 16;
const uint16_t PANEL_H = 16;
const uint16_t LED_COUNT_FRONT = PANEL_W * PANEL_H;
const uint16_t LED_COUNT_BACK  = 0;    // ตั้งมากกว่า 0 ถ้าต่อแถบไฟด้านหลัง
uint8_t ledBrightness = 25;            // 0..255 (ตั้งต่ำไว้ แผงกินกระแส 5V สูง) Pi ปรับได้

// รูปแบบการเดินสายในแผง (ค่าเดียวกับสเก็ตช์ Mochi ต้นฉบับ)
const bool SERPENTINE   = true;
const bool START_BOTTOM = false;
const bool START_RIGHT  = false;
const bool COLUMN_MAJOR = false;

Adafruit_NeoPixel ledFront(LED_COUNT_FRONT, PIN_RGB_FRONT, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ledBack (LED_COUNT_BACK ? LED_COUNT_BACK : 1, PIN_RGB_BACK, NEO_GRB + NEO_KHZ800);

int      ledAnim      = -1;     // ลำดับใน MOCHI_ANIMS, -1 = ปิดจอ
uint16_t ledFrame     = 0;
uint16_t ledLoopsLeft = 0;      // 0 = เล่นวนไม่หยุด
uint32_t ledNextAt    = 0;
int      ledIdleAnim  = 0;      // เล่นเมื่ออนิเมชันแบบกำหนดรอบจบ
int      ledAlertAnim = 0;      // เล่นระหว่างเตือนกล่องเปิดค้าง
void ledPlay(int idx, uint16_t loops);

// ============================ ทัช ============================
struct Touch {
  int pin;
  bool stable;
  bool lastRaw;
  uint32_t changedAt;
};
Touch touchBox    = {PIN_TOUCH_BOX,    false, false, 0};
Touch touchScreen = {PIN_TOUCH_SCREEN, false, false, 0};

const uint32_t STATUS_LOG_MS = 5000;
uint32_t lastStatus = 0;

// ============================ ลิงก์ Raspberry Pi ============================
const uint32_t PI_LINK_TIMEOUT_MS = 6000;   // Pi ส่ง ping ทุก 2 วิ เงียบเกินนี้ = หลุด
const uint32_t PI_MOVE_TIMEOUT_MS = 10000;  // ล้อที่ Pi สั่งหยุดเองถ้าไม่ได้รับ stop
const uint32_t STATUS_EVERY_MS = 10000;
uint32_t lastPiAt = 0;
uint32_t lastStatusAt = 0;
bool piDriving = false;
enum DispStage { DS_IDLE, DS_PUSHING, DS_OPENING };
DispStage dispStage = DS_IDLE;
String dispId;
int dispSlot = 0;
char boxBy[8] = "touch";        // ใครสั่งกล่องครั้งนี้: touch / pi / alarm
const char* piVia = "usb";      // สายที่ Pi ส่งคำสั่งมาล่าสุด
bool piLinked() { return lastPiAt && (millis() - lastPiAt) < PI_LINK_TIMEOUT_MS; }
void sendBox(const char* state);
void sendObstacle();

// ============================================================
//                       ฟังก์ชันพื้นฐาน
// ============================================================
void servoUs(uint8_t ch, uint16_t us) { pwm.writeMicroseconds(ch, us); }

void contRun(uint8_t ch, int speedPct) {          // -100..100
  speedPct = constrain(speedPct, -100, 100);
  servoUs(ch, stopUs[ch] + (speedPct * SPEED_SPAN_US) / 100);
}

void contStop(uint8_t ch) {
  if (CONT_STOP_RELEASE) pwm.setPin(ch, 0);       // ปิดสัญญาณช่องนี้
  else servoUs(ch, stopUs[ch]);
}

void servoAngle(uint8_t ch, int angle) {          // 0..180
  angle = constrain(angle, 0, 180);
  servoUs(ch, map(angle, 0, 180, SERVO_MIN_US, SERVO_MAX_US));
}

bool touchPressedEdge(Touch& t) {                 // คืน true หนึ่งครั้งต่อการแตะ
  bool raw = (digitalRead(t.pin) == TOUCH_ACTIVE);
  uint32_t now = millis();
  if (raw != t.lastRaw) { t.lastRaw = raw; t.changedAt = now; }
  if ((now - t.changedAt) > 40 && raw != t.stable) {
    t.stable = raw;
    if (raw) return true;
  }
  return false;
}

// ============================================================
//                          ล้อ (รอตัวสั่งจาก WiFi / Pi)
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
}

void wheelsMove(int l, int r, bool forward) {
  if (forward && distMm < obstacleMm) {
    LOGF("ไม่ออกตัว: มีสิ่งกีดขวางที่ %d mm (ระยะหยุด %d mm)\n", distMm, obstacleMm);
    buzzMode = BUZ_DOUBLE; buzzStart = millis();
    sendObstacle();
    return;
  }
  wheelsSet(l, r);
  driving = true;
  drivingForward = forward;
  driveUntil = millis() + driveRunMs;
}

void robotForward()  { wheelsMove(wheelSpeed, wheelSpeed, true); }
void robotBackward() { wheelsMove(-wheelSpeed, -wheelSpeed, false); }
void robotLeft()     { wheelsMove(-wheelSpeed, wheelSpeed, false); }
void robotRight()    { wheelsMove(wheelSpeed, -wheelSpeed, false); }

void wheelsUpdate() {
  if (!driving) return;
  if (drivingForward && distMm < obstacleMm) {
    wheelsStop();
    buzzMode = BUZ_DOUBLE; buzzStart = millis();
    sendObstacle();
    LOGF("เจอสิ่งกีดขวางที่ %d mm -> หยุด\n", distMm);
    return;
  }
  if ((int32_t)(millis() - driveUntil) >= 0) wheelsStop();
}

// ============================================================
//                        กล่องรับยา
// ============================================================
void boxStart(bool open) {
  if (open && (boxState == BOX_OPEN || boxState == BOX_OPENING)) return;
  if (!open && (boxState == BOX_CLOSED || boxState == BOX_CLOSING)) return;
  int dir = open ? BOX_OPEN_DIR : -BOX_OPEN_DIR;
  contRun(CH_BOX, dir * BOX_SPEED);
  boxState = open ? BOX_OPENING : BOX_CLOSING;
  boxMoveUntil = millis() + boxTravelMs;
  LOGLN(open ? "กล่อง: กำลังเปิด" : "กล่อง: กำลังปิด");
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
      LOGLN("กล่อง: เปิดแล้ว");
      sendBox("open");
    } else {
      boxState = BOX_CLOSED;
      boxAlarmActive = false;
      if (buzzMode == BUZ_ALARM) buzzMode = BUZ_OFF;
      if (ledAnim == ledAlertAnim) ledPlay(ledIdleAnim, 0);
      LOGLN("กล่อง: ปิดแล้ว");
      sendBox("closed");
    }
  }
  if (boxState == BOX_OPEN && !boxAlarmActive && (now - boxOpenedAt) >= boxAlarmMs) {
    boxAlarmActive = true;
    buzzMode = BUZ_ALARM; buzzStart = now;
    ledPlay(ledAlertAnim, 0);
    LOGLN("กล่องเปิดค้างนานเกินกำหนด -> แจ้งเตือน");
  }
  if (boxState == BOX_OPENING || boxState == BOX_CLOSING) boxAlarmActive = false;
}

// ============================================================
//                       ที่ดันยา
// ============================================================
void pusherPush() {
  servoAngle(CH_PUSHER, pusherPushAngle);
  pusherState = PUSH_PUSHING;
  pusherUntil = millis() + pusherHoldMs;
  LOGLN("ที่ดันยา: ดัน");
}

void pusherUpdate() {
  if (pusherState == PUSH_PUSHING && (int32_t)(millis() - pusherUntil) >= 0) {
    servoAngle(CH_PUSHER, pusherHomeAngle);
    pusherState = PUSH_IDLE;
    LOGLN("ที่ดันยา: กลับตำแหน่งเดิม");
    if (openAfterPush) {                      // ยาตกลงกล่องแล้ว -> เลื่อนกล่องออก
      openAfterPush = false;
      boxStart(true);
    }
  }
}

// ============================================================
//                            แขน
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
//                        BUZZER / พัดลม
// ============================================================
void buzzerUpdate() {
  uint32_t t = millis() - buzzStart;
  bool on = false;
  switch (buzzMode) {
    case BUZ_OFF:    on = false; break;
    case BUZ_BEEP:   on = (t < 150); if (t >= 150) buzzMode = BUZ_OFF; break;
    case BUZ_DOUBLE: on = (t < 120) || (t >= 240 && t < 360); if (t >= 360) buzzMode = BUZ_OFF; break;
    case BUZ_ALARM:  on = ((t % 800) < 400); break;
  }
  digitalWrite(PIN_BUZZER, on ? HIGH : LOW);
}

void fanSet(bool on) {
  fanOn = on;
  digitalWrite(PIN_FAN, on ? HIGH : LOW);
}

// ============================================================
//                        จอ LED
// ============================================================
int findAnim(const char* name) {
  for (int i = 0; i < MOCHI_ANIM_COUNT; i++)
    if (strcmp(MOCHI_ANIMS[i].name, name) == 0) return i;
  return -1;
}

int panelXY(int x, int y) {
  if (START_RIGHT)  x = PANEL_W - 1 - x;
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
    LOGLN("จอ LED: ปิด");
  } else {
    LOGF("จอ LED: %d %s\n", idx, MOCHI_ANIMS[idx].name);
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

void ledNext() {                                   // ข้าม "intro"
  int n = (ledAnim < 0) ? 1 : (ledAnim + 1) % MOCHI_ANIM_COUNT;
  if (n == 0) n = 1;
  ledPlay(n, 0);
}

// ============================================================
//                       เซ็นเซอร์ระยะ
// ============================================================
void tofInit() {
  for (int i = 0; i < 4; i++) {            // กดรีเซ็ตเซ็นเซอร์ทุกตัวไว้ก่อน
    pinMode(PIN_XSHUT[i], OUTPUT);
    digitalWrite(PIN_XSHUT[i], LOW);
  }
#if USE_TOF_SENSOR
  // ลองปลุกเซ็นเซอร์ทีละขา XSHUT (เริ่มจาก TOF_SENSOR_INDEX) เผื่อเสียบสายผิดตำแหน่งบน U30
  for (int n = 0; n < 4 && !tofOk; n++) {
    int idx = (TOF_SENSOR_INDEX + n) % 4;
    for (int i = 0; i < 4; i++) { pinMode(PIN_XSHUT[i], OUTPUT); digitalWrite(PIN_XSHUT[i], LOW); }
    delay(10);
    pinMode(PIN_XSHUT[idx], INPUT);        // ปล่อยขา -> pull-up บนโมดูลปลุกเซ็นเซอร์
    delay(20);
    tofOk = lox.begin(0x29, false, &Wire);
    if (tofOk) LOGF("เซ็นเซอร์ระยะ: พร้อมใช้งาน (พบที่ XSHUT_%d = GPIO%d)\n", idx + 1, PIN_XSHUT[idx]);
  }
  if (!tofOk) {
    for (int i = 0; i < 4; i++) { pinMode(PIN_XSHUT[i], OUTPUT); digitalWrite(PIN_XSHUT[i], LOW); }
    LOGLN("เซ็นเซอร์ระยะ: ไม่พบ! ล้อจะไม่หยุดเองเมื่อเจอสิ่งกีดขวาง (เช็กสาย I2C / XSHUT / ไฟ 3.3V)");
  }
#else
  LOGLN("เซ็นเซอร์ระยะ: ปิดใช้งาน (USE_TOF_SENSOR=0)");
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
  distMm = (m.RangeStatus != 4) ? (int)m.RangeMilliMeter : 9999;   // 4 = เกินระยะวัด
#endif
}

// ============================================================
//                    ทัช + สถานะ
// ============================================================
void touchUpdate() {
  bool t1 = touchPressedEdge(touchBox);
  bool t2 = touchPressedEdge(touchScreen);

  if (t1) {
    LOGLN("แตะเซ็นเซอร์ 1: กล่องรับยา");
    buzzMode = BUZ_BEEP; buzzStart = millis();
    if (pusherState == PUSH_PUSHING) {
      // กำลังปล่อยยา รอให้เสร็จก่อน ไม่ทำอะไรเมื่อแตะซ้ำ
    } else if (boxState == BOX_CLOSED) {
      strcpy(boxBy, "touch");
      ledPlay(findAnim("happy"), 2);
      // ต่อกับ Pi อยู่: Pi เป็นคนจ่ายยาตามเวลา แตะแค่เปิดกล่อง (กันยาออกเกินขนาด)
      if (DISPENSE_ON_TOUCH && !piLinked()) { openAfterPush = true; pusherPush(); }
      else boxStart(true);
    } else if (boxState == BOX_OPEN) {
      strcpy(boxBy, "touch");     // แตะปิดหลัง Pi จ่ายยา = กินยาแล้ว
      boxStart(false);
    }
  }
  if (t2) {
    if (piLinked()) {             // ต่อกับ Pi: ทัช 2 = ปุ่มคุยกับ Pebbi
      LOGLN("แตะเซ็นเซอร์ 2: คุยกับ Pebbi");
      PiOut.println("{\"evt\":\"button\",\"press\":\"short\",\"by\":\"touch2\"}");
      buzzMode = BUZ_BEEP; buzzStart = millis();
    } else {
      LOGLN("แตะเซ็นเซอร์ 2: เปลี่ยนหน้าจอ");
      ledNext();
    }
  }
}

void statusUpdate() {
  if (!DEBUG_SERIAL) return;
  uint32_t now = millis();
  if (now - lastStatus < STATUS_LOG_MS) return;
  lastStatus = now;
  const char* bs[] = {"ปิด", "กำลังเปิด", "เปิด", "กำลังปิด"};
  int pinMv = (int)analogReadMilliVolts(PIN_BATT_SENSE);
  Serial.printf("[สถานะ] แบต %.2f V | ระยะ %d mm | กล่อง %s | พัดลม %s | จอ %s\n",
                pinMv * BATT_DIV / 1000.0f, distMm, bs[boxState], fanOn ? "เปิด" : "ปิด",
                ledAnim < 0 ? "ปิด" : MOCHI_ANIMS[ledAnim].name);
}

// ============================================================
//              หน้าจอตามอารมณ์ / เช็กอินความรู้สึกจาก Pi
// ============================================================
// อารมณ์ของหุ่นที่ Pi ส่งมา -> อนิเมชัน Mochi (ใช้ชื่อแรกที่มีใน mochi_frames.h)
struct MoodFace { const char* mood; const char* anims[3]; };
const MoodFace MOOD_FACES[] = {
  {"idle",      {"content", nullptr}},
  {"happy",     {"happy", "happy_2", nullptr}},
  {"listening", {"content", nullptr}},
  {"thinking",  {"confused_2", "content", nullptr}},
  {"speaking",  {"happy_2", "happy", nullptr}},
  {"concerned", {"embarrassed", "content", nullptr}},
  {"alert",     {"excited_2", "determined", nullptr}},
  {"pairing",   {"intro", "content", nullptr}},
  {"offline",   {"sleepy_3", "sleepy", nullptr}},
  {"sleepy",    {"sleepy", "sleepy_3", nullptr}},
  {"boot",      {"intro", "content", nullptr}},
  {"music",     {"music", "relaxed", nullptr}},
};

void faceForMood(const String& mood) {
  if (boxAlarmActive) return;   // ระหว่างเตือนกล่องเปิดค้าง ให้หน้าเตือนค้างไว้
  for (const MoodFace& m : MOOD_FACES) {
    if (mood != m.mood) continue;
    for (int i = 0; i < 3 && m.anims[i]; i++) {
      int idx = findAnim(m.anims[i]);
      if (idx >= 0) { if (idx != ledAnim) ledPlay(idx, 0); return; }
    }
    return;
  }
}

// เช็กอินความรู้สึกบนจอ Pi (1 แย่มาก ... 7 ดีมาก) -> อนิเมชัน Mochi ของทีม
// แก้ชื่อได้ตามใจ (ต้องตรงกับชื่อใน mochi_frames.h)
const char* CHECKIN_ANIMS[7] = {
  "frustrated",   // 1 😭 แย่มาก
  "embarrassed",  // 2 🥹 เศร้า
  "confused_2",   // 3 😢 ไม่ค่อยดี
  "content",      // 4 😌 เฉย ๆ
  "relaxed",      // 5 ☺️ สงบ
  "happy",        // 6 😊 ดี
  "love",         // 7 🥰 ดีมาก
};

uint32_t animMs(int idx) {      // เวลาเล่นอนิเมชันนี้ 1 รอบ
  uint32_t t = 0;
  for (uint16_t f = 0; f < MOCHI_ANIMS[idx].count; f++) t += MOCHI_DELAY[MOCHI_ANIMS[idx].start + f];
  return t ? t : 1;
}

void showCheckin(int score, uint32_t holdMs) {
  if (score < 1 || score > 7 || boxAlarmActive) return;
  int idx = findAnim(CHECKIN_ANIMS[score - 1]);
  if (idx < 0) idx = ledIdleAnim;
  uint16_t loops = max<uint32_t>(1, (holdMs + animMs(idx) / 2) / animMs(idx));
  ledPlay(idx, loops);          // เล่นครบรอบแล้วกลับหน้าปกติเอง
  LOGF("เช็กอินความรู้สึก %d -> %s x%u\n", score, MOCHI_ANIMS[idx].name, loops);
}

// ============================================================
//        ลิงก์กับ Raspberry Pi: JSON ทีละบรรทัด (ไม่ต้องใช้ไลบรารีเพิ่ม)
// ============================================================
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

int batteryMv() { return (int)(analogReadMilliVolts(PIN_BATT_SENSE) * BATT_DIV); }

int batteryPercent() {
  int mv = batteryMv();
  return constrain(map(mv, BATT_EMPTY_MV, BATT_FULL_MV, 0, 100), 0, 100);
}

void sendObstacle() {
  if (piLinked()) PiOut.printf("{\"evt\":\"obstacle\",\"mm\":%d}\n", distMm);
}

void sendBox(const char* state) {
  if (piLinked()) PiOut.printf("{\"evt\":\"box\",\"state\":\"%s\",\"by\":\"%s\"}\n", state, boxBy);
  if (!strcmp(state, "open") || !strcmp(state, "closed")) strcpy(boxBy, "touch");
}

void sendStatus() {
  const char* bs[] = {"closed", "opening", "open", "closing"};
  int mv = batteryMv();
  PiOut.print("{\"evt\":\"status\",");
  if (mv > 500) PiOut.printf("\"battery\":%d,\"battMv\":%d,", batteryPercent(), mv);
  PiOut.printf("\"charging\":false,\"box\":\"%s\",\"dist\":%d,\"tof\":%s,\"fan\":%s,\"anim\":\"%s\"}\n",
               bs[boxState], distMm, tofOk ? "true" : "false", fanOn ? "true" : "false",
               ledAnim < 0 ? "off" : MOCHI_ANIMS[ledAnim].name);
}

void sendHello() {
  PiOut.printf("{\"evt\":\"hello\",\"fw\":\"%s\",\"board\":\"esp32s3-medbot\",\"slots\":1,\"mochi\":true,"
               "\"caps\":[\"wheels\",\"box\",\"pusher\",\"arm\",\"face\",\"emoji\",\"buzzer\",\"fan\",\"tof\"],\"link\":\"%s\"}\n",
               PEBBLE_FW, piVia);
}

void sendDispensed(bool ok, const char* why) {
  PiOut.printf("{\"evt\":\"dispensed\",\"slot\":%d,\"id\":\"%s\",\"ok\":%s%s%s%s}\n", dispSlot, dispId.c_str(),
               ok ? "true" : "false", why[0] ? ",\"msg\":\"" : "", why, why[0] ? "\"" : "");
}

void stopAll() {
  wheelsStop();
  piDriving = false;
  contStop(CH_BOX);
  if (boxState == BOX_OPENING) boxState = BOX_OPEN;
  if (boxState == BOX_CLOSING) boxState = BOX_CLOSED;
  buzzMode = BUZ_OFF;
  armSeqIdx = -1;
  LOGLN("หยุดทุกอย่าง");
}

void piMove(const String& dir, int speed) {
  speed = constrain(speed, 0, 100);
  if (dir == "stop") { wheelsStop(); piDriving = false; return; }
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
  if (dispStage != DS_IDLE || pusherState != PUSH_IDLE) { sendDispensed(false, "busy"); return; }
  if (boxState != BOX_CLOSED) { sendDispensed(false, "box not closed"); return; }
  openAfterPush = false;
  pusherPush();
  int ex = findAnim("excited_2");
  if (ex >= 0) ledPlay(ex, 1);
  buzzMode = BUZ_DOUBLE; buzzStart = millis();
  dispStage = DS_PUSHING;
}

void dispenseUpdate() {
  if (dispStage == DS_PUSHING && pusherState == PUSH_IDLE) {
    strcpy(boxBy, "pi");
    boxStart(true);             // ยาตกลงกล่องแล้ว -> เลื่อนกล่องออกให้หยิบ
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
  if (cmd == "ping") { PiOut.println("{\"evt\":\"pong\"}"); }
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
  else if (cmd == "emoji") { showCheckin(jInt(s, "score", 0), jInt(s, "ms", 6000)); }
  else if (cmd == "brightness") { ledBrightness = constrain(jInt(s, "value", ledBrightness), 0, 255); ledFront.setBrightness(ledBrightness); }
  else if (cmd == "buzz" || cmd == "beep") {
    String m = jStr(s, "mode", "beep");
    buzzMode = m == "double" ? BUZ_DOUBLE : m == "alarm" ? BUZ_ALARM : m == "off" ? BUZ_OFF : BUZ_BEEP;
    buzzStart = millis();
  }
  else if (cmd == "fan") { fanSet(jStr(s, "on", "true") == "true"); }
  else if (cmd == "stop") { stopAll(); }
  else if (cmd == "head") { /* หุ่นรุ่นนี้ไม่มีหัวหมุน */ }
  else { PiOut.printf("{\"evt\":\"error\",\"msg\":\"unknown cmd %s\"}\n", cmd.c_str()); }
}

void piUpdate() {
  uint32_t now = millis();
  // ความปลอดภัย: Pi เงียบ (สายหลุด / Pi ค้าง) ระหว่างที่ Pi สั่งล้ออยู่ -> หยุด
  if (piDriving && lastPiAt && now - lastPiAt > PI_LINK_TIMEOUT_MS) {
    wheelsStop();
    piDriving = false;
    PiOut.println("{\"evt\":\"error\",\"msg\":\"lost the Pi: stopped\"}");
  }
  if (piDriving && !driving) piDriving = false;
  if (piLinked() && now - lastStatusAt > STATUS_EVERY_MS) {
    lastStatusAt = now;
    sendStatus();
  }
}

// อ่านบรรทัดจาก USB และสาย RX/TX: บรรทัดที่ขึ้นต้นด้วย { = คำสั่งจาก Pi
struct LineIn { Stream* port; String buf; const char* via; };
LineIn usbIn = {&Serial, "", "usb"};
#if PI_UART
LineIn uartIn = {&Serial1, "", "uart"};
#endif

void readLines(LineIn& in) {
  while (in.port->available()) {
    char c = in.port->read();
    if (c == '\r') continue;
    if (c == '\n') {
      in.buf.trim();
      if (in.buf.startsWith("{")) { piVia = in.via; handlePi(in.buf); }
      in.buf = "";
    } else if (in.buf.length() < 300) {
      in.buf += c;
    } else {
      in.buf = "";              // สัญญาณรบกวน / บรรทัดยาวผิดปกติ
    }
  }
}

// ============================================================
//                         SETUP / LOOP
// ============================================================
void setup() {
  Serial.begin(115200);         // USB: บันทึก + คุยกับ Pi ได้ด้วย
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 1000) delay(10);
#if PI_UART
  Serial1.setRxBufferSize(1024);  // ต้องตั้งก่อน begin
  Serial1.begin(PI_UART_BAUD, SERIAL_8N1, PIN_PI_RX, PIN_PI_TX);
#endif

  pinMode(PIN_BUZZER, OUTPUT); digitalWrite(PIN_BUZZER, LOW);
  pinMode(PIN_FAN, OUTPUT);    fanSet(FAN_ON_AT_BOOT);
  pinMode(PIN_TOUCH_BOX, INPUT);
  pinMode(PIN_TOUCH_SCREEN, INPUT);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(400000);

  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(50);
  delay(10);
  for (uint8_t ch = 0; ch <= CH_BOX; ch++) contStop(ch);   // เซอร์โว 360 ทุกตัวหยุด
  servoAngle(CH_PUSHER, pusherHomeAngle);                  // เซอร์โวมุมไปท่าเริ่มต้นตรง ๆ ไม่ให้กระตุก
  servoAngle(CH_ARM, armHomeAngle);

  ledFront.begin();
  ledFront.setBrightness(ledBrightness);
  ledFront.clear();
  ledFront.show();
  if (LED_COUNT_BACK) { ledBack.begin(); ledBack.clear(); ledBack.show(); }
  ledIdleAnim  = max(findAnim("content"), 0);
  ledAlertAnim = max(findAnim("angry"), 0);
  ledPlay(max(findAnim("intro"), 0), 1);      // เล่น intro หนึ่งรอบ แล้วเข้าท่าปกติ

  tofInit();

  LOGLN("MedBot พร้อมใช้งาน (โหมดอัตโนมัติ ไม่มีเมนู) · ต่อ Raspberry Pi ทาง RX/TX (GPIO43/44) หรือ USB ได้");
}

void loop() {
  readLines(usbIn);
#if PI_UART
  readLines(uartIn);
#endif
  tofUpdate();
  touchUpdate();
  wheelsUpdate();
  boxUpdate();
  pusherUpdate();
  armUpdate();
  buzzerUpdate();
  ledUpdate();
  dispenseUpdate();
  piUpdate();
  statusUpdate();               // บันทึกภาษาไทยออกแค่สาย USB (Pi อ่านเฉพาะบรรทัด JSON)
}
