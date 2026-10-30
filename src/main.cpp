
/*
  ROBOT LAMP V1.8
  ESP32-WROOM-32 / NodeMCU-32S

  RGB: WS2812B, GPIO18
  SERVO: FEETECH SCS15, IDs 1-4
  Waveshare Bus Servo Adapter (A)
  UART2: RX16 / TX17, 1000000 baud
  USB Serial: 115200 baud

  No automatic servo movement on startup.
  No EEPROM writes.
*/

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <stdio.h>
#include <string.h>

// ==========================================
// 1. CONFIGURATION
// ==========================================

constexpr uint8_t RGB_PIN = 18;
constexpr uint8_t RGB_COUNT = 12;
constexpr uint8_t LED_BRIGHTNESS = 15;

constexpr uint32_t USB_BAUD = 115200;
constexpr uint32_t SERVO_BAUD = 1000000;

constexpr int SERVO_RX = 16;
constexpr int SERVO_TX = 17;

constexpr uint8_t IDS[4] = {1, 2, 3, 4};

constexpr uint16_t SAFE_MARGIN = 20;
constexpr uint16_t MAX_MANUAL_STEP = 150;
constexpr uint16_t MOVE_SPEED = 80;

constexpr int EMOTION_DELTA_LIMIT = 32;

constexpr float MIN_VOLT = 6.5f;
constexpr float MAX_VOLT = 8.4f;
constexpr uint16_t MAX_TEMP_C = 55;

Adafruit_NeoPixel ring(
    RGB_COUNT, RGB_PIN, NEO_GRB + NEO_KHZ800
);

bool online[4] = {false, false, false, false};
uint16_t homeTicks[4] = {0, 0, 0, 0};

uint8_t emotionMask = 0;
String emotion = "IDLE";
String rxLine;

// ==========================================
// 2. EMOTION KEYFRAMES
// ==========================================

struct Frame {
    int16_t delta[4];
    uint16_t holdMs;
};

const Frame idleFrames[] = {
    {{0, 0, 0, 0}, 600}
};

const Frame happyFrames[] = {
    {{-12, 8, 8, 0}, 280},
    {{12, -8, -8, 0}, 280},
    {{-12, 8, 8, 0}, 280},
    {{0, 0, 0, 0}, 450}
};

const Frame angryFrames[] = {
    {{-20, 10, 0, 0}, 220},
    {{20, -10, 0, 0}, 220},
    {{-20, 10, 0, 0}, 220},
    {{0, 0, 0, 0}, 500}
};

const Frame surprisedFrames[] = {
    {{0, 0, 0, 0}, 200},
    {{-28, 28, -20, 20}, 400},
    {{0, 0, 0, 0}, 600}
};

const Frame sadFrames[] = {
    {{0, -25, 15, 0}, 1200}
};

const Frame sleepyFrames[] = {
    {{0, -18, 18, 0}, 1600},
    {{0, -25, 25, 0}, 1600}
};

const Frame *frames = nullptr;

uint8_t frameCount = 0;
uint8_t frameIndex = 0;
uint32_t nextFrameAt = 0;
bool motionActive = false;

// ==========================================
// 3. SCS15 COMMUNICATION
// ==========================================

bool validID(uint8_t id) {
    return id >= 1 && id <= 4;
}

bool checksumOK(const uint8_t *p, size_t n) {
    uint16_t sum = 0;

    for (size_t i = 2; i < n; ++i) {
        sum += p[i];
    }

    return (sum & 255) == 255;
}

bool request(
    uint8_t id,
    uint8_t instruction,
    const uint8_t *params,
    uint8_t pLen,
    uint8_t *out,
    uint8_t &outLen,
    uint8_t expectedData
) {
    if (!validID(id) || pLen > 16) {
        return false;
    }

    uint8_t tx[24] = {
        0xFF, 0xFF, id,
        static_cast<uint8_t>(pLen + 2),
        instruction
    };

    uint16_t sum = id + tx[3] + instruction;

    for (uint8_t i = 0; i < pLen; ++i) {
        tx[5 + i] = params[i];
        sum += params[i];
    }

    tx[5 + pLen] = static_cast<uint8_t>(~sum);

    const size_t txLen = pLen + 6;

    while (Serial2.available()) {
        Serial2.read();
    }

    Serial2.write(tx, txLen);
    Serial2.flush();

    uint8_t rx[96];
    size_t got = 0;

    const uint32_t t0 = millis();

    while (millis() - t0 < 180) {
        while (
            Serial2.available() &&
            got < sizeof(rx)
        ) {
            rx[got++] =
                static_cast<uint8_t>(Serial2.read());
        }

        for (size_t i = 0; i + 6 <= got; ++i) {
            if (
                rx[i] != 0xFF ||
                rx[i + 1] != 0xFF ||
                rx[i + 2] != id
            ) {
                continue;
            }

            const uint8_t len = rx[i + 3];

            if (
                len < 2 ||
                len > 20 ||
                i + len + 4 > got
            ) {
                continue;
            }

            const size_t packetLen = len + 4;

            if (!checksumOK(rx + i, packetLen)) {
                continue;
            }

            // Ignore a command echo.
            if (
                packetLen == txLen &&
                memcmp(rx + i, tx, txLen) == 0
            ) {
                continue;
            }

            if (
                rx[i + 4] != 0 ||
                len != expectedData + 2
            ) {
                continue;
            }

            outLen = expectedData;

            for (
                uint8_t j = 0;
                j < expectedData;
                ++j
            ) {
                out[j] = rx[i + 5 + j];
            }

            return true;
        }

        delay(1);
    }

    return false;
}

