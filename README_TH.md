# Robot Lamp V1.0 — VS Code + PlatformIO (ESP32)

โปรเจกต์โคมไฟแสดงอารมณ์ ใช้ ESP32-WROOM-32, Waveshare Bus Servo Adapter (A) และเซอร์โว 4 ตัว
แนวคิดการแบ่ง Emotion → Animation/LED อ้างอิงจาก https://github.com/autonomous-ai/lampOS

## ใช้ Arduino หรือ ESP32?

- **ESP32** = ชิป/บอร์ดควบคุม (ฮาร์ดแวร์)
- **Arduino framework** = ไลบรารีและรูปแบบการเขียนโปรแกรมที่ใช้กับ ESP32 ได้
- **VS Code** = โปรแกรมแก้ไขโค้ด
- **PlatformIO** = ส่วนขยายสำหรับจัดการบอร์ด, คอมไพล์, อัปโหลด และ Serial Monitor ใน VS Code

ไฟล์หลักของ PlatformIO คือ `src/main.cpp` (แทนไฟล์ `.ino` ของ Arduino IDE)
โค้ดโปรเจกต์นี้ยังใช้ Arduino framework บน ESP32 ตาม `platformio.ini` ซึ่งเป็นวิธีปกติ

## เริ่มต้น

1. ติดตั้ง VS Code และส่วนขยาย **PlatformIO IDE**
2. แตกไฟล์ ZIP แล้วเปิด **ทั้งโฟลเดอร์** `Robot_Lamp_VSCode` ใน VS Code
3. ต่อสาย USB ของ ESP32 กับคอมพิวเตอร์ (ยังไม่ต้องจ่ายไฟให้เซอร์โว)
4. ตรวจสอบว่า `platformio.ini` มี `board = esp32dev` และ `framework = arduino`
5. เปิด `src/main.cpp` (ไม่ต้องย้ายไฟล์)
6. จากแถบ PlatformIO เลือก **Build** เพื่อคอมไพล์ จากนั้น **Upload** เพื่อลงบอร์ด
7. เปิด **Monitor** ที่ 115200 baud และส่งคำสั่งปิดท้ายด้วย Newline:
   - `HELP`
   - `STATUS`
   - `SURPRISED`
   - `ANGRY`
   - `HAPPY`
   - `IDLE`
   - `STOP`

> หมายเหตุ: ใน PlatformIO device monitor ที่ไม่ส่งบรรทัดใหม่อาจทำให้ ESP32 ยังไม่รับคำสั่ง
> ถ้า Upload ไม่ได้ให้ตรวจ COM, ไดรเวอร์ USB-UART และลองกดปุ่ม BOOT ค้างช่วงเริ่ม Upload

## โหมดปลอดภัย

`src/main.cpp` ตั้งค่าเริ่มต้น:

```cpp
#define ENABLE_REAL_STS_SERVOS 0
#define SERVO_SETUP_VERIFIED 0
```

ให้คงค่าเป็น `0` ทั้งคู่ก่อน เพราะไม่มีการยืนยันรุ่นเซอร์โว, ช่วงมุม, ID และแหล่งจ่ายจริง
โปรแกรมจะแสดงภาพจำลองท่าทางผ่าน Serial Monitor เท่านั้น **ไม่สั่งให้มอเตอร์หมุน**
และยังไม่ได้ควบคุมหลอด RGB ภายนอก

ก่อนเปิดใช้งานมอเตอร์จริง ต้องยืนยันรุ่น/โปรโตคอล/แรงดัน/ID และสอบเทียบจุดศูนย์และขีดจำกัด
ระบบ `STOP` เป็นการหยุดส่ง keyframe ไม่ใช่ Emergency Stop ที่ตัดกำลังไฟ

## ทดสอบกล้อง (ทางเลือก)

ไฟล์: `tools/emotion_lamp_camera.py` (สคริปต์ Python บน Windows ไม่ใช่โปรแกรมของ ESP32)

เปิด Terminal ที่โฟลเดอร์โปรเจกต์และติดตั้ง:

```powershell
py -m pip install opencv-python mediapipe pyserial
```

แก้ตัวแปร `ESP32_PORT = None` ในไฟล์กล้องเป็น `"COM5"` หรือ COM จริงของเครื่อง
(ปิด PlatformIO Serial Monitor ก่อนใช้ Python เพราะเปิดพอร์ตเดียวกันพร้อมกันไม่ได้)

จากนั้นรัน:

```powershell
py tools/emotion_lamp_camera.py
```

- เข้าใกล้กล้อง: `SURPRISED`
- ชูนิ้วกลาง: `ANGRY`
- ไม่มีท่าทาง: `IDLE`

ครั้งแรกต้องเชื่อมต่ออินเทอร์เน็ตเพื่อดาวน์โหลดไฟล์โมเดล MediaPipe
ตัวตรวจจับใช้ขนาดภาพมือเป็นตัวประมาณความใกล้ ไม่ใช่เซ็นเซอร์วัดระยะจริง

## ตรวจสอบก่อนต่ออุปกรณ์

- ตรวจสอบมอเตอร์ทั้ง 4 ตัวว่าเป็น STS/SMS หรือ SC และอ่านพิกัดแรงดันที่สติกเกอร์
- Waveshare Bus Servo Adapter (A) ควบคุมผ่าน UART ได้ แต่ต้องตรวจสอบกระแสที่บอร์ดรองรับ
- ห้ามป้อนไฟ 7.5V เข้าขา 3V3/5V ของ ESP32 โดยตรง
- ยังไม่ต้องซื้อ LED หรือ Step-down เพื่อรันทดสอบขั้นแรก

**สถานะการตรวจสอบ:** จัดรูปแบบโปรเจกต์ให้เหมาะกับ PlatformIO แล้ว แต่ยังไม่ได้ทดสอบ Build ผ่าน PlatformIO หรืออัปโหลดไปยังบอร์ดจริง
