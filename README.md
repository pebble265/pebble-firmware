# pebble(a) · Robot firmware

ซอฟต์แวร์ในตัวหุ่น **pebble(a)** หุ่นเพื่อนคู่ใจสำหรับผู้ที่มีภาวะซึมเศร้า ทำงานคู่กับแอป Pebble
(repo: [`pebble265/pebble`](https://github.com/pebble265/pebble))

หุ่นมีคอมพิวเตอร์ 2 ตัว:

```
                    Wi-Fi                               USB serial (JSON ทีละบรรทัด)
 backend Pebble  ◄────────►  Raspberry Pi 5  ◄──────────────────────────────►  ESP32
 (API + Socket.IO)            "สมอง"                                          "ร่างกาย"
 ThingsBoard     ◄── MQTT ──  · หน้าจอใบหน้า (ตา/อารมณ์)                       · มอเตอร์ล้อ 2 ข้าง
 แอปมือถือ       ◄── BLE ───  · ไมค์ + ลำโพง คุยกับ Pebbi                      · servo จ่ายยา + หัว
                              · กล้อง (รู้ว่ามีคนอยู่ ตามมอง)                  · ไฟ LED วงแหวน
                              · ตารางยา จับคู่ heartbeat                        · ปุ่มบนหัว buzzer วัดแบต
```

| โฟลเดอร์ | ภาษา | ทำหน้าที่ |
|---|---|---|
| [`raspberry-pi/`](raspberry-pi/) | Python 3.11 | แอปหลักบน Raspberry Pi 5 ต่อ backend, หน้าจอ, เสียง, กล้อง, Bluetooth, ThingsBoard แล้วสั่ง ESP32 |
| [`esp32/`](esp32/) | C++ (Arduino IDE, ESP32-S3) | MedBot ของทีม: คุมฮาร์ดแวร์ตามคำสั่งจาก Pi แล้วรายงานทัช / กล่องยา / แบตกลับ (มีเมนูทดสอบผ่าน Serial ด้วย) |
| [`esp32/PROTOCOL.md`](esp32/PROTOCOL.md) | — | รูปแบบข้อความระหว่าง Pi กับ ESP32 |

## เริ่มต้นเร็ว

1. ลงทะเบียนหุ่นใน backend (ครั้งเดียวต่อหุ่น) ใน repo แอป `apps/api`:
   `npm run build && node --env-file=.env dist/scripts/provision-device.js 4` → ได้ serial + secret
2. อัปโหลดโค้ด ESP32-S3 ด้วย Arduino IDE: ดู [`esp32/README.md`](esp32/README.md) แล้วต่อ ESP32 เข้า Pi ด้วยสาย USB
3. ติดตั้งบน Pi: ดู [`raspberry-pi/README.md`](raspberry-pi/README.md)
4. เปิดแอป Pebble → เพิ่มหุ่น → แอปเจอหุ่นทาง Bluetooth (หรือพิมพ์รหัส 6 หลักที่ขึ้นบนจอหุ่น)

## ใช้งาน

- **ถึงเวลายา:** หุ่นดันยาลงกล่อง เลื่อนกล่องออก จอขึ้นเตือนและพูดบอก → หยิบยาแล้ว**แตะเซ็นเซอร์ 1 ปิดกล่อง** = บันทึกว่ากินแล้ว (หรือแตะ "กินแล้ว" บนจอ)
- **แตะเซ็นเซอร์ 2** หรือปุ่มไมค์บนจอ = คุยกับ Pebbi
- **แอปมือถือ (แท็บ pebble → ควบคุมหุ่น):** เดินหน้า / ถอยหลัง / เลี้ยวซ้าย / เลี้ยวขวา (กดค้าง), โบกมือ, เปิด/ปิดกล่องยา

> เวอร์ชันก่อนหน้า (v1, ESP32 ตัวเดียวต่อ Wi-Fi เอง) ยังอยู่ในประวัติ git
