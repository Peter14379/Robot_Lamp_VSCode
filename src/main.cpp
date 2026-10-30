
/*
  ROBOT LAMP V1.7
  ESP32-WROOM-32 / NodeMCU-32S

  RGB: WS2812B 12 LEDs, GPIO18
  SERVO: FEETECH SCS15, ID 1-4
  UART: GPIO16 RX2, GPIO17 TX2
  Waveshare Bus Servo Adapter (A)
  Servo baud: 1000000
  USB baud: 115200

  COMMANDS:
    SCAN
    READPOS
    READINFO
    MOVE 1 400
    TORQUEOFF 1
    IDLE / HAPPY / ANGRY / SURPRISED
    SAD / SLEEPY / STOP / STATUS / HELP

  Emotion movements remain SIMULATION ONLY.
  Only explicit MOVE commands operate real servos.
  No EEPROM writes.
*/

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <string.h>
#include <stdio.h>

// ==========================================
// 1. CONFIGURATION
// ==========================================

constexpr uint8_t RGB_PIN = 18;
constexpr uint8_t RGB_COUNT = 12;
constexpr uint8_t ROBOT_LED_BRIGHTNESS = 15;

constexpr uint32_t USB_BAUD = 115200;
constexpr uint32_t SERVO_BAUD = 1000000;

constexpr int SERVO_RX_PIN = 16;
constexpr int SERVO_TX_PIN = 17;

constexpr uint8_t SERVO_COUNT = 4;
constexpr uint32_t UART_TIMEOUT_MS = 180;
constexpr size_t UART_BUFFER_SIZE = 128;

const uint8_t SERVO_IDS[SERVO_COUNT] = {
    1, 2, 3, 4
};

bool servoOnline[SERVO_COUNT] = {
    false, false, false, false
};

Adafruit_NeoPixel rgbRing(
    RGB_COUNT,
    RGB_PIN,
    NEO_GRB + NEO_KHZ800
);

// ==========================================
// 2. MANUAL MOVE SAFETY
// ==========================================

constexpr uint16_t MOVE_SAFE_MARGIN = 20;
constexpr uint16_t MOVE_MAX_STEP = 150;
constexpr uint16_t MOVE_SPEED = 80;

constexpr float MOVE_MIN_VOLTAGE = 6.5f;
constexpr float MOVE_MAX_VOLTAGE = 8.4f;
constexpr uint16_t MOVE_MAX_TEMP = 55;

// No automatic motion on startup.
// No automatic emotion-to-servo movement.

String currentEmotion = "IDLE";
String rxLine;

// ==========================================
// 3. SIMULATION DATA
// ==========================================

const int16_t HOME_TICKS[SERVO_COUNT] = {
    2048, 2048, 2048, 2048
};

const int16_t LOW_TICKS[SERVO_COUNT] = {
    2000, 2000, 2000, 2000
};

const int16_t HIGH_TICKS[SERVO_COUNT] = {
    2096, 2096, 2096, 2096
};

struct Keyframe {
    int16_t delta[SERVO_COUNT];
    uint16_t holdMs;
    uint16_t speed;
};

struct Motion {
    const Keyframe *frames;
    uint8_t count;
};

const Keyframe MOTION_IDLE[] = {
    {{0, 0, 0, 0}, 600, 100}
};

const Keyframe MOTION_SURPRISED[] = {
    {{0, 0, 0, 0}, 200, 150},
    {{-28, 28, -20, 20}, 400, 180},
    {{0, 0, 0, 0}, 600, 120}
};

const Keyframe MOTION_ANGRY[] = {
    {{-20, 10, 0, 0}, 220, 160},
    {{20, -10, 0, 0}, 220, 160},
    {{-20, 10, 0, 0}, 220, 160},
    {{0, 0, 0, 0}, 500, 100}
};

const Keyframe MOTION_HAPPY[] = {
    {{-12, 8, 8, 0}, 280, 150},
    {{12, -8, -8, 0}, 280, 150},
    {{-12, 8, 8, 0}, 280, 150},
    {{0, 0, 0, 0}, 450, 120}
};

const Keyframe MOTION_SAD[] = {
    {{0, -25, 15, 0}, 1200, 65}
};

const Keyframe MOTION_SLEEPY[] = {
    {{0, -18, 18, 0}, 1600, 45},
    {{0, -25, 25, 0}, 1600, 45}
};

