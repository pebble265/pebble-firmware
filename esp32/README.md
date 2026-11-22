# pebble(a) · ESP32-S3 MedBot (medbot-3.0.0)

โค้ดของทีม (เมนูทดสอบผ่าน Serial) + ลิงก์คุยกับ Raspberry Pi 5 ทางสาย RX/TX (หรือ USB)
รูปแบบข้อความดูที่ [`PROTOCOL.md`](PROTOCOL.md)

## ฮาร์ดแวร์

| ส่วน | ต่อที่ |
|---|---|
| บอร์ด | ESP32-S3-WROOM-1 (N16R8) |
| ล้อ 4 ตัว (เซอร์โว 360) | PCA9685 ช่อง 0–3 |
| กล่องรับยาเลื่อนออก (MG996R 360) | PCA9685 ช่อง 4 |
| ที่ดันยา (MG90S) | PCA9685 ช่อง 5 |
| แขน (MG90S) | PCA9685 ช่อง 6 |
| PCA9685 / เซ็นเซอร์ระยะ VL53L0X | I2C GPIO8 (SDA) / GPIO9 (SCL) · XSHUT_1 = GPIO10 |
| ทัช 1 (กล่องยา) / ทัช 2 (คุย / เปลี่ยนหน้า) | GPIO4 / GPIO5 (TTP223) |
| แผง LED 16x16 (หน้า Mochi) / ไฟหลัง | GPIO38 / GPIO39 |
| Buzzer / พัดลม | GPIO40 / GPIO41 |
| วัดแบต | GPIO1 (ตั้ง `BATT_DIVIDER`, `BATT_EMPTY_MV`, `BATT_FULL_MV` ให้ตรงแบตจริง) |
| Raspberry Pi (หลัก) | สาย RX/TX: Pi ขา 8 → GPIO44, Pi ขา 10 ← GPIO43, GND ↔ GND (เปลี่ยนขาได้ที่ `PIN_PI_RX/PIN_PI_TX`) |
| Raspberry Pi (สำรอง) | สาย USB-C ช่อง USB ของ ESP32-S3 (GPIO19/20) |

## อัปโหลด (Arduino IDE 2.x)

1. เปิด `pebble_medbot/pebble_medbot.ino`
2. **คัดลอก `mochi_frames.h` ของทีม** มาไว้ในโฟลเดอร์ `pebble_medbot/` (ถ้าไม่มี จะ compile ได้แต่หน้าบนแผง LED เป็นตาสำรองแบบง่าย)
3. Library Manager: `Adafruit PWM Servo Driver Library`, `Adafruit NeoPixel`, `Adafruit VL53L0X`
4. Tools: Board `ESP32S3 Dev Module` · USB CDC On Boot **Enabled** · USB Mode `Hardware CDC and JTAG` · PSRAM `OPI PSRAM` · Flash `16MB`
5. Upload แล้วเปิด Serial Monitor (115200, Newline) จะเห็นเมนู MEDBOT และบรรทัด `{"evt":"hello",...}`

ทดสอบคำสั่งแบบที่ Pi ส่งได้ใน Serial Monitor เช่น `{"cmd":"arm","do":"wave"}` หรือ `{"cmd":"dispense","slot":1,"id":"test"}`

เสร็จแล้วถอดสายจากคอม ต่อสาย RX/TX 3 เส้นเข้า Raspberry Pi (ตารางด้านบน) แอปบน Pi จะหาเอง (`/dev/serial0` ก่อน แล้วค่อยสาย USB)
`install.sh` บน Pi เปิดพอร์ต UART ให้แล้ว (ต้อง reboot ครั้งแรก)

โค้ดนี้คือ **โค้ดทีมเวอร์ชันอัตโนมัติ** (ไม่มีเมนู) + ลิงก์คุยกับ Pi · ไม่ต่อ Pi ก็ทำงานเองเหมือนเดิมทุกอย่าง
ต่อ Pi อยู่: แตะทัช 1 ตอนกล่องปิด = เปิดกล่องเฉย ๆ (Pi เป็นคนจ่ายยาตามเวลา กันยาออกเกิน) · ทัช 2 = ปุ่มคุยกับ Pebbi
หน้าจอตอนต่อ Pi: หน้าปกติกระพริบตาอย่างเดียว · เช็กอินความรู้สึก (บนจอ Pi หรือในแอปมือถือ) = เล่นอนิเมชันตามอีโมจิ ~10 วิ แล้วกลับมากระพริบตา · กล่องเปิดค้างเกิน 10 นาทียังขึ้นหน้าเตือน (angry) เหมือนเดิม

ทดสอบเช็กอินความรู้สึกโดยไม่ใช้ Pi: Serial Monitor (115200, ส่งท้ายด้วย Newline) พิมพ์ `{"cmd":"emoji","score":7}`
อนิเมชันของแต่ละระดับแก้ได้ที่ `CHECKIN_ANIMS` ใน .ino

`mochi_frames.h` (อนิเมชัน Mochi 18 แบบของทีม) อยู่ในโฟลเดอร์เดียวกับ .ino แล้ว

## ปรับแต่ง

- ชื่ออนิเมชันของแต่ละอารมณ์: `MOOD_FACES` ใน `.ino` (ใช้ชื่อแรกที่มีใน `mochi_frames.h`)
- จุดหยุดเซอร์โว 360, มุมที่ดันยา, เวลาเลื่อนกล่อง: ปรับในเมนูทดสอบ แล้วจดค่าไปแก้ในโค้ด
- ระยะหยุดเมื่อเจอสิ่งกีดขวาง: `obstacleMm` (ค่าเริ่มต้น 200 มม.)