// ==========================================
// 4. PING / READ / WRITE
// ==========================================

bool ping(uint8_t id) {
    uint8_t out[4];
    uint8_t n = 0;

    return request(
        id, 0x01, nullptr, 0, out, n, 0
    );
}

bool readReg(
    uint8_t id,
    uint8_t reg,
    uint8_t width,
    uint16_t &value
) {
    if (width != 1 && width != 2) {
        return false;
    }

    uint8_t p[2] = {reg, width};
    uint8_t out[4] = {0};
    uint8_t n = 0;

    if (!request(id, 0x02, p, 2, out, n, width)) {
        return false;
    }

    // SC series: HIGH byte first.
    value = (width == 1)
        ? out[0]
        : static_cast<uint16_t>(
              (out[0] << 8) | out[1]
          );

    return true;
}

// RAM-only writes; no EEPROM writes.
bool writeRAM(
    uint8_t id,
    uint8_t reg,
    const uint8_t *data,
    uint8_t width
) {
    if (!(
        (reg == 0x28 && width == 1) ||
        (reg == 0x2A && width == 6)
    )) {
        return false;
    }

    uint8_t params[7] = {reg};

    for (uint8_t i = 0; i < width; ++i) {
        params[i + 1] = data[i];
    }

    uint8_t out[4];
    uint8_t n = 0;

    return request(
        id, 0x03, params, width + 1, out, n, 0
    );
}

bool torqueSet(uint8_t id, bool enable) {
    uint8_t v = enable ? 1 : 0;

    return writeRAM(id, 0x28, &v, 1);
}

bool setGoal(uint8_t id, uint16_t ticks) {
    uint8_t data[6] = {
        static_cast<uint8_t>(ticks >> 8),
        static_cast<uint8_t>(ticks),
        0, 0,
        static_cast<uint8_t>(MOVE_SPEED >> 8),
        static_cast<uint8_t>(MOVE_SPEED)
    };

    return writeRAM(id, 0x2A, data, 6);
}

bool readPosition(
    uint8_t id,
    uint16_t &pos
) {
    return readReg(id, 0x38, 2, pos) &&
           pos <= 1023;
}

// ==========================================
// 5. SCAN / READPOS / READINFO
// ==========================================

void scanServos() {
    uint8_t count = 0;

    for (uint8_t id : IDS) {
        bool ok = ping(id);

        if (!ok) {
            delay(30);
            ok = ping(id);
        }

        online[id - 1] = ok;

        Serial.printf(
            "[%s] Servo ID %u\n",
            ok ? "FOUND" : "ERROR", id
        );

        if (ok) {
            ++count;
        }
    }

    Serial.printf("[SCAN] %u/4 detected\n", count);
}

void readPositions() {
    for (uint8_t id : IDS) {
        uint16_t p = 0;

        if (readPosition(id, p)) {
            Serial.printf(
                "[POSITION] Servo ID %u = %u ticks\n",
                id, p
            );
        } else {
            Serial.printf(
                "[ERROR] ID %u READPOS failed\n", id
            );
        }
    }
}

void readInfo() {
    for (uint8_t id : IDS) {
        uint16_t lo = 0;
        uint16_t hi = 0;
        uint16_t v = 0;
        uint16_t t = 0;

        if (
            readReg(id, 0x09, 2, lo) &&
            readReg(id, 0x0B, 2, hi) &&
            readReg(id, 0x3E, 1, v) &&
            readReg(id, 0x3F, 1, t)
        ) {
            Serial.printf(
                "[INFO] ID=%u MIN=%u MAX=%u "
                "VOLTAGE=%.1f TEMP=%u\n",
                id, lo, hi, v / 10.0f, t
            );
        } else {
            Serial.printf(
                "[INFO ERROR] Servo ID %u\n", id
            );
        }
    }
}

