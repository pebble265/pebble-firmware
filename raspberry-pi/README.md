# pebble(a) · Raspberry Pi 5 app (pi-1.0.0)

"สมอง" ของหุ่น pebble(a) เขียนด้วย Python ทำงานบน Raspberry Pi 5 แล้วสั่งงาน ESP32 ทางสาย USB

## ทำอะไรได้บ้าง

| ฟีเจอร์ | รายละเอียด |
|---|---|
| หน้าจอสัมผัส | แอปสไตล์เดียวกับแอป Pebble บนมือถือ (แนวนอน): หน้าแรก (ทักทาย นาฬิกา ยาถัดไป เช็กอินความรู้สึก 7 อีโมจิ ปุ่มคุยกับ Pebbi), หน้ายาวันนี้ (แตะ "กินแล้ว"), หน้าคุยกับ Pebbi (พูดหรือพิมพ์ เห็นบทสนทนา), หน้าข้อมูลเครื่อง, หน้า Welcome → Setup (รหัสจับคู่ 6 หลัก ตาม Figma), หน้าต่างเตือนยา และนาฬิกาตอนพักจอ |
| ใบหน้าหุ่น (ตัวเลือก) | `--ui face`: ตาขยับ กะพริบ มีอารมณ์ ตามมองคนที่กล้องเห็น ใช้กับ Pi OS Lite ที่ไม่มี desktop |
| เช็กอินความรู้สึก | แตะอีโมจิบนจอ → `POST /robot/moods` (บันทึกเป็น source `robot` ขึ้นในแอปมือถือด้วย) |
| คุยกับ Pebbi ด้วยเสียง | กดปุ่มบนหัว → ฟัง → แปลงเป็นข้อความ → `POST /robot/chat/messages` → พูดคำตอบ คุยต่อได้หลายรอบจนเงียบหรือบอกลา ถ้า backend เจอสัญญาณวิกฤต จอจะขึ้นเบอร์สายด่วน 1323 / 1669 และเบอร์ฉุกเฉินของผู้ใช้ |
| จับคู่กับแอป | ยังไม่มีเจ้าของ: ขอรหัส 6 หลัก (`POST /robot/pairing-code`) แสดงบนจอ และประกาศ Bluetooth LE ตามโปรโตคอลเดียวกับแอป (`INFO`, `PAIR`) |
| เตือนกินยา | ได้ `dose:due` แบบ realtime (และเช็กเองทุก 2 นาที) → สั่ง ESP32 ดันยาลงกล่องแล้วเลื่อนกล่องออก → `POST /robot/doses/:id/dispensed` → จอเตือน + พูด เตือนซ้ำทุก 5 นาที → แตะเซ็นเซอร์ 1 ปิดกล่อง หรือแตะ "กินแล้ว" บนจอ = กินแล้ว (`POST /robot/doses/:id/take`) |
| รับคำสั่งจากแอป | `wake` (โบกมือ ทักแล้วรอฟัง), `forward` / `backward` / `left` / `right` / `stop` (ล้อ หยุดเองใน 10 วิ), `wave`, `box` (เปิด/ปิดกล่องยา) |
| หน้า LED ของ ESP32 | เปลี่ยนตามอารมณ์ของหุ่น (ฟังอยู่ คิด พูด ดีใจ เป็นห่วง ง่วง …) แขนโบกตอนทักทายและตอนกินยาเสร็จ |
| กล้อง | รู้ว่ามีคนอยู่หน้าหุ่น ตาและหัวหันตาม ทักเมื่อกลับมาหลังหายไปนาน ภาพไม่ถูกบันทึกหรือส่งออกจากเครื่อง |
| ช่วงเวลาเงียบ | ตามที่ตั้งในแอป (quiet hours) หน้าง่วง ไม่ชวนคุยเอง แต่ยังเตือนยา |
| Heartbeat + ThingsBoard | heartbeat ทุก 1 นาที (แบตจาก ESP32) · telemetry ทุก 30 วิ: `battery` `charging` `paired` `online` `esp32` `person` `cpu_temp` `uptime` `mood` และ `event` (boot, paired, cmd, dose_due, dispensed, dose_taken, crisis_support …) ไม่ส่งชื่อยาหรือข้อความแชทเข้า ThingsBoard |