#define TO_MOTION(arr) \
    {arr, static_cast<uint8_t>(sizeof(arr) / sizeof(arr[0]))}

const Motion IDLE_MOTION = TO_MOTION(MOTION_IDLE);
const Motion SURPRISED_MOTION = TO_MOTION(MOTION_SURPRISED);
const Motion ANGRY_MOTION = TO_MOTION(MOTION_ANGRY);
const Motion HAPPY_MOTION = TO_MOTION(MOTION_HAPPY);
const Motion SAD_MOTION = TO_MOTION(MOTION_SAD);
const Motion SLEEPY_MOTION = TO_MOTION(MOTION_SLEEPY);

const Motion *activeMotion = nullptr;

uint8_t nextFrameIndex = 0;
uint32_t nextFrameAt = 0;
bool motionActive = false;

// ==========================================
// 4. UART HELPERS
// ==========================================

static void printHex(
    const uint8_t *data,
    size_t count
) {
    for (size_t i = 0; i < count; ++i) {
        Serial.printf(
            "%02X%s",
            data[i],
            i + 1 == count ? "" : " "
        );
    }
    Serial.println();
}

static bool checksumOK(
    const uint8_t *packet,
    size_t total
) {
    uint16_t sum = 0;

    for (size_t i = 2; i < total; ++i) {
        sum += packet[i];
    }

    return (sum & 0xFF) == 0xFF;
}

static bool validServoID(uint8_t sid) {
    for (uint8_t id : SERVO_IDS) {
        if (sid == id) {
            return true;
        }
    }
    return false;
}

// ==========================================
// 5. UART SEND AND RECEIVE
// ==========================================

static size_t sendAndReceive(
    uint8_t sid,
    const uint8_t *tx,
    size_t txLength,
    uint8_t *rx,
    size_t rxMax
) {
    while (Serial2.available() > 0) {
        Serial2.read();
    }

    Serial.printf("[UART TX ID %u] ", sid);
    printHex(tx, txLength);

    Serial2.write(tx, txLength);
    Serial2.flush();

    size_t count = 0;
    bool overflow = false;

    const uint32_t started = millis();

    while (
        static_cast<uint32_t>(
            millis() - started
        ) < UART_TIMEOUT_MS
    ) {
        while (Serial2.available() > 0) {
            const int value = Serial2.read();

            if (value < 0) {
                continue;
            }

            if (count < rxMax) {
                rx[count++] = static_cast<uint8_t>(value);
            } else {
                overflow = true;
            }
        }

        delay(1);
    }

    Serial.printf("[UART RX ID %u] ", sid);

    if (count == 0) {
        Serial.println("NO DATA");
    } else {
        printHex(rx, count);
    }

    if (overflow) {
        Serial.println(
            "[UART WARNING] RX overflow"
        );
    }

    return count;
}

// ==========================================
// 6. PING
// ==========================================

static bool pingScsServo(uint8_t sid) {
    const uint8_t tx[] = {
        0xFF, 0xFF, sid, 0x02, 0x01,
        static_cast<uint8_t>(
            ~(sid + 0x02 + 0x01)
        )
    };

    uint8_t rx[UART_BUFFER_SIZE];

    const size_t count = sendAndReceive(
        sid, tx, sizeof(tx), rx, sizeof(rx)
    );

    for (size_t i = 0; i + 6 <= count; ++i) {
        if (
            rx[i] == 0xFF &&
            rx[i + 1] == 0xFF &&
            rx[i + 2] == sid &&
            rx[i + 3] == 0x02 &&
            checksumOK(rx + i, 6) &&
            rx[i + 4] == 0x00
        ) {
            return true;
        }
    }

    return false;
}

static void scanServos() {
    Serial.println();
    Serial.println(
        "===== SCS15 UART DIAGNOSTIC ====="
    );

    uint8_t found = 0;

    for (uint8_t i = 0; i < SERVO_COUNT; ++i) {
        const uint8_t sid = SERVO_IDS[i];

        bool ok = pingScsServo(sid);

        if (!ok) {
            delay(30);
            ok = pingScsServo(sid);
        }

        servoOnline[i] = ok;

        if (ok) {
            ++found;
            Serial.printf(
                "[FOUND] Servo ID %u\n", sid
            );
        } else {
            Serial.printf(
                "[ERROR] Servo ID %u no reply\n", sid
            );
        }
    }

    Serial.printf(
        "[SCAN] %u/%u servo(s) responding\n",
        found, SERVO_COUNT
    );

    Serial.println(
        "===== PING TEST COMPLETE (NO MOVEMENT) ====="
    );
}