// ==========================================
// 6. SAFETY CHECKS
// ==========================================

bool servoChecks(
    uint8_t id,
    uint16_t &actual,
    uint16_t &low,
    uint16_t &high,
    uint16_t &torque
) {
    uint16_t volt = 0;
    uint16_t temp = 0;

    if (
        !readPosition(id, actual) ||
        !readReg(id, 0x09, 2, low) ||
        !readReg(id, 0x0B, 2, high) ||
        !readReg(id, 0x3E, 1, volt) ||
        !readReg(id, 0x3F, 1, temp) ||
        !readReg(id, 0x28, 1, torque)
    ) {
        Serial.printf(
            "[SAFE] ID %u: read failed\n", id
        );
        return false;
    }

    if (
        low >= high ||
        high > 1023 ||
        actual < low ||
        actual > high ||
        high - low <= 2 * SAFE_MARGIN ||
        torque > 1 ||
        volt / 10.0f < MIN_VOLT ||
        volt / 10.0f > MAX_VOLT ||
        temp >= MAX_TEMP_C
    ) {
        Serial.printf(
            "[SAFE] ID=%u POS=%u LIMIT=%u..%u "
            "VOLT=%.1f TEMP=%u TORQUE=%u REJECT\n",
            id, actual, low, high,
            volt / 10.0f, temp, torque
        );

        return false;
    }

    return true;
}

// ==========================================
// 7. REAL SERVO MOVE
// ==========================================

bool moveServo(uint8_t id, uint16_t target) {
    uint16_t actual = 0;
    uint16_t lo = 0;
    uint16_t hi = 0;
    uint16_t torque = 0;

    if (
        !validID(id) ||
        !servoChecks(id, actual, lo, hi, torque)
    ) {
        return false;
    }

    if (
        target < lo + SAFE_MARGIN ||
        target > hi - SAFE_MARGIN ||
        abs(
            static_cast<int>(target) -
            static_cast<int>(actual)
        ) > MAX_MANUAL_STEP
    ) {
        Serial.printf(
            "[MOVE REJECT] ID=%u FROM=%u "
            "TARGET=%u ALLOWED=%u..%u\n",
            id, actual, target,
            lo + SAFE_MARGIN,
            hi - SAFE_MARGIN
        );

        return false;
    }

    // First hold the current position before torque ON.
    if (torque == 0) {
        if (!setGoal(id, actual)) {
            Serial.println(
                "[MOVE ERROR] Hold goal write"
            );
            return false;
        }

        uint16_t check = 0;

        if (
            !readReg(id, 0x2A, 2, check) ||
            check != actual ||
            !torqueSet(id, true)
        ) {
            Serial.println(
                "[MOVE ERROR] Goal/Torque check"
            );
            return false;
        }

        delay(200);

        uint16_t now = 0;

        if (
            !readPosition(id, now) ||
            abs(
                static_cast<int>(now) -
                static_cast<int>(actual)
            ) > 5
        ) {
            torqueSet(id, false);

            Serial.println(
                "[MOVE ERROR] Unexpected movement"
            );

            return false;
        }
    }

    if (!setGoal(id, target)) {
        Serial.println(
            "[MOVE ERROR] Goal write failed"
        );
        return false;
    }

    uint16_t written = 0;

    if (
        !readReg(id, 0x2A, 2, written) ||
        written != target
    ) {
        Serial.println(
            "[MOVE ERROR] Goal verify failed"
        );
        return false;
    }

    Serial.printf(
        "[MOVE OK] ID=%u FROM=%u TARGET=%u\n",
        id, actual, target
    );

    // Accepted command, not proof of reaching target.
    return true;
}

// ==========================================
// 8. ARM / DISARM EMOTION
// ==========================================

void disarm() {
    emotionMask = 0;
    motionActive = false;
    frames = nullptr;

    Serial.println(
        "[EMOTION] DISARMED"
    );

    // Torque remains unchanged.
}