## เซ็นเซอร์ทัชบนตัวหุ่น (ESP32)

| แตะ | ผล |
|---|---|
| ทัช 1 (กล่องยา) | เปิด/ปิดกล่อง · ถ้าหุ่นเพิ่งจ่ายยา: ปิดกล่อง = บันทึกว่ากินแล้ว |
| ทัช 2 | กำลังพูด → หยุดพูด · ปกติ → เริ่มคุยกับ Pebbi (บนจอสัมผัสแตะปุ่มแทนได้ทุกอย่าง) |

## ฮาร์ดแวร์

- Raspberry Pi 5 (4 GB ขึ้นไป) + microSD 32 GB + อะแดปเตอร์ 27 W
- จอ (HDMI หรือ DSI เช่น 5–7 นิ้ว 800×480)
- ไมค์ USB + ลำโพง (USB หรือ 3.5 มม. ผ่าน DAC/HAT)
- Camera Module 3 (หรือเว็บแคม USB)
- ESP32-S3 MedBot ที่อัปโหลด `../esp32/pebble_medbot` แล้ว ต่อด้วยสาย USB (Pi เห็นเป็น `/dev/ttyACM0`)

## ติดตั้ง

1. แฟลช **Raspberry Pi OS (64-bit, Bookworm) แบบมี desktop** ด้วย Raspberry Pi Imager ตั้ง Wi-Fi / SSH / ชื่อผู้ใช้ไว้เลย
   (แบบ Lite ก็ได้ แต่จะได้หน้าใบหน้าหุ่นแทนแอปหน้าจอสัมผัส)
2. ลงทะเบียนหุ่นใน backend (repo แอป `apps/api`) จด serial + secret ไว้:
   ```bash
   npm run build && node --env-file=.env dist/scripts/provision-device.js 4
   ```
3. ถ้าจะส่ง log เข้า ThingsBoard: สร้าง device ใน ThingsBoard แล้วคัดลอก Access token
4. บน Pi:
   ```bash
   git clone https://github.com/pebble265/pebble-firmware.git
   cd pebble-firmware/raspberry-pi
   sudo ./install.sh
   sudo nano /etc/pebble/config.toml      # ใส่ server.url, robot.serial, robot.secret, thingsboard.token
   sudo reboot
   ```
   - **มี desktop:** แอปเปิดเองหลังล็อกอิน (Chromium เต็มจอ) · log อยู่ที่ `~/pebble.log`
   - **Lite:** ใบหน้าหุ่นเปิดผ่าน systemd · log: `journalctl -u pebble -f`

### อัปเดตเวอร์ชันใหม่

ส่งโฟลเดอร์ใหม่ไป Pi (หรือ `git pull`) แล้วรัน `sudo ./install.sh` ซ้ำ ค่าตั้งใน `/etc/pebble/config.toml` ยังอยู่ แล้ว `sudo reboot`

## ทดสอบบนคอมพิวเตอร์ (ไม่มีหุ่น)

```bash
cd raspberry-pi
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt opencv-python
cp config.example.toml config.toml        # ใส่ URL backend + serial/secret ของหุ่นทดสอบ
python -m pebble --no-browser --sim --no-camera     # แล้วเปิด http://127.0.0.1:8787 ใน Chrome
python -m pebble --ui face --windowed --sim --text   # ใบหน้าหุ่นในหน้าต่าง
```