// ==========================================
// 7. READ REGISTERS
// SCS15 SC SERIES: HIGH BYTE FIRST
// ==========================================

static bool readScsRegister(
    uint8_t sid,
    uint8_t address,
    uint8_t width,
    uint16_t &result
) {
    if (
        !validServoID(sid) ||
        (width != 1 && width != 2)
    ) {
        return false;
    }

    const uint8_t tx[] = {
        0xFF, 0xFF, sid, 0x04, 0x02,
        address, width,
        static_cast<uint8_t>(
            ~(sid + 0x04 + 0x02 + address + width)
        )
    };

    uint8_t rx[UART_BUFFER_SIZE];

    const size_t count = sendAndReceive(
        sid, tx, sizeof(tx), rx, sizeof(rx)
    );

    const size_t packetSize = width + 6;

    for (
        size_t i = 0;
        i + packetSize <= count;
        ++i
    ) {
        if (
            rx[i] != 0xFF ||
            rx[i + 1] != 0xFF ||
            rx[i + 2] != sid ||
            rx[i + 3] != width + 2 ||
            !checksumOK(rx + i, packetSize)
        ) {
            continue;
        }

        // Skip an exact command echo.
        if (
            packetSize == sizeof(tx) &&
            memcmp(rx + i, tx, sizeof(tx)) == 0
        ) {
            continue;
        }

        if (rx[i + 4] != 0x00) {
            Serial.printf(
                "[READ ERROR] ID=%u ADDR=0x%02X STATUS=%u\n",
                sid, address, rx[i + 4]
            );
            continue;
        }

        if (width == 1) {
            result = rx[i + 5];
        } else {
            result =
                (static_cast<uint16_t>(rx[i + 5]) << 8) |
                static_cast<uint16_t>(rx[i + 6]);
        }

        return true;
    }

    Serial.printf(
        "[READ ERROR] ID=%u ADDR=0x%02X NO REPLY\n",
        sid, address
    );

    return false;
}

static bool readScsPosition(
    uint8_t sid,
    uint16_t &position
) {
    uint16_t value = 0;

    if (!readScsRegister(sid, 0x38, 2, value)) {
        return false;
    }

    if (value > 1023) {
        return false;
    }

    position = value;
    return true;
}

// ==========================================
// 8. READPOS
// ==========================================

static void readAllPositions() {
    Serial.println();
    Serial.println(
        "===== REAL SERVO POSITIONS (READ ONLY) ====="
    );

    for (uint8_t sid : SERVO_IDS) {
        uint16_t position = 0;

        if (readScsPosition(sid, position)) {
            Serial.printf(
                "[POSITION] Servo ID %u = %u ticks\n",
                sid, position
            );
        } else {
            Serial.printf(
                "[ERROR] ID %u position read failed\n",
                sid
            );
        }
    }

    Serial.println(
        "===== READ COMPLETE (NO MOVEMENT) ====="
    );
}

// ==========================================
// 9. READINFO
// ==========================================

static void readAllServoInfo() {
    Serial.println();
    Serial.println(
        "===== SERVO READINFO (READ ONLY) ====="
    );

    for (uint8_t sid : SERVO_IDS) {
        uint16_t low = 0;
        uint16_t high = 0;
        uint16_t voltageRaw = 0;
        uint16_t temp = 0;

        Serial.println("--------------------------------");

        bool ok =
            readScsRegister(sid, 0x09, 2, low) &&
            readScsRegister(sid, 0x0B, 2, high) &&
            readScsRegister(sid, 0x3E, 1, voltageRaw) &&
            readScsRegister(sid, 0x3F, 1, temp);

        if (!ok) {
            Serial.printf(
                "[INFO ERROR] ID %u read failed\n",
                sid
            );
            continue;
        }

        Serial.printf(
            "[INFO] ID=%u MIN=%u MAX=%u VOLTAGE=%.1f TEMP=%u\n",
            sid, low, high,
            voltageRaw / 10.0f, temp
        );

        if (low >= high || high > 1023) {
            Serial.printf(
                "[WARNING] ID %u invalid limits\n",
                sid
            );
        } else {
            Serial.printf(
                "[INFO OK] Servo ID %u registers read\n",
                sid
            );
        }
    }

    Serial.println("--------------------------------");
    Serial.println(
        "===== READINFO COMPLETE (NO MOVEMENT) ====="
    );
}