bool armEmotion(uint8_t mask) {
    disarm();

    uint16_t bases[4] = {0, 0, 0, 0};

    for (uint8_t i = 0; i < 4; ++i) {
        if (!(mask & (1u << i))) {
            continue;
        }

        uint16_t pos = 0;
        uint16_t lo = 0;
        uint16_t hi = 0;
        uint16_t torque = 0;

        if (
            !ping(IDS[i]) ||
            !servoChecks(
                IDS[i], pos, lo, hi, torque
            ) ||
            static_cast<int>(pos) <
                static_cast<int>(lo) +
                SAFE_MARGIN + EMOTION_DELTA_LIMIT ||
            static_cast<int>(pos) >
                static_cast<int>(hi) -
                SAFE_MARGIN - EMOTION_DELTA_LIMIT
        ) {
            Serial.printf(
                "[ARM REJECTED] ID=%u\n", IDS[i]
            );
            return false;
        }

        bases[i] = pos;

        Serial.printf(
            "[ARM CHECK] ID=%u HOME=%u\n",
            IDS[i], pos
        );
    }

    for (uint8_t i = 0; i < 4; ++i) {
        homeTicks[i] = bases[i];
    }

    emotionMask = mask;

    Serial.printf(
        "[EMOTION] ARMED mask=0x%02X\n",
        emotionMask
    );

    return true;
}

// ==========================================
// 9. RGB EMOTION COLORS
// ==========================================

void setColor(const String &e) {
    uint8_t r = 0;
    uint8_t g = 150;
    uint8_t b = 255;

    if (e == "HAPPY") {
        r = 255; g = 200; b = 0;
    } else if (e == "ANGRY") {
        r = 255; g = 0; b = 0;
    } else if (e == "SAD") {
        r = 0; g = 0; b = 255;
    } else if (e == "SURPRISED") {
        r = 255; g = 255; b = 255;
    } else if (e == "SLEEPY") {
        r = 150; g = 0; b = 255;
    } else if (e == "STOP") {
        r = 0; g = 0; b = 0;
    }

    ring.fill(ring.Color(r, g, b));
    ring.show();
}

// ==========================================
// 10. SELECT EMOTION
// ==========================================

void setEmotion(String e) {
    if (e == "SHOCK") {
        e = "SURPRISED";
    }

    if (e == "IDLE") {
        frames = idleFrames;
        frameCount = sizeof(idleFrames) / sizeof(Frame);
    } else if (e == "HAPPY") {
        frames = happyFrames;
        frameCount = sizeof(happyFrames) / sizeof(Frame);
    } else if (e == "ANGRY") {
        frames = angryFrames;
        frameCount = sizeof(angryFrames) / sizeof(Frame);
    } else if (e == "SURPRISED") {
        frames = surprisedFrames;
        frameCount =
            sizeof(surprisedFrames) / sizeof(Frame);
    } else if (e == "SAD") {
        frames = sadFrames;
        frameCount = sizeof(sadFrames) / sizeof(Frame);
    } else if (e == "SLEEPY") {
        frames = sleepyFrames;
        frameCount =
            sizeof(sleepyFrames) / sizeof(Frame);
    } else {
        Serial.printf(
            "[ERROR] Unknown emotion: %s\n",
            e.c_str()
        );
        return;
    }

    emotion = e;
    frameIndex = 0;
    nextFrameAt = millis();
    motionActive = true;

    Serial.printf(
        "[EMOTION] %s | %s\n",
        e.c_str(),
        emotionMask ? "REAL" : "SIMULATION"
    );

    setColor(e);
}

// ==========================================
// 11. UPDATE SERVO EMOTION
// ==========================================

void updateMotion() {
    if (
        !motionActive ||
        !frames ||
        static_cast<int32_t>(
            millis() - nextFrameAt
        ) < 0
    ) {
        return;
    }

    if (frameIndex >= frameCount) {
        motionActive = false;
        Serial.println("[MOTION] Done");
        return;
    }

    const Frame &f = frames[frameIndex++];

    for (uint8_t i = 0; i < 4; ++i) {
        if (!(emotionMask & (1u << i))) {
            continue;
        }

        int delta = f.delta[i];

        if (
            delta < -EMOTION_DELTA_LIMIT ||
            delta > EMOTION_DELTA_LIMIT ||
            !moveServo(
                IDS[i],
                static_cast<uint16_t>(
                    static_cast<int>(homeTicks[i]) +
                    delta
                )
            )
        ) {
            Serial.printf(
                "[EMOTION ERROR] ID=%u\n", IDS[i]
            );

            disarm();
            return;
        }
    }

    if (!emotionMask) {
        Serial.printf(
            "[FRAME] %s: SIMULATION ONLY\n",
            emotion.c_str()
        );
    }

    nextFrameAt = millis() + f.holdMs;
}

// ==========================================
// 12. STATUS / HELP
// ==========================================

void printStatus() {
    Serial.printf(
        "[STATUS] Emotion=%s Servo=%s MASK=0x%02X\n",
        emotion.c_str(),
        emotionMask ? "REAL" : "SIMULATION",
        emotionMask
    );

    for (uint8_t id : IDS) {
        Serial.printf(
            "Servo ID %u: %s\n",
            id,
            online[id - 1]
                ? "FOUND" : "NOT CONFIRMED"
        );
    }
}