- `--no-browser` ไม่เปิด Chromium เอง · `--sim` ไม่มี ESP32 (พิมพ์คำสั่งที่จะส่งลง log) · `--text` พิมพ์คุยแทนพูด
- ต่อ ESP32 กับ Mac/PC ได้ด้วย: ตั้ง `[esp32] port = "/dev/cu.usbmodem…"` ใน config.toml
- หน้าใบหน้าหุ่น: `Space` = กดปุ่ม · `L` = กดค้าง · `D` = กดสองครั้ง · `Esc` = ปิด
- ในโหมด `--text` พิมพ์ใน terminal ได้: ข้อความ = คุยกับ Pebbi, `/b` = กดปุ่ม, `/l` = กดค้าง, `/cmd wake`

## ตั้งค่าเสียง

| ค่าใน `[voice]` | ตัวเลือก |
|---|---|
| `stt` | `google` (ฟรี ต้องต่อเน็ต) · `openai` (แม่นกว่า ใส่ `openai_api_key`) |
| `tts` | `gtts` (ฟรี ต้องต่อเน็ต) · `openai` (เสียงธรรมชาติกว่า) |
| `input_device` / `output_device` | ชื่อหรือเลขจาก `python -m sounddevice` ว่าง = ค่าเริ่มต้นของระบบ |

ความเร็วการพูดใช้ค่า `speechRate` ที่ผู้ใช้ตั้งในแอป

## โครงสร้าง

```
pebble/__main__.py   จุดเริ่ม (python -m pebble) + ตัวเลือก
pebble/app.py        ตัวควบคุมหลัก: จับคู่ heartbeat ยา คุย อารมณ์บนหน้า ไฟ
pebble/api.py        เรียก backend (/robot/*) ต่ออายุ token เอง
pebble/realtime.py   Socket.IO /realtime (robot:command, dose:due)
pebble/ui_server.py  เว็บเซิร์ฟเวอร์ในเครื่อง (127.0.0.1:8787) + เปิด Chromium เต็มจอ
pebble/ui/index.html หน้าจอสัมผัส (HTML/CSS/JS ไฟล์เดียว)
pebble/ui/*.png      โลโก้ และรูปหุ่นในหน้า Setup (ใส่ bg-welcome.jpg ที่ export จาก Figma เพื่อใช้แทนพื้นหลังหน้า Welcome ได้)
pebble/face.py       ใบหน้าหุ่น (pygame, --ui face)
pebble/voice.py      ไมค์ ลำโพง แปลงเสียง↔ข้อความ
pebble/camera.py     ตรวจจับใบหน้า (OpenCV) ตำแหน่งให้ตามมอง
pebble/ble.py        Bluetooth LE สำหรับจับคู่ (bless / BlueZ)
pebble/esp32.py      สาย serial ไป ESP32 (ดู ../esp32/PROTOCOL.md)
pebble/telemetry.py  MQTT ไป ThingsBoard
install.sh           ติดตั้งบน Pi + systemd
pebble.service       systemd unit
config.example.toml  ตัวอย่างค่าตั้ง (คัดลอกไป /etc/pebble/config.toml)
```

## ความเป็นส่วนตัวและความปลอดภัย

- `config.toml` เก็บ secret ของหุ่น ตั้งสิทธิ์ 600 และ `.gitignore` กันไม่ให้ขึ้น git
- ภาพจากกล้องอยู่ในหน่วยความจำเท่านั้น ไม่บันทึก ไม่ส่งออก
- เสียงพูดถูกส่งไปแปลงเป็นข้อความที่ Google หรือ OpenAI (ตามที่ตั้ง) แล้วข้อความไป backend ของ Pebble ไม่มีการบันทึกเสียงไว้ในเครื่อง
- ThingsBoard ได้แค่สถานะเครื่องและชื่อเหตุการณ์ ไม่มีชื่อยาหรือเนื้อหาแชท
- หน้าจอสัมผัสเปิดได้จากในเครื่องเท่านั้น (127.0.0.1) เครื่องอื่นในเครือข่ายเข้าไม่ได้
- ต้องใช้ backend ที่มี route `/robot/moods` (repo แอป `apps/api/src/moods`) สำหรับเช็กอินความรู้สึกจากหุ่น