// ==========================================
// 10. RAM WRITE
// Only 0x28 and 0x2A are allowed.
// No EEPROM addresses.
// ==========================================

static bool writeScsRAM(
    uint8_t sid,
    uint8_t address,
    const uint8_t *data,
    uint8_t width
) {
    if (!validServoID(sid)) {
        return false;
    }

    if (
        !(
            (address == 0x28 && width == 1) ||
            (address == 0x2A && width == 6)
        )
    ) {
        Serial.println(
            "[WRITE ERROR] Address not allowed"
        );
        return false;
    }

    uint8_t tx[16] = {0};

    tx[0] = 0xFF;
    tx[1] = 0xFF;
    tx[2] = sid;
    tx[3] = width + 3;
    tx[4] = 0x03;
    tx[5] = address;

    uint16_t sum =
        sid + tx[3] + tx[4] + address;

    for (uint8_t i = 0; i < width; ++i) {
        tx[6 + i] = data[i];
        sum += data[i];
    }

    tx[6 + width] =
        static_cast<uint8_t>(~sum);

    uint8_t rx[UART_BUFFER_SIZE];

    const size_t count = sendAndReceive(
        sid,
        tx,
        width + 7,
        rx,
        sizeof(rx)
    );

    // ACK:
    // FF FF ID 02 00 CHECKSUM

    for (size_t i = 0; i + 6 <= count; ++i) {
        if (
            rx[i] == 0xFF &&
            rx[i + 1] == 0xFF &&
            rx[i + 2] == sid &&
            rx[i + 3] == 0x02 &&
            checksumOK(rx + i, 6)
        ) {
            if (rx[i + 4] == 0x00) {
                return true;
            }

            Serial.printf(
                "[WRITE ERROR] ID=%u STATUS=%u\n",
                sid, rx[i + 4]
            );
        }
    }

    Serial.printf(
        "[WRITE ERROR] ID=%u NO VALID ACK\n",
        sid
    );
    return false;
}

static bool setScsTorque(
    uint8_t sid,
    bool enabled
) {
    const uint8_t value = enabled ? 1 : 0;

    return writeScsRAM(
        sid, 0x28, &value, 1
    );
}

static bool writeScsGoal(
    uint8_t sid,
    uint16_t target
) {
    // Position HIGH, LOW
    // Time HIGH, LOW
    // Speed HIGH, LOW
    const uint8_t data[6] = {
        static_cast<uint8_t>(target >> 8),
        static_cast<uint8_t>(target & 0xFF),
        0x00,
        0x00,
        static_cast<uint8_t>(MOVE_SPEED >> 8),
        static_cast<uint8_t>(MOVE_SPEED & 0xFF)
    };

    return writeScsRAM(
        sid, 0x2A, data, sizeof(data)
    );
}

// ==========================================
// 11. MANUAL MOVE
// Example: MOVE 1 400
// ==========================================

