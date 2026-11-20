# pebble(a) · Robot firmware (ESP32)

เฟิร์มแวร์ของหุ่น **pebble(a)** หุ่นเพื่อนคู่ใจสำหรับผู้ที่มีภาวะซึมเศร้า ทำงานคู่กับ
แอป Pebble (repo: [`pebble265/pebble`](https://github.com/pebble265/pebble))

> สถานะ: **v1.0.0 — starter firmware** ครอบคลุมการเชื่อมต่อทั้งหมดกับระบบ (backend, แอป, ThingsBoard)
> ส่วนฮาร์ดแวร์ (ขา GPIO, มอเตอร์, ช่องจ่ายยา) ปรับได้ที่ `include/config.h` ตามบอร์ดจริง

## ทำอะไรได้บ้าง

| ฟีเจอร์ | รายละเอียด |
|---|---|
| ล็อกอินกับ backend | `POST /robot/auth` ด้วย serial + secret ได้ device token (ต่ออายุเองทุก 50 นาที) |
| จับคู่กับแอปผ่าน Bluetooth LE | ตอนยังไม่มีเจ้าของ: ขอรหัส 6 หลัก (`POST /robot/pairing-code`) แล้วประกาศ BLE service ตามโปรโตคอลเดียวกับแอป (`INFO`, `PAIR`) พอแอปจับคู่สำเร็จ ปิด BLE เอง |
| Heartbeat | ทุก 1 นาที ส่งแบตเตอรี่ สถานะชาร์จ และเวอร์ชันเฟิร์มแวร์ (`POST /robot/heartbeat`) |
| รับคำสั่งจากแอปแบบ realtime | Socket.IO `/realtime`: `wake` (ไฟกระพริบ + เสียง), `forward` / `backward` / `stop` (มอเตอร์ หยุดเองถ้าไม่ได้รับ stop ภายใน 10 วิ) |
| เตือนกินยา | ได้รับ `dose:due` (หรือเช็กเองทุก 5 นาทีจาก `GET /robot/doses/today`) → จ่ายยา ไฟกระพริบ ส่งเสียงทุก 30 วิ จนกดปุ่ม → `POST /robot/doses/:id/take` |
| ส่ง log เข้า ThingsBoard | MQTT ทุก 30 วิ: `battery`, `charging`, `paired`, `rssi`, `uptime`, `heap`, `realtime`, `doseWaiting` และเหตุการณ์ (`event`: boot, paired, cmd, dose_due, dose_taken) ดูได้ที่เว็บแอดมิน |

## ฮาร์ดแวร์ (ค่าเริ่มต้นใน `include/config.h`)

| ส่วน | ขา ESP32 |
|---|---|
| ปุ่มบนหัวหุ่น (ยืนยันกินยา) | GPIO 0 (active LOW) |
| ไฟสถานะ | GPIO 2 |
| Buzzer | GPIO 25 |
| วัดแบต (ผ่านตัวแบ่งแรงดัน 1:2) | GPIO 34 |
| สถานะชาร์จ | GPIO 35 |
| มอเตอร์ (H-bridge) | GPIO 26, 27 |
| ช่องจ่ายยา | GPIO 14 |

## วิธีใช้

1. ติดตั้ง [PlatformIO](https://platformio.org/) (VS Code extension หรือ `pip install platformio`)
2. ลงทะเบียนหุ่นใน backend (ทำครั้งเดียวต่อหุ่นหนึ่งตัว) ใน repo แอป `apps/api`:
   ```bash
   npm run build && node --env-file=.env dist/scripts/provision-device.js
   ```
   จะได้ `PEBBLE_SERIAL` และ `PEBBLE_SECRET`
3. คัดลอก `include/secrets.example.h` เป็น `include/secrets.h` แล้วใส่ Wi-Fi, URL ของ backend,
   serial/secret และ Access token ของอุปกรณ์ใน ThingsBoard (`secrets.h` ไม่ถูกอัปขึ้น git)
4. Build และ upload:
   ```bash
   pio run -t upload
   pio device monitor      # ดู log ทาง Serial (115200)
   ```
5. เปิดแอป Pebble → ตั้งค่าครั้งแรก → "Finding your pebble" แอปจะเจอหุ่นผ่าน Bluetooth

## โครงสร้าง

```
platformio.ini            บอร์ด ESP32 + ไลบรารี (ArduinoJson, PubSubClient, WebSockets)
include/config.h          ขา GPIO, ค่าแบต, ช่วงเวลา
include/secrets.example.h ตัวอย่างค่าลับ (คัดลอกเป็น secrets.h)
src/main.cpp              เฟิร์มแวร์ทั้งหมด
```

## หมายเหตุด้านความปลอดภัย

- ค่าลับทั้งหมดอยู่ใน `secrets.h` ซึ่ง git ไม่เก็บ
- ตอนพัฒนาใช้ `setInsecure()` กับ HTTPS (เพราะ URL ของ ngrok เปลี่ยนได้) ก่อนใช้งานจริงควรฝัง root CA ของเซิร์ฟเวอร์
- หุ่นไม่ได้เก็บหรือส่งชื่อยา ข้อความแชท หรือโน้ตของผู้ใช้
