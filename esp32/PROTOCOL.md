# Pi ⇄ ESP32 (MedBot) protocol

สาย RX/TX (UART) ระหว่าง Raspberry Pi 5 กับ ESP32-S3, 115200 baud 8N1 — Pi ใช้ `/dev/serial0`
หรือสาย USB (Pi เห็นเป็น `/dev/ttyACM0`) ก็ได้ ข้อความเหมือนกันทั้งสองทาง ESP32 ตอบออกทั้งสองสาย

| Raspberry Pi 5 | ESP32-S3 |
|---|---|
| ขา 8 (GPIO14 TX) | GPIO44 (RXD0) |
| ขา 10 (GPIO15 RX) | GPIO43 (TXD0) |
| ขา 6 (GND) | GND |

ไฟ 3.3 V ทั้งคู่ ต่อตรงได้ไม่ต้องมีตัวแปลงระดับ (ห้ามต่อ 5 V เข้าขา RX/TX)
**JSON หนึ่งออบเจกต์ต่อหนึ่งบรรทัด** (`\n`) บรรทัดที่ไม่ได้ขึ้นต้นด้วย `{` เป็น log/เมนูทดสอบ Pi จะไม่สนใจ
เมนูทดสอบแบบเดิม (พิมพ์ `1` `2` … ใน Serial Monitor) ยังใช้ได้เหมือนเดิม

## Pi → ESP32 (`cmd`)

| คำสั่ง | ตัวอย่าง | ผล |
|---|---|---|
| `hello` | `{"cmd":"hello"}` | ตอบ `hello` + `status` |
| `ping` | `{"cmd":"ping"}` | ตอบ `pong` (Pi ส่งทุก 2 วิ = keep-alive) |
| `status` | `{"cmd":"status"}` | ตอบ `status` |
| `move` | `{"cmd":"move","dir":"forward","speed":60}` | `dir`: `forward` `backward` `left` `right` `stop` · หยุดเองเมื่อเจอสิ่งกีดขวาง (VL53L0X) |
| `dispense` | `{"cmd":"dispense","slot":1,"id":"<doseId>"}` | ดันยาลงกล่อง (MED_2) → เลื่อนกล่องออก (MED_1) → ตอบ `dispensed` |
| `box` | `{"cmd":"box","do":"open"}` | `open` / `close` / `toggle` |
| `arm` | `{"cmd":"arm","do":"wave"}` | `wave` โบกมือ · `home` · `{"do":"angle","angle":120}` |
| `emoji` | `{"cmd":"emoji","score":7,"ms":6000}` | เช็กอินความรู้สึกบนจอ Pi → เล่นอนิเมชัน Mochi ของทีมประมาณ `ms` แล้วกลับหน้าปกติ: 1 😭 frustrated · 2 🥹 embarrassed · 3 😢 confused_2 · 4 😌 content · 5 ☺️ relaxed · 6 😊 happy · 7 🥰 love (แก้ได้ที่ `CHECKIN_ANIMS` ใน .ino) |
| `face` | `{"cmd":"face","mood":"happy"}` | หน้าบนแผง LED 16x16 ตามอารมณ์ (ดู `MOOD_FACES` ใน .ino) หรือ `{"anim":"excited_2","loops":2}` |
| `buzz` | `{"cmd":"buzz","mode":"double"}` | `beep` `double` `alarm` `off` |
| `fan` | `{"cmd":"fan","on":true}` | พัดลม |
| `brightness` | `{"cmd":"brightness","value":40}` | ความสว่างแผง LED 0–255 |
| `stop` | `{"cmd":"stop"}` | หยุดทุกอย่าง |

## ESP32 → Pi (`evt`)

| เหตุการณ์ | ตัวอย่าง | เมื่อไร |
|---|---|---|
| `hello` | `{"evt":"hello","fw":"medbot-3.0.0","board":"esp32s3-medbot","slots":1,"caps":[…]}` | เปิดเครื่อง / ถูกถาม |
| `status` | `{"evt":"status","battery":70,"battMv":3900,"box":"closed","dist":420,"fan":true,"anim":"content"}` | ทุก 10 วิ (ตอนต่อ Pi) |
| `box` | `{"evt":"box","state":"closing","by":"touch"}` | กล่องเลื่อน: `opening` `open` `closing` `closed` · `by`: `touch` `pi` `menu` `alarm` |
| `dispensed` | `{"evt":"dispensed","slot":1,"id":"…","ok":true}` | กล่องเลื่อนออกพร้อมยาแล้ว (`ok:false` + `msg` ถ้ากล่องไม่ได้ปิดอยู่ / เครื่องไม่ว่าง) |
| `button` | `{"evt":"button","press":"short","by":"touch2"}` | แตะเซ็นเซอร์ 2 ตอนต่อกับ Pi |
| `obstacle` | `{"evt":"obstacle","mm":150}` | มีสิ่งกีดขวางข้างหน้า ล้อหยุด |
| `pong` / `error` | `{"evt":"error","msg":"lost the Pi: stopped"}` | |

## การทำงานร่วมกัน

- **ถึงเวลายา:** Pi ส่ง `dispense` → ยาตกลงกล่อง กล่องเลื่อนออก → จอ Pi ขึ้นเตือน + พูด
  ผู้ใช้หยิบยาแล้ว**แตะเซ็นเซอร์ 1 ให้กล่องปิด** → Pi บันทึก "กินแล้ว" ให้ (หรือแตะ "กินแล้ว" บนจอ)
- **แตะเซ็นเซอร์ 2** = เริ่มคุยกับ Pebbi / หยุดพูด
- **แผง LED** เปลี่ยนหน้าตามอารมณ์ของหุ่น (ฟังอยู่ คิด พูด ดีใจ ง่วง …) แขนโบกตอนทักทายและตอนกินยาเสร็จ
- **แอปมือถือ:** เดินหน้า / ถอยหลัง / เลี้ยวซ้าย / เลี้ยวขวา (กดค้าง), โบกมือ, เปิด-ปิดกล่องยา → backend → Pi → ESP32

## ความปลอดภัย

- ล้อหยุดเองถ้าไม่ได้รับ `stop` ภายใน 10 วิ หรือ Pi เงียบเกิน 6 วิ (สายหลุด / Pi ค้าง)
- เดินหน้าไม่ได้ถ้ามีสิ่งกีดขวางใกล้กว่า 200 มม. (ตั้งได้ในเมนู 8)
- จ่ายยาทีละครั้ง และต้องปิดกล่องอยู่ก่อน · Pi จำ dose ที่จ่ายแล้วลงไฟล์ รีสตาร์ตก็ไม่จ่ายซ้ำ