static bool moveOneServo(
    uint8_t sid,
    uint16_t target
) {
    if (!validServoID(sid)) {
        Serial.println(
            "[MOVE ERROR] Invalid Servo ID"
        );
        return false;
    }

    uint16_t actual = 0;
    uint16_t low = 0;
    uint16_t high = 0;
    uint16_t voltageRaw = 0;
    uint16_t temperature = 0;
    uint16_t torque = 0;

    if (!readScsPosition(sid, actual)) {
        Serial.println(
            "[MOVE ERROR] Cannot read Actual"
        );
        return false;
    }

    if (
        !readScsRegister(sid, 0x09, 2, low) ||
        !readScsRegister(sid, 0x0B, 2, high)
    ) {
        Serial.println(
            "[MOVE ERROR] Cannot read Min/Max"
        );
        return false;
    }

    if (
        !readScsRegister(sid, 0x3E, 1, voltageRaw) ||
        !readScsRegister(sid, 0x3F, 1, temperature)
    ) {
        Serial.println(
            "[MOVE ERROR] Cannot read Voltage/Temp"
        );
        return false;
    }

    if (
        low >= high ||
        high > 1023 ||
        actual < low ||
        actual > high ||
        high - low <= 2 * MOVE_SAFE_MARGIN
    ) {
        Serial.println(
            "[MOVE ERROR] Invalid hardware limits"
        );
        return false;
    }

    const uint16_t safeLow =
        low + MOVE_SAFE_MARGIN;

    const uint16_t safeHigh =
        high - MOVE_SAFE_MARGIN;

    if (
        target < safeLow ||
        target > safeHigh
    ) {
        Serial.printf(
            "[MOVE ERROR] Target %u outside %u..%u\n",
            target, safeLow, safeHigh
        );
        return false;
    }

    const int distance = abs(
        static_cast<int>(target) -
        static_cast<int>(actual)
    );

    if (distance > MOVE_MAX_STEP) {
        Serial.printf(
            "[MOVE ERROR] Distance %d exceeds %u ticks\n",
            distance, MOVE_MAX_STEP
        );
        return false;
    }

    const float voltage = voltageRaw / 10.0f;

    if (
        voltage < MOVE_MIN_VOLTAGE ||
        voltage > MOVE_MAX_VOLTAGE
    ) {
        Serial.println(
            "[MOVE ERROR] Voltage outside range"
        );
        return false;
    }

    if (temperature >= MOVE_MAX_TEMP) {
        Serial.println(
            "[MOVE ERROR] Temperature too high"
        );
        return false;
    }

    if (!readScsRegister(sid, 0x28, 1, torque)) {
        Serial.println(
            "[MOVE ERROR] Cannot read Torque"
        );
        return false;
    }

    if (torque > 1) {
        Serial.println(
            "[MOVE ERROR] Unexpected torque mode"
        );
        return false;
    }

    if (torque == 0) {
        // Set holding goal before enabling torque.
        if (!writeScsGoal(sid, actual)) {
            Serial.println(
                "[MOVE ERROR] Cannot write holding goal"
            );
            return false;
        }

        // Verify encoding and written position.
        uint16_t verifyGoal = 0;

        if (
            !readScsRegister(
                sid, 0x2A, 2, verifyGoal
            ) ||
            verifyGoal != actual
        ) {
            Serial.println(
                "[MOVE ERROR] Holding goal verification failed"
            );
            return false;
        }

        if (!setScsTorque(sid, true)) {
            Serial.println(
                "[MOVE ERROR] Cannot enable Torque"
            );
            return false;
        }

        delay(200);

        uint16_t afterEnable = 0;

        if (
            !readScsPosition(sid, afterEnable) ||
            abs(
                static_cast<int>(afterEnable) -
                static_cast<int>(actual)
            ) > 5
        ) {
            Serial.println(
                "[MOVE ERROR] Unexpected movement on Torque ON"
            );

            setScsTorque(sid, false);
            return false;
        }
    }

    // Send real position command.
    if (!writeScsGoal(sid, target)) {
        Serial.println(
            "[MOVE ERROR] Goal write not confirmed"
        );
        return false;
    }

    // Verify the goal register.
    uint16_t verifyTarget = 0;

    if (
        !readScsRegister(
            sid, 0x2A, 2, verifyTarget
        ) ||
        verifyTarget != target
    ) {
        Serial.println(
            "[MOVE ERROR] Goal register verification failed"
        );
        return false;
    }

    Serial.printf(
        "[MOVE OK] ID=%u FROM=%u TARGET=%u SPEED=%u\n",
        sid, actual, target, MOVE_SPEED
    );

    // This confirms command acceptance,
    // NOT that the servo has reached the target.
    return true;
}

// ==========================================
// 12. TORQUE OFF
// ==========================================

static void torqueOffOneServo(uint8_t sid) {
    if (!validServoID(sid)) {
        Serial.println(
            "[TORQUE ERROR] Invalid Servo ID"
        );
        return;
    }

    if (setScsTorque(sid, false)) {
        Serial.printf(
            "[TORQUE OFF] Servo ID %u\n",
            sid
        );
    } else {
        Serial.println(
            "[TORQUE ERROR] No confirmed ACK"
        );
    }
}

// ==========================================
// 13. RGB EMOTION COLORS
// ==========================================