void printHelp() {
    Serial.println(
        "SCAN / READPOS / READINFO / STATUS / HELP"
    );
    Serial.println(
        "MOVE 1 300 / TORQUEOFF 1"
    );
    Serial.println(
        "ARM EMOTION 1 / 2 / 3 / 4"
    );
    Serial.println(
        "ARM EMOTION ALL / DISARM"
    );
    Serial.println(
        "IDLE / HAPPY / SAD / ANGRY / SURPRISED"
    );
    Serial.println(
        "SLEEPY / STOP"
    );
}

// ==========================================
// 13. SERIAL COMMAND HANDLER
// ==========================================

void command(String cmd) {
    cmd.trim();
    cmd.toUpperCase();

    if (cmd.isEmpty()) {
        return;
    }

    if (cmd == "SCAN") {
        scanServos();
        return;
    }

    if (cmd == "READPOS") {
        readPositions();
        return;
    }

    if (cmd == "READINFO") {
        readInfo();
        return;
    }

    if (cmd == "STATUS") {
        printStatus();
        return;
    }

    if (cmd == "HELP") {
        printHelp();
        return;
    }

    if (
        cmd == "DISARM" ||
        cmd == "EMOTION DISARM"
    ) {
        disarm();
        return;
    }

    if (cmd == "STOP") {
        disarm();
        emotion = "STOP";
        setColor("STOP");

        Serial.println(
            "[STOP] No new frames; NOT hardware E-stop"
        );
        return;
    }

    // ======================================
    // ARM EMOTION
    // ======================================

    if (cmd == "ARM EMOTION ALL") {
        armEmotion(0x0F);
        return;
    }

    if (
        cmd.startsWith("ARM EMOTION ") &&
        cmd.length() == 13 &&
        cmd[12] >= '1' &&
        cmd[12] <= '4'
    ) {
        armEmotion(
            1u << (cmd[12] - '1')
        );
        return;
    }

    // ======================================
    // TORQUE OFF
    // ======================================

    if (cmd.startsWith("TORQUEOFF ")) {
        unsigned int id = 0;
        char extra;

        disarm();

        if (
            sscanf(
                cmd.c_str(),
                "TORQUEOFF %u %c",
                &id, &extra
            ) == 1 &&
            id >= 1 &&
            id <= 4
        ) {
            Serial.printf(
                "[TORQUEOFF] ID %u: %s\n",
                id,
                torqueSet(id, false)
                    ? "ACK" : "FAILED"
            );
        } else {
            Serial.println(
                "[ERROR] Usage: TORQUEOFF 1"
            );
        }

        return;
    }

    // ======================================
    // MANUAL MOVE
    // ======================================

    if (cmd.startsWith("MOVE ")) {
        unsigned int id = 0;
        unsigned int target = 0;
        char extra;

        disarm();

        if (
            sscanf(
                cmd.c_str(),
                "MOVE %u %u %c",
                &id, &target, &extra
            ) == 2 &&
            id >= 1 &&
            id <= 4 &&
            target <= 1023
        ) {
            moveServo(id, target);
        } else {
            Serial.println(
                "[ERROR] Usage: MOVE 1 300"
            );
        }

        return;
    }

    // Other commands are emotion names.
    setEmotion(cmd);
}

// ==========================================
// 14. SETUP
// ==========================================

void setup() {
    Serial.begin(USB_BAUD);
    rxLine.reserve(64);

    ring.begin();
    ring.setBrightness(LED_BRIGHTNESS);
    ring.clear();
    ring.show();

    Serial2.begin(
        SERVO_BAUD,
        SERIAL_8N1,
        SERVO_RX,
        SERVO_TX
    );

    delay(300);

    Serial.println(
        "=== ROBOT LAMP V1.8 / ESP32 READY ==="
    );

    setColor("IDLE");

    scanServos();
    printHelp();
    printStatus();

    Serial.println(
        "[READY] Simulation until ARM EMOTION"
    );
}

// ==========================================
// 15. MAIN LOOP
// ==========================================

void loop() {
    while (Serial.available()) {
        char c =
            static_cast<char>(Serial.read());

        if (c == '\n' || c == '\r') {
            if (rxLine.length()) {
                command(rxLine);
            }

            rxLine = "";

        } else if (rxLine.length() < 63) {
            rxLine += c;

        } else {
            rxLine = "";

            Serial.println(
                "[ERROR] Command too long"
            );
        }
    }

    updateMotion();
}