static void showLightPreset(
    const String &emotion
) {
    uint8_t r = 0;
    uint8_t g = 150;
    uint8_t b = 255;

    const char *colorName = "CYAN";

    if (emotion == "HAPPY") {
        r = 255; g = 200; b = 0;
        colorName = "YELLOW";

    } else if (emotion == "ANGRY") {
        r = 255; g = 0; b = 0;
        colorName = "RED";

    } else if (emotion == "SURPRISED") {
        r = 255; g = 255; b = 255;
        colorName = "WHITE";

    } else if (emotion == "SAD") {
        r = 0; g = 0; b = 255;
        colorName = "BLUE";

    } else if (emotion == "SLEEPY") {
        r = 150; g = 0; b = 255;
        colorName = "PURPLE";

    } else if (emotion == "STOP") {
        r = 0; g = 0; b = 0;
        colorName = "OFF";
    }

    rgbRing.fill(
        rgbRing.Color(r, g, b)
    );

    rgbRing.show();

    Serial.printf(
        "[RGB] %s R=%u G=%u B=%u | SOLID\n",
        colorName, r, g, b
    );
}

// ==========================================
// 14. EMOTION SIMULATION
// ==========================================

static const Motion *motionForEmotion(
    const String &emotion
) {
    if (emotion == "IDLE") {
        return &IDLE_MOTION;
    }
    if (emotion == "SURPRISED") {
        return &SURPRISED_MOTION;
    }
    if (emotion == "ANGRY") {
        return &ANGRY_MOTION;
    }
    if (emotion == "HAPPY") {
        return &HAPPY_MOTION;
    }
    if (emotion == "SAD") {
        return &SAD_MOTION;
    }
    if (emotion == "SLEEPY") {
        return &SLEEPY_MOTION;
    }

    return nullptr;
}

static void setEmotion(const String &emotion) {
    const Motion *motion = motionForEmotion(emotion);

    if (motion == nullptr) {
        Serial.printf(
            "[ERROR] Unknown emotion: %s\n",
            emotion.c_str()
        );
        return;
    }

    currentEmotion = emotion;
    activeMotion = motion;
    nextFrameIndex = 0;
    nextFrameAt = millis();
    motionActive = true;

    Serial.printf(
        "[EMOTION] %s\n",
        emotion.c_str()
    );

    showLightPreset(emotion);
}

static void sendPose(const Keyframe &frame) {
    Serial.print("[FRAME]");

    for (uint8_t i = 0; i < SERVO_COUNT; ++i) {
        const int value = constrain(
            static_cast<int>(HOME_TICKS[i]) +
                frame.delta[i],
            static_cast<int>(LOW_TICKS[i]),
            static_cast<int>(HIGH_TICKS[i])
        );

        Serial.printf(
            " M%u=%d",
            SERVO_IDS[i],
            value
        );
    }

    Serial.println(
        " | SIMULATION ONLY"
    );
}

static void updateMotion() {
    if (
        !motionActive ||
        activeMotion == nullptr
    ) {
        return;
    }

    if (
        static_cast<int32_t>(
            millis() - nextFrameAt
        ) < 0
    ) {
        return;
    }

    if (nextFrameIndex >= activeMotion->count) {
        motionActive = false;
        Serial.println(
            "[MOTION] Done (simulation)"
        );
        return;
    }

    const Keyframe &frame =
        activeMotion->frames[nextFrameIndex++];

    sendPose(frame);

    nextFrameAt =
        millis() + frame.holdMs;
}

// ==========================================
// 15. STATUS / HELP
// ==========================================

static void printStatus() {
    Serial.println();
    Serial.println(
        "==== ROBOT LAMP STATUS ===="
    );

    Serial.printf(
        "Emotion: %s\n",
        currentEmotion.c_str()
    );

    Serial.println(
        "Emotion movement: SIMULATION ONLY"
    );

    Serial.println(
        "Manual MOVE: ENABLED (limited step)"
    );

    for (uint8_t i = 0; i < SERVO_COUNT; ++i) {
        Serial.printf(
            "Servo ID %u: %s\n",
            SERVO_IDS[i],
            servoOnline[i] ? "FOUND" : "NOT CONFIRMED"
        );
    }

    Serial.println(
        "==========================="
    );
}

static void printHelp() {
    Serial.println();
    Serial.println(
        "=== ROBOT LAMP V1.7 COMMANDS ==="
    );

    Serial.println(
        "SCAN / READPOS / READINFO"
    );

    Serial.println(
        "MOVE 1 400       : Move Servo 1"
    );

    Serial.println(
        "TORQUEOFF 1     : Release Servo 1"
    );

    Serial.println(
        "IDLE / HAPPY / ANGRY / SURPRISED / SAD / SLEEPY"
    );

    Serial.println(
        "STOP / STATUS / HELP"
    );

    Serial.println(
        "Only MOVE commands can move real servos"
    );

    Serial.println(
        "STOP only stops emotion simulation"
    );

    Serial.println(
        "==========================="
    );
}

// ==========================================
// 16. COMMAND HANDLER
// ==========================================

static void handleCommand(String cmd) {
    cmd.trim();
    cmd.toUpperCase();

    if (cmd.isEmpty()) {
        return;
    }

    if (cmd == "SHOCK") {
        cmd = "SURPRISED";
    }

    if (cmd == "SCAN") {
        scanServos();
        return;
    }

    if (cmd == "READPOS") {
        readAllPositions();
        return;
    }

    if (cmd == "READINFO") {
        readAllServoInfo();
        return;
    }

    if (cmd.startsWith("MOVE ")) {
        unsigned int id = 0;
        unsigned int target = 0;
        char extra = '\0';

        Serial.println(
            "===== MOVE RESPONSE ====="
        );

        const int parsed = sscanf(
            cmd.c_str(),
            "MOVE %u %u %c",
            &id, &target, &extra
        );

        if (
            parsed != 2 ||
            id < 1 ||
            id > 4 ||
            target > 1023
        ) {
            Serial.println(
                "[MOVE ERROR] Usage: MOVE 1 400"
            );
        } else {
            moveOneServo(
                static_cast<uint8_t>(id),
                static_cast<uint16_t>(target)
            );
        }

        Serial.println(
            "===== MOVE RESPONSE END ====="
        );
        return;
    }

    if (cmd.startsWith("TORQUEOFF ")) {
        unsigned int id = 0;
        char extra = '\0';

        const int parsed = sscanf(
            cmd.c_str(),
            "TORQUEOFF %u %c",
            &id, &extra
        );

        if (
            parsed == 1 &&
            id >= 1 &&
            id <= 4
        ) {
            torqueOffOneServo(
                static_cast<uint8_t>(id)
            );
        } else {
            Serial.println(
                "[TORQUE ERROR] Usage: TORQUEOFF 1"
            );
        }

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

    if (cmd == "STOP") {
        motionActive = false;
        activeMotion = nullptr;
        currentEmotion = "STOP";
        showLightPreset("STOP");

        Serial.println(
            "[STOP] Simulation stopped; NOT hardware E-stop"
        );
        return;
    }

    if (
        cmd == "DISARM" ||
        cmd == "ARM SERVOS"
    ) {
        Serial.println(
            "[SAFE] Use individual MOVE / TORQUEOFF"
        );
        return;
    }

    setEmotion(cmd);
}

// ==========================================
// 17. READ USB SERIAL
// ==========================================

static void pollUsbCommands() {
    while (Serial.available() > 0) {
        const char c =
            static_cast<char>(Serial.read());

        if (c == '\n' || c == '\r') {
            if (rxLine.length() > 0) {
                handleCommand(rxLine);
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
}

// ==========================================
// 18. SETUP
// ==========================================

void setup() {
    pinMode(2, OUTPUT);
    digitalWrite(2, LOW);

    Serial.begin(USB_BAUD);
    rxLine.reserve(64);

    rgbRing.begin();
    rgbRing.setBrightness(
        ROBOT_LED_BRIGHTNESS
    );
    rgbRing.clear();
    rgbRing.show();

    Serial2.begin(
        SERVO_BAUD,
        SERIAL_8N1,
        SERVO_RX_PIN,
        SERVO_TX_PIN
    );

    delay(300);

    Serial.println();
    Serial.println(
        "=== ROBOT LAMP V1.7 / ESP32 READY ==="
    );

    showLightPreset("IDLE");

    scanServos();
    printHelp();
    printStatus();

    Serial.println(
        "[READY] Manual MOVE available; no startup motion"
    );
}

// ==========================================
// 19. MAIN LOOP
// ==========================================

void loop() {
    pollUsbCommands();

    // Emotion motion is simulation only.
    updateMotion();
}
