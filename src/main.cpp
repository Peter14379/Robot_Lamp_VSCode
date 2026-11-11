
/*
  Robot Lamp V1.8
  ESP32-WROOM-32 / NodeMCU-32S

  RGB: WS2812B GPIO18
  Servo: FEETECH SCS15 IDs 1-4
  Waveshare Bus Servo Adapter (A)
  UART2 RX16 / TX17 / 1000000 baud
  USB Serial 115200 baud

  RAM-only servo commands.
  No automatic servo movement at startup.
*/

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <stdio.h>
#include <math.h>
#include <string.h>

// ===============================
// CONFIGURATION
// ===============================

constexpr uint8_t RGB_PIN = 18;
constexpr uint8_t RGB_COUNT = 12;
constexpr uint8_t LED_BRIGHTNESS = 15;

constexpr uint32_t USB_BAUD = 115200;
constexpr uint32_t SERVO_BAUD = 1000000;

constexpr int SERVO_RX = 16;
constexpr int SERVO_TX = 17;

constexpr uint8_t IDS[4] = {1, 2, 3, 4};

constexpr int SAFE_MARGIN = 20;
constexpr int MAX_MANUAL_STEP = 300;
constexpr int EMOTION_DELTA_LIMIT = 800;
constexpr int EMOTION_ARM_MARGIN = 60;

constexpr uint16_t MIN_SPEED = 40;
constexpr uint16_t MAX_SPEED = 800;
constexpr uint16_t DEFAULT_SPEED = 80;

constexpr float MIN_VOLT = 6.5f;
constexpr float MAX_VOLT = 8.4f;
constexpr int MAX_TEMP_C = 55;

Adafruit_NeoPixel ring(
    RGB_COUNT,
    RGB_PIN,
    NEO_GRB + NEO_KHZ800
);

bool online[4] = {};
uint16_t homeTicks[4] = {};
uint16_t servoSpeed[4] = {80, 80, 80, 80};

uint8_t emotionMask = 0;

String emotion = "IDLE";
String rxLine;

// RL_WAKE_HOME_V1
// SLEEPY -> IDLE HOME -> NEXT EMOTION

enum WakePhase : uint8_t {
    WAKE_NONE = 0,
    WAKE_WAIT_SLEEP = 1,
    WAKE_WAIT_HOME = 2,
    WAKE_HOLD_IDLE = 3
};

WakePhase wakePhase = WAKE_NONE;

String wakePending = "";

bool wakeInternal = false;

uint32_t wakeHomeSince = 0;


// ===============================
// EMOTION KEYFRAMES
// ===============================

struct Frame {
    int16_t delta[4];
    uint16_t holdMs;
};

const Frame idleFrames[] = {
    {{0, 0, 0, 0}, 600}
};

const Frame happyFrames[] = {
    {{-100, 30, 20, 0}, 2000},
    {{100, -30, -20, 0}, 2000},
    {{100, 30, 20, 0}, 2000},
    {{0, 0, 0, 0}, 2000}
};

const Frame surprisedFrames[] = {
    {{0, 0, 0, 0}, 200},
    {{-28, 28, -20, 20}, 400},
    {{0, 0, 0, 0}, 600}
};

const Frame sadFrames[] = {
    {{0, -25, 15, 0}, 1200},
    {{0, 0, 0, 0}, 800}
};

const Frame sleepyFrames[] = {
    {{0, -18, 18, 0}, 1600},
    {{0, -25, 25, 0}, 1600},
    {{0, 0, 0, 0}, 1200}
};

// ===============================
// CURIO - AUTONOMOUS EXPLORATION
// ===============================

// Uses saved Home position.
// Servo 1: assumed left/right axis.
// Servo 2: assumed up/down axis.
// Check actual axis directions before ARM.

const Frame curioFrames[] = {
    {{  0,   0, 0, 0}, 600},  // HOME
    {{-30,   0, 0, 0}, 1000}, // LEFT
    {{  0,   0, 0, 0}, 350},  // CENTER
    {{ 30,   0, 0, 0}, 1000}, // RIGHT
    {{  0,   0, 0, 0}, 350},  // CENTER
    {{  0, -25, 0, 0}, 1000}, // UP
    {{  0,   0, 0, 0}, 350},  // CENTER
    {{  0,  25, 0, 0}, 1000}, // DOWN
    {{  0,   0, 0, 0}, 950}   // HOME
};

const Frame *frames = nullptr;

uint8_t frameCount = 0;
uint8_t frameIndex = 0;

uint32_t nextFrameAt = 0;
bool motionActive = false;


enum class MotionStage : uint8_t {
    Prepare,
    Travelling,
    Holding
};

MotionStage motionStage = MotionStage::Prepare;

int motionTargets[4] = {};
int motionWaypoints[4] = {};

bool motionWaypointActive[4] = {};

uint8_t motionReadFail[4] = {};

uint32_t motionWaypointSince[4] = {};
uint32_t motionLastPoll = 0;
uint32_t motionHoldUntil = 0;

uint16_t motionHoldMs = 0;

// RL_PROFILE_SPEED_SEQUENCER_V1
// CPG selects safe waypoints and keyframes.
// Servo velocity comes from its own RAW speed.

// RL_HORMONE_CPG_V1
// Hormone value is normalized to 0..1.
// 0 is slow, NOT motor stop.

float motorHormone = 0.0f;
uint32_t motorHormoneAt = 0;

float cpgPhase = 0.0f;


// Motor speed now comes ONLY from the selected
// emotion's saved servoSpeed[0..3] values.
uint16_t cpgMotorSpeedRaw(uint8_t index) {
    return servoSpeed[index];
}

// Retained for compatibility; no hormone
// multiplier is used for motor movement.
float cpgHormoneRate() {
    return 1.0f;
}

int cpgStart[4] = {};
int cpgGoal[4] = {};
int cpgLastGoal[4] = {};

int cpgSafeLo[4] = {};
int cpgSafeHi[4] = {};

bool cpgEnabled[4] = {};

uint8_t cpgReadFails[4] = {};

uint32_t cpgStarted = 0;
uint32_t cpgDuration = 0;
uint32_t cpgLastUpdate = 0;
uint32_t cpgLastSafety = 0;

uint8_t cpgStable = 0;
uint8_t cpgLastIncomplete = 1;

// RL_CPG_STEP_FIX_V2
constexpr int CPG_ARRIVAL_TOL = 20;
// RL_CPG_LOOKAHEAD_V2
// Send the next waypoint before reaching
// the current waypoint completely.
constexpr int CPG_LOOKAHEAD_TOL = 35;


int cpgLastActual[4] = {};
char cpgLastError[72] = "NONE";







// ===========================================
// RL_MOTION_EDITOR_V3
// EDITABLE RAM KEYFRAMES
// ===========================================

constexpr uint8_t RL_PROFILE_COUNT = 5;
constexpr uint8_t RL_STEP_MAX = 12;

const char* rlNames[RL_PROFILE_COUNT] = {
    "HAPPY",
    "SURPRISED",
    "SAD",
    "SLEEPY",
    "CURIO"
};

Frame rlFrames
    [RL_PROFILE_COUNT][RL_STEP_MAX] = {};

uint8_t rlCounts[RL_PROFILE_COUNT] = {};
bool rlEnabled[RL_PROFILE_COUNT] = {};

Frame rlStaging[RL_STEP_MAX] = {};

int rlStageProfile = -1;
uint8_t rlStageCount = 0;
uint16_t rlStageBits = 0;


int rlSlot(const String& name) {

    for (
        int i = 0;
        i < RL_PROFILE_COUNT;
        ++i
    ) {
        if (name == rlNames[i]) {
            return i;
        }
    }

    return -1;
}


void rlHandleMotionCommand(
    const String& cmd
) {

    if (motionActive) {
        Serial.println(
            "[ERROR] Finish the current motion first"
        );
        return;
    }

    char name[16] = {};

    unsigned count = 0;
    unsigned idx = 0;
    unsigned hold = 0;

    int d[4] = {};

    int n = 0;

    // =====================================
    // BEGIN PROFILE
    // =====================================

    if (cmd.startsWith("KFB ")) {

        bool ok = sscanf(
            cmd.c_str(),
            "KFB %15s %u %n",
            name,
            &count,
            &n
        ) == 2

        && n > 0
        && cmd.substring(n).isEmpty()
        && rlSlot(String(name)) >= 0
        && count >= 1
        && count <= RL_STEP_MAX;

        if (!ok) {
            Serial.println(
                "[ERROR] KFB name count"
            );
            return;
        }

        rlStageProfile = rlSlot(
            String(name)
        );

        rlStageCount = (uint8_t)count;
        rlStageBits = 0;

        Serial.println("[KF OK] BEGIN");
        return;
    }

    // =====================================
    // RECEIVE ONE KEYFRAME
    // =====================================

    if (cmd.startsWith("KFF ")) {

        bool ok = sscanf(
            cmd.c_str(),

            "KFF %15s %u %d %d %d %d %u %n",

            name,
            &idx,
            &d[0],
            &d[1],
            &d[2],
            &d[3],
            &hold,
            &n
        ) == 7

        && n > 0
        && cmd.substring(n).isEmpty()
        && rlStageProfile >= 0
        && rlSlot(String(name)) == rlStageProfile
        && idx < rlStageCount
        && hold >= 300
        && hold <= 8000;

        if (!ok) {
            Serial.println(
                "[ERROR] KFF name index "
                "d1 d2 d3 d4 hold"
            );
            return;
        }

        for (
            int j = 0;
            j < 4;
            ++j
        ) {
            if (
                abs(d[j]) >
                EMOTION_DELTA_LIMIT
            ) {
                Serial.println(
                    "[ERROR] Keyframe offset "
                    "exceeds limit"
                );
                return;
            }

            rlStaging[idx].delta[j] =
                (int16_t)d[j];
        }

        rlStaging[idx].holdMs =
            (uint16_t)hold;

        rlStageBits |=
            (uint16_t)(1u << idx);

        Serial.println("[KF OK] FRAME");
        return;
    }

    // =====================================
    // COMMIT PROFILE
    // =====================================

    if (cmd.startsWith("KFC ")) {

        bool ok = sscanf(
            cmd.c_str(),
            "KFC %15s %n",
            name,
            &n
        ) == 1

        && n > 0
        && cmd.substring(n).isEmpty()
        && rlStageProfile >= 0
        && rlSlot(String(name)) == rlStageProfile

        && rlStageBits ==
            (uint16_t)(
                (1u << rlStageCount) - 1u
            );

        if (!ok) {
            Serial.println(
                "[ERROR] KFC incomplete keyframes"
            );
            return;
        }

        for (
            int j = 0;
            j < rlStageCount;
            ++j
        ) {
            rlFrames[rlStageProfile][j] =
                rlStaging[j];
        }

        rlCounts[rlStageProfile] =
            rlStageCount;

        rlEnabled[rlStageProfile] = true;

        rlStageProfile = -1;
        rlStageCount = 0;
        rlStageBits = 0;

        Serial.println("[KF OK] COMMIT");
        return;
    }

    Serial.println(
        "[ERROR] Unknown motion "
        "configuration command"
    );
}

// ===============================
// SCS15 COMMUNICATION
// ===============================

bool validID(uint8_t id) {
    return id >= 1 && id <= 4;
}

bool checksumOK(
    const uint8_t *p,
    size_t n
) {
    uint16_t sum = 0;

    for (size_t i = 2; i < n; i++) {
        sum += p[i];
    }

    return (sum & 255) == 255;
}

bool requestServo(
    uint8_t id,
    uint8_t instruction,
    const uint8_t *params,
    uint8_t pLen,
    uint8_t *out,
    uint8_t expectedData
) {
    if (!validID(id) || pLen > 16) {
        return false;
    }

    uint8_t tx[24] = {
        0xFF,
        0xFF,
        id,
        (uint8_t)(pLen + 2),
        instruction
    };

    uint16_t sum =
        id + tx[3] + instruction;

    for (uint8_t i = 0; i < pLen; i++) {
        tx[5 + i] = params[i];
        sum += params[i];
    }

    tx[5 + pLen] = (uint8_t)(~sum);

    const size_t txLen = pLen + 6;

    while (Serial2.available()) {
        Serial2.read();
    }

    Serial2.write(tx, txLen);
    Serial2.flush();

    uint8_t rx[96];
    size_t got = 0;

    uint32_t t0 = millis();

    while (millis() - t0 < 180) {

        while (
            Serial2.available() &&
            got < sizeof(rx)
        ) {
            rx[got++] =
                (uint8_t)Serial2.read();
        }

        for (size_t i = 0; i + 6 <= got; i++) {

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

            const size_t n = len + 4;

            if (!checksumOK(rx + i, n)) {
                continue;
            }

            // Ignore command echo.
            if (
                n == txLen &&
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

            for (
                uint8_t j = 0;
                j < expectedData;
                j++
            ) {
                out[j] = rx[i + 5 + j];
            }

            return true;
        }

        delay(1);
    }

    return false;
}

// ===============================
// PING / READ / WRITE
// ===============================

bool ping(uint8_t id) {
    uint8_t b[4];

    return requestServo(
        id, 0x01, nullptr, 0, b, 0
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
    uint8_t b[4] = {};

    if (!requestServo(
        id, 0x02, p, 2, b, width
    )) {
        return false;
    }

    value = (width == 1)
        ? b[0]
        : (uint16_t)((b[0] << 8) | b[1]);

    return true;
}

// RAM only. No EEPROM writes.
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

    uint8_t p[7] = {reg};
    uint8_t b[4];

    for (uint8_t i = 0; i < width; i++) {
        p[i + 1] = data[i];
    }

    return requestServo(
        id, 0x03, p, width + 1, b, 0
    );
}

bool torqueSet(uint8_t id, bool on) {
    uint8_t b = on ? 1 : 0;
    return writeRAM(id, 0x28, &b, 1);
}

bool setGoal(
    uint8_t id,
    uint16_t ticks,
    uint16_t speed
) {
    if (
        speed < MIN_SPEED ||
        speed > MAX_SPEED
    ) {
        return false;
    }

    uint8_t b[6] = {
        (uint8_t)(ticks >> 8),
        (uint8_t)ticks,
        0,
        0,
        (uint8_t)(speed >> 8),
        (uint8_t)speed
    };

    return writeRAM(id, 0x2A, b, 6);
}

bool readPosition(
    uint8_t id,
    uint16_t &p
) {
    return readReg(id, 0x38, 2, p)
        && p <= 1023;
}

// ===============================
// SCAN / READPOS / READINFO
// ===============================

void scanServos() {
    uint8_t n = 0;

    for (uint8_t id : IDS) {

        bool ok = ping(id);

        if (!ok) {
            delay(30);
            ok = ping(id);
        }

        online[id - 1] = ok;

        Serial.printf(
            "[%s] Servo ID %u\n",
            ok ? "FOUND" : "ERROR",
            id
        );

        if (ok) {
            n++;
        }
    }

    Serial.printf(
        "[SCAN] %u/4 detected\n",
        n
    );
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
                "[ERROR] ID %u READPOS failed\n",
                id
            );
        }
    }
}

void readInfo() {
    for (uint8_t id : IDS) {
        uint16_t lo, hi, v, t;

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
                "[INFO ERROR] Servo ID %u\n",
                id
            );
        }
    }
}

// ===============================
// READ TUNING PARAMETERS
// ===============================

void readServoTuning() {
    Serial.println(
        "=== SCS15 TUNING REPORT ==="
    );

    for (uint8_t id : IDS) {
        uint16_t p, i, d, cw, ccw, pwm;

        if (
            readReg(id, 0x15, 1, p) &&
            readReg(id, 0x16, 1, d) &&
            readReg(id, 0x17, 1, i) &&
            readReg(id, 0x1A, 1, cw) &&
            readReg(id, 0x1B, 1, ccw) &&
            readReg(id, 0x18, 2, pwm)
        ) {
            Serial.printf(
                "[TUNE] ID=%u P=%u I=%u D=%u "
                "CW_DEAD=%u CCW_DEAD=%u MIN_PWM=%u\n",
                id, p, i, d, cw, ccw, pwm
            );
        } else {
            Serial.printf(
                "[TUNE ERROR] ID=%u\n",
                id
            );
        }
    }

    Serial.println(
        "=== END TUNING REPORT ==="
    );
}

// ===============================
// READ SERVO LOADS
// ===============================

void readServoLoads() {
    Serial.println(
        "=== SCS15 SERVO LOAD REPORT ==="
    );

    for (uint8_t id : IDS) {
        uint16_t pos, goal, torque;
        uint16_t rawSpeed = 0;
        uint16_t rawLoad = 0;
        uint16_t moving = 0;
        uint16_t status = 0;

        if (!(
            readPosition(id, pos) &&
            readReg(id, 0x2A, 2, goal) &&
            readReg(id, 0x28, 1, torque)
        )) {
            Serial.printf(
                "[LOAD ERROR] ID=%u\n", id
            );
            continue;
        }

        bool s = readReg(
            id, 0x3A, 2, rawSpeed
        );
        bool l = readReg(
            id, 0x3C, 2, rawLoad
        );
        bool m = readReg(
            id, 0x42, 1, moving
        );
        bool e = readReg(
            id, 0x41, 1, status
        );

        Serial.printf(
            "[LOAD] ID=%u POS=%u GOAL=%u "
            "ERROR=%d TORQUE=%u",
            id, pos, goal,
            (int)goal - (int)pos,
            torque
        );

        if (l) {
            Serial.printf(
                " LOAD=%.1f%% LOAD_DIR=%u",
                (rawLoad & 0x3FF) / 10.0f,
                (rawLoad & 0x400) ? 1u : 0u
            );
        } else {
            Serial.print(
                " LOAD=NA LOAD_DIR=NA"
            );
        }

        if (s) {
            Serial.printf(
                " SPEED_RAW=%u", rawSpeed
            );
        } else {
            Serial.print(" SPEED_RAW=NA");
        }

        if (m) {
            Serial.printf(
                " MOVING=%u", moving
            );
        } else {
            Serial.print(" MOVING=NA");
        }

        if (e) {
            Serial.printf(
                " STATUS=0x%02X", status
            );
        } else {
            Serial.print(" STATUS=NA");
        }

        Serial.println();
    }

    Serial.println(
        "=== END LOAD REPORT ==="
    );
}

// ===============================
// CHECK LIMITS
// ===============================

void checkServoState() {
    for (uint8_t id : IDS) {
        uint16_t p, lo, hi, goal, tq;

        if (
            readPosition(id, p) &&
            readReg(id, 0x09, 2, lo) &&
            readReg(id, 0x0B, 2, hi) &&
            readReg(id, 0x2A, 2, goal) &&
            readReg(id, 0x28, 1, tq)
        ) {
            Serial.printf(
                "[CHECK] ID=%u POS=%u MIN=%u "
                "MAX=%u GOAL=%u TORQUE=%u %s\n",
                id, p, lo, hi, goal, tq,
                (p >= lo && p <= hi)
                    ? "WITHIN_LIMIT"
                    : "OUTSIDE_LIMIT"
            );
        } else {
            Serial.printf(
                "[CHECK ERROR] ID=%u\n", id
            );
        }
    }
}

// ===============================
// SERVO CHECKS
// ===============================

bool servoChecks(
    uint8_t id,
    uint16_t &actual,
    uint16_t &lo,
    uint16_t &hi,
    uint16_t &tq
) {
    uint16_t volt, temp;

    if (
        !readPosition(id, actual) ||
        !readReg(id, 0x09, 2, lo) ||
        !readReg(id, 0x0B, 2, hi) ||
        !readReg(id, 0x3E, 1, volt) ||
        !readReg(id, 0x3F, 1, temp) ||
        !readReg(id, 0x28, 1, tq)
    ) {
        Serial.printf(
            "[SAFE] ID %u: read failed\n",
            id
        );
        return false;
    }

    if (
        lo >= hi ||
        hi > 1023 ||
        actual < lo ||
        actual > hi ||
        hi - lo <= 2 * SAFE_MARGIN ||
        tq > 1 ||
        volt / 10.0f < MIN_VOLT ||
        volt / 10.0f > MAX_VOLT ||
        temp >= MAX_TEMP_C
    ) {
        Serial.printf(
            "[SAFE] ID=%u POS=%u LIMIT=%u..%u "
            "VOLT=%.1f TEMP=%u TORQUE=%u REJECT\n",
            id, actual, lo, hi,
            volt / 10.0f, temp, tq
        );
        return false;
    }

    return true;
}

// ===============================
// MOVE SERVO
// ===============================

bool moveServo(
    uint8_t id,
    uint16_t target,
    uint16_t speed
) {
    uint16_t actual, lo, hi, tq;

    if (
        !validID(id) ||
        !servoChecks(
            id, actual, lo, hi, tq
        )
    ) {
        return false;
    }

    if (
        target < lo + SAFE_MARGIN ||
        target > hi - SAFE_MARGIN ||
        abs(
            (int)target - (int)actual
        ) > MAX_MANUAL_STEP
    ) {
        Serial.printf(
            "[MOVE REJECT] ID=%u FROM=%u "
            "TARGET=%u ALLOWED=%u..%u "
            "MAX_STEP=%d\n",
            id, actual, target,
            lo + SAFE_MARGIN,
            hi - SAFE_MARGIN,
            MAX_MANUAL_STEP
        );
        return false;
    }

    // Hold position before enabling torque.
    if (tq == 0) {

        if (!setGoal(id, actual, speed)) {
            Serial.println(
                "[MOVE ERROR] Hold goal write"
            );
            return false;
        }

        uint16_t check;

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

        uint16_t now;

        if (
            !readPosition(id, now) ||
            abs(
                (int)now - (int)actual
            ) > 5
        ) {
            torqueSet(id, false);

            Serial.println(
                "[MOVE ERROR] Unexpected movement"
            );
            return false;
        }
    }

    if (!setGoal(id, target, speed)) {
        Serial.println(
            "[MOVE ERROR] Goal write failed"
        );
        return false;
    }

    uint16_t written;

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
        "[MOVE OK] ID=%u FROM=%u "
        "TARGET=%u SPEED=%u\n",
        id, actual, target, speed
    );

    // Accepted, not proof of reaching target.
    return true;
}

// ===============================
// ARM / DISARM EMOTION
// ===============================

void disarm() {
    wakePhase = WAKE_NONE;
    wakePending = "";
    wakeInternal = false;

    emotionMask = 0;
    motionActive = false;
    frames = nullptr;

    Serial.println(
        "[EMOTION] DISARMED"
    );
}

// Original manual ARM mode.
bool armEmotion(uint8_t mask) {
    disarm();

    uint16_t bases[4] = {};

    for (uint8_t i = 0; i < 4; i++) {

        if (!(mask & (1u << i))) {
            continue;
        }

        uint16_t pos, lo, hi, tq;

        if (
            !ping(IDS[i]) ||
            !servoChecks(
                IDS[i], pos, lo, hi, tq
            ) ||
            (int)pos <
                (int)lo +
                SAFE_MARGIN +
                EMOTION_ARM_MARGIN ||
            (int)pos >
                (int)hi -
                SAFE_MARGIN -
                EMOTION_ARM_MARGIN
        ) {
            Serial.printf(
                "[ARM REJECTED] ID=%u\n",
                IDS[i]
            );
            return false;
        }

        bases[i] = pos;

        Serial.printf(
            "[ARM CHECK] ID=%u HOME=%u\n",
            IDS[i], pos
        );
    }

    for (uint8_t i = 0; i < 4; i++) {
        homeTicks[i] = bases[i];
    }

    emotionMask = mask;

    Serial.printf(
        "[EMOTION] ARMED mask=0x%02X\n",
        emotionMask
    );

    return true;
}

// ===============================
// ARM DASHBOARD SAVED HOME
// ===============================

bool armDashboardHome(
    const uint16_t zeros[4]
) {
    // ARM does not cause movement.
    disarm();

    for (uint8_t i = 0; i < 4; i++) {
        uint16_t pos = 0;
        uint16_t lo = 0;
        uint16_t hi = 0;
        uint16_t tq = 0;

        uint8_t id = IDS[i];

        bool ok =
            ping(id) &&
            servoChecks(
                id, pos, lo, hi, tq
            );

        const int h = (int)zeros[i];

        if (
            !ok ||
            h < (int)lo +
                SAFE_MARGIN +
                EMOTION_ARM_MARGIN ||
            h > (int)hi -
                SAFE_MARGIN -
                EMOTION_ARM_MARGIN ||
            abs((int)pos - h) > 20
        ) {
            Serial.printf(
                "[ARM REJECTED] ID=%u "
                "ACTUAL=%u HOME=%u\n",
                id, pos, zeros[i]
            );
            return false;
        }
    }

    for (uint8_t i = 0; i < 4; i++) {
        homeTicks[i] = zeros[i];
    }

    emotionMask = 0x0F;

    Serial.println(
        "[EMOTION] ARMED HOME mask=0x0F"
    );

    return true;
}

// ===============================
// RGB EMOTION COLORS
// ===============================

void setColor(const String &e) {
    uint8_t r = 0;
    uint8_t g = 150;
    uint8_t b = 255;

    if (e == "HAPPY") {
        r = 255; g = 200; b = 0;
    } else if (e == "SAD") {
        r = 0; g = 0; b = 255;
    } else if (e == "SURPRISED") {
        r = 255; g = 255; b = 255;
    } else if (e == "SLEEPY") {
        r = 150; g = 0; b = 255;
    } else if (e == "CURIO") {
        r = 35; g = 180; b = 90;
    } else if (e == "STOP") {
        r = g = b = 0;
    }

    ring.fill(
        ring.Color(r, g, b)
    );

    ring.show();
}

// ===============================
// SELECT EMOTION
// ===============================

void setEmotion(String e) {
    if (e == "SHOCK") {
        e = "SURPRISED";
    }

    // RL_WAKE_HOME_V1
    // Keep the next emotion while sleeping.
    if (
        !wakeInternal &&
        (
            emotion == "SLEEPY" ||
            wakePhase != WAKE_NONE
        )
    ) {
        bool valid =
            e == "IDLE" ||
            e == "HAPPY" ||
            e == "SURPRISED" ||
            e == "SAD" ||
            e == "SLEEPY" ||
            e == "CURIO";

        if (!valid) {
            Serial.println(
                "[ERROR] Unknown wake emotion"
            );
            return;
        }

        if (!emotionMask) {
            Serial.println(
                "[ERROR] WAKE requires ARM"
            );
            return;
        }

        if (e != "SLEEPY") {

            if (wakePhase == WAKE_NONE) {
                wakePhase = WAKE_WAIT_SLEEP;
            }

            // Keep the first real wake gesture.
            // An IDLE update must not erase it.
            if (
                wakePending.isEmpty() ||
                wakePending == "IDLE"
            ) {
                wakePending = e;
            }

            Serial.printf(
                "[WAKE] QUEUED=%s\n",
                wakePending.c_str()
            );
        }

        // Acknowledge the queued request.
        // Physical motion starts after waking.
        Serial.printf(
            "[EMOTION] %s | %s\n",
            e.c_str(),
            emotionMask
                ? "REAL"
                : "SIMULATION"
        );

        return;
    }


    // Ignore repeated emotion while moving.
    if (motionActive) {
        if (e == emotion) {
            Serial.printf(
                "[EMOTION] %s | %s\n",
                e.c_str(),
                emotionMask ? "REAL" : "SIMULATION"
            );
        } else {
            Serial.printf(
                "[ERROR] MOTION BUSY STEP=%u EMOTION=%s\n",
                (unsigned)(frameIndex + 1),
                emotion.c_str()
            );
        }
        return;
    }

    if (e == "IDLE") {
        frames = idleFrames;
        frameCount =
            sizeof(idleFrames) / sizeof(Frame);

    } else if (e == "HAPPY") {
        frames = happyFrames;
        frameCount =
            sizeof(happyFrames) / sizeof(Frame);

    } else if (e == "SURPRISED") {
        frames = surprisedFrames;
        frameCount =
            sizeof(surprisedFrames) / sizeof(Frame);

    } else if (e == "SAD") {
        frames = sadFrames;
        frameCount =
            sizeof(sadFrames) / sizeof(Frame);

    } else if (e == "SLEEPY") {
        frames = sleepyFrames;
        frameCount =
            sizeof(sleepyFrames) / sizeof(Frame);

    } else if (e == "CURIO") {
        frames = curioFrames;
        frameCount =
            sizeof(curioFrames) / sizeof(Frame);

    } else {
        Serial.printf(
            "[ERROR] Unknown emotion: %s\n",
            e.c_str()
        );
        return;
    }

    const int custom = rlSlot(e);

    if (
        custom >= 0 &&
        rlEnabled[custom]
    ) {
        frames = rlFrames[custom];
        frameCount = rlCounts[custom];
    }

    emotion = e;
    snprintf(cpgLastError, sizeof(cpgLastError), "NONE");
    frameIndex = 0;
    motionStage = MotionStage::Prepare;
    nextFrameAt = millis();
    motionActive = true;

    Serial.printf(
        "[EMOTION] %s | %s\n",
        e.c_str(),
        emotionMask ? "REAL" : "SIMULATION"
    );

    setColor(e);
}

// ===============================
// UPDATE SERVO EMOTION
// ===============================


// ==========================================
// RL_EMOTION_WAYPOINT_800_V1
// VERIFIED WAYPOINT MOTION
// ==========================================

// Motion Editor: +/-800 relative to Home.
// Each physical command: maximum 60 ticks.
// Hardware limits remain enforced.

constexpr int MOTION_WAYPOINT_TICKS = 60;
constexpr int MOTION_ARRIVAL_TOL = 10;

constexpr uint32_t MOTION_POLL_MS = 30;
constexpr uint32_t MOTION_WAYPOINT_TIMEOUT_MS = 10000;


bool motionServoEnabled(uint8_t i) {

    if (!(emotionMask & (1u << i))) {
        return false;
    }

    if (
        emotion == "CURIO" &&
        i >= 2 &&
        !rlEnabled[rlSlot("CURIO")]
    ) {
        return false;
    }

    return true;
}


void motionFailure(
    uint8_t id,
    const char *why
) {
    snprintf(
        cpgLastError,
        sizeof(cpgLastError),
        "STEP=%u ID=%u REASON=%s",
        (unsigned)(frameIndex + 1),
        (unsigned)id,
        why
    );


    Serial.printf(
        "[EMOTION ERROR] STEP=%u ID=%u REASON=%s\n",
        (unsigned)(frameIndex + 1),
        (unsigned)id,
        why
    );

    // Stops the emotion sequence.
    // Not an electrical emergency stop.
    disarm();
}



void updateMotion() {
    if (!motionActive || !frames) return;

    uint32_t now = millis();

    // Finish the hold time before next frame.
    if (motionStage == MotionStage::Holding) {
        if ((int32_t)(now - motionHoldUntil) < 0) {
            return;
        }

        ++frameIndex;

        if (frameIndex >= frameCount) {
            motionActive = false;
            Serial.println("[MOTION] Done");
        } else {
            motionStage = MotionStage::Prepare;
        }
        return;
    }

    if (frameIndex >= frameCount) {
        motionActive = false;
        Serial.println("[MOTION] Done");
        return;
    }

    const Frame &f = frames[frameIndex];

    // =====================================
    // PREPARE THE NEXT KEYFRAME
    // =====================================
    if (motionStage == MotionStage::Prepare) {

        for (uint8_t i = 0; i < 4; ++i) {
            cpgEnabled[i] = motionServoEnabled(i);
            cpgReadFails[i] = 0;
            motionReadFail[i] = 0;
            motionWaypointActive[i] = false;

            if (!cpgEnabled[i]) continue;

            uint16_t pos, lo, hi, tq;
            const int delta = (int)f.delta[i];
            const int target =
                (int)homeTicks[i] + delta;

            if (
                abs(delta) > EMOTION_DELTA_LIMIT ||
                target < 0 ||
                target > 1023 ||
                !servoChecks(
                    IDS[i], pos, lo, hi, tq
                )
            ) {
                motionFailure(
                    IDS[i], "CPG_PRECHECK"
                );
                return;
            }

            const int safeLo =
                (int)lo + SAFE_MARGIN;

            const int safeHi =
                (int)hi - SAFE_MARGIN;

            if (
                target < safeLo ||
                target > safeHi
            ) {
                motionFailure(
                    IDS[i], "CPG_LIMIT"
                );
                return;
            }

            // Preserve the original torque check.
            if (
                tq == 0 &&
                !moveServo(
                    IDS[i], pos, servoSpeed[i]
                )
            ) {
                motionFailure(
                    IDS[i], "CPG_TORQUE"
                );
                return;
            }

            cpgStart[i] = (int)pos;
            cpgGoal[i] = target;
            cpgLastGoal[i] = (int)pos;
            cpgLastActual[i] = (int)pos;
            cpgSafeLo[i] = safeLo;
            cpgSafeHi[i] = safeHi;

            motionTargets[i] = target;
            motionWaypoints[i] = (int)pos;
            motionWaypointSince[i] = now;
        }

        if (!emotionMask) {
            motionStage = MotionStage::Holding;
            motionHoldUntil = now + f.holdMs;
            Serial.println(
                "[FRAME] SIMULATION ONLY"
            );
            return;
        }

        cpgStable = 0;
        cpgLastIncomplete = 1;
        cpgLastSafety = now;
        motionLastPoll = now - MOTION_POLL_MS;
        motionStage = MotionStage::Travelling;

        Serial.printf(
            "[CPG] STEP=%u/%u "
            "RAW=%u,%u,%u,%u\n",
            (unsigned)(frameIndex + 1),
            (unsigned)frameCount,
            (unsigned)servoSpeed[0],
            (unsigned)servoSpeed[1],
            (unsigned)servoSpeed[2],
            (unsigned)servoSpeed[3]
        );

        return;
    }

    // =====================================
    // CHECK POSITION / NEXT SAFE WAYPOINT
    // =====================================
    if (motionStage != MotionStage::Travelling) {
        return;
    }

    if (
        (uint32_t)(now - motionLastPoll)
        < MOTION_POLL_MS
    ) {
        return;
    }

    motionLastPoll = now;

    const bool safety =
        (uint32_t)(now - cpgLastSafety) >= 800UL;

    bool allArrived = true;

    for (uint8_t i = 0; i < 4; ++i) {

        if (!cpgEnabled[i]) continue;

        uint16_t actual = 0;
        uint16_t lo = 0;
        uint16_t hi = 0;
        uint16_t tq = 0;

        bool ok = safety
            ? servoChecks(
                IDS[i], actual, lo, hi, tq
            )
            : readPosition(
                IDS[i], actual
            );

        if (!ok) {
            allArrived = false;
            cpgLastIncomplete = IDS[i];

            if (++cpgReadFails[i] >= 8) {
                motionFailure(
                    IDS[i], "CPG_FEEDBACK"
                );
                return;
            }
            continue;
        }

        cpgReadFails[i] = 0;
        cpgLastActual[i] = (int)actual;

        if (
            (int)actual < cpgSafeLo[i] ||
            (int)actual > cpgSafeHi[i] ||
            (
                safety &&
                (
                    tq == 0 ||
                    cpgGoal[i] <
                        (int)lo + SAFE_MARGIN ||
                    cpgGoal[i] >
                        (int)hi - SAFE_MARGIN
                )
            )
        ) {
            motionFailure(
                IDS[i], "CPG_SAFETY"
            );
            return;
        }

        // Wait for the current waypoint.
        if (motionWaypointActive[i]) {
            if (
                abs((int)actual - motionWaypoints[i]) > CPG_LOOKAHEAD_TOL
            ) {
                allArrived = false;
                cpgLastIncomplete = IDS[i];

                if (
                    (uint32_t)(
                        now - motionWaypointSince[i]
                    ) > MOTION_WAYPOINT_TIMEOUT_MS
                ) {
                    motionFailure(
                        IDS[i], "CPG_TIMEOUT"
                    );
                }
                continue;
            }

            motionWaypointActive[i] = false;
        }

        const int remaining =
            cpgGoal[i] - (int)actual;

        if (abs(remaining) > CPG_ARRIVAL_TOL) {

            // Only limit target distance,
            // never override motor velocity.
            const int unsent =
                cpgGoal[i] - cpgLastGoal[i];

            // Final target already commanded:
            // wait for actual arrival, no resend.
            if (unsent == 0) {
                allArrived = false;

                if (
                    (uint32_t)(
                        now - motionWaypointSince[i]
                    ) > MOTION_WAYPOINT_TIMEOUT_MS
                ) {
                    motionFailure(
                        IDS[i], "CPG_TIMEOUT"
                    );
                    return;
                }
                continue;
            }

            const int step = constrain(
                unsent,
                -MOTION_WAYPOINT_TICKS,
                MOTION_WAYPOINT_TICKS
            );

            int waypoint = cpgLastGoal[i] + step;

            waypoint = constrain(
                waypoint,
                cpgSafeLo[i],
                cpgSafeHi[i]
            );

            // The servo itself controls velocity
            // using its saved emotion RAW speed.
            if (!setGoal(
                IDS[i],
                (uint16_t)waypoint,
                servoSpeed[i]
            )) {
                motionFailure(
                    IDS[i], "CPG_GOAL_WRITE"
                );
                return;
            }

            motionWaypoints[i] = waypoint;
            motionWaypointActive[i] = true;
            motionWaypointSince[i] = now;
            cpgLastGoal[i] = waypoint;

            allArrived = false;
            cpgLastIncomplete = IDS[i];
        }
    }

    if (safety) {
        cpgLastSafety = millis();
    }

    // =====================================
    // KEYFRAME REACHED -> HOLD -> NEXT
    // =====================================
    if (allArrived) {
        if (++cpgStable >= 2) {

            Serial.printf(
                "[CPG] STEP=%u/%u REACHED\n",
                (unsigned)(frameIndex + 1),
                (unsigned)frameCount
            );

            motionStage = MotionStage::Holding;
            const uint32_t transitionHold =
                (
                    frameIndex + 1 < frameCount &&
                    (
                        emotion == "HAPPY" ||
                        emotion == "SURPRISED" ||
                        emotion == "CURIO"
                    )
                )
                ? 60UL
                : (uint32_t)f.holdMs;

            motionHoldUntil =
                millis() + transitionHold;
            cpgStable = 0;
        }
    } else {
        cpgStable = 0;
    }
}


// ==========================================
// RL_WAKE_HOME_V1
// SLEEPY -> WAKING -> IDLE -> NEXT
// ==========================================

void updateWakeTransition() {

    if (wakePhase == WAKE_NONE) {
        return;
    }

    // No automatic motion when DISARMED.
    if (!emotionMask) {
        wakePhase = WAKE_NONE;
        wakePending = "";
        return;
    }

    // ======================================
    // PHASE 1: WAIT FOR SLEEPY TO FINISH
    // ======================================

    if (wakePhase == WAKE_WAIT_SLEEP) {

        if (motionActive) {
            return;
        }

        wakePhase = WAKE_WAIT_HOME;

        // Bypass only the emotion queue.
        // Do not bypass servo safety checks.
        wakeInternal = true;

        setEmotion("IDLE");

        wakeInternal = false;

        Serial.println(
            "[WAKE] IDLE HOME START"
        );

        return;
    }

    // ======================================
    // PHASE 2: WAIT FOR IDLE / HOME
    // ======================================

    if (wakePhase == WAKE_WAIT_HOME) {

        // CPG must complete all movement.
        if (motionActive) {
            return;
        }

        if (emotion != "IDLE") {

            Serial.println(
                "[WAKE ERROR] IDLE interrupted"
            );

            disarm();
            return;
        }

        // Confirm Home using actual
        // feedback from each armed servo.
        for (uint8_t i = 0; i < 4; ++i) {

            if (!(emotionMask & (1u << i))) {
                continue;
            }

            uint16_t actual = 0;

            bool ok = false;

            // Retry temporary bus read failure.
            for (uint8_t attempt = 0;
                 attempt < 3;
                 ++attempt) {

                if (
                    readPosition(
                        IDS[i],
                        actual
                    )
                ) {
                    ok = true;
                    break;
                }

                delay(30);
            }

            if (
                !ok ||
                abs(
                    (int)actual -
                    (int)homeTicks[i]
                ) > 18
            ) {

                Serial.printf(
                    "[WAKE ERROR] HOME "
                    "ID=%u ACT=%u HOME=%u\n",
                    (unsigned)IDS[i],
                    (unsigned)actual,
                    (unsigned)homeTicks[i]
                );

                // Cancel the sequence.
                // Do not force a new emotion.
                disarm();
                return;
            }
        }

        // Home reached successfully.
        wakeHomeSince = millis();

        wakePhase = WAKE_HOLD_IDLE;

        Serial.println(
            "[WAKE] HOME VERIFIED; IDLE HOLD"
        );

        return;
    }

    // ======================================
    // PHASE 3: PAUSE AT HOME FOR 1 SECOND
    // ======================================

    if (wakePhase == WAKE_HOLD_IDLE) {

        if (
            (uint32_t)(
                millis() - wakeHomeSince
            ) < 1000UL
        ) {
            return;
        }

        String next = wakePending;

        wakePending = "";
        wakePhase = WAKE_NONE;

        // ==================================
        // PHASE 4: START PENDING EMOTION
        // ==================================

        if (
            next.length() > 0 &&
            next != "IDLE"
        ) {

            Serial.printf(
                "[WAKE] START=%s\n",
                next.c_str()
            );

            setEmotion(next);

        } else {

            Serial.println(
                "[WAKE] READY IDLE"
            );
        }
    }
}


// ===============================
// STATUS / HELP
// ===============================

void printStatus() {

    Serial.printf(
        "[STATUS] Emotion=%s Servo=%s MASK=0x%02X "
        "ACTIVE=%u STEP=%u/%u STAGE=%u "
        "LAST=%s ACT=%d,%d,%d,%d "
        "GOAL=%d,%d,%d,%d\n",
        emotion.c_str(),
        emotionMask ? "REAL" : "SIMULATION",
        (unsigned)emotionMask,
        (unsigned)motionActive,
        (unsigned)(
            frameIndex < frameCount
                ? frameIndex + 1
                : frameCount
        ),
        (unsigned)frameCount,
        (unsigned)static_cast<uint8_t>(motionStage),
        cpgLastError,
        cpgLastActual[0],
        cpgLastActual[1],
        cpgLastActual[2],
        cpgLastActual[3],
        cpgGoal[0],
        cpgGoal[1],
        cpgGoal[2],
        cpgGoal[3]
    );

    for (uint8_t id : IDS) {
        Serial.printf(
            "Servo ID %u: %s "
            "SPEED=%u HOME=%u\n",
            id,
            online[id - 1]
                ? "FOUND" : "NOT CONFIRMED",
            servoSpeed[id - 1],
            homeTicks[id - 1]
        );
    }
}

void printHelp() {
    Serial.println(
        "SCAN / READPOS / READINFO / READLOAD"
    );
    Serial.println(
        "READTUNE / CHECKLIMITS / STATUS / HELP"
    );
    Serial.println(
        "MOVE 1 300 80 (MAX STEP 300 ticks)"
    );
    Serial.println(
        "SPEED 1 80 / SPEED ALL 80 / READSPEED"
    );
    Serial.println(
        "ARM EMOTION 1..4 / ARM EMOTION ALL"
    );
    Serial.println(
        "ARM HOME h1 h2 h3 h4 / DISARM"
    );
    Serial.println(
        "TORQUEOFF 1 / STOP"
    );
    Serial.println(
        "IDLE / HAPPY / SURPRISED / SAD / SLEEPY / CURIO"
    );
}

// ===============================
// SERIAL COMMAND HANDLER
// ===============================

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

    if (cmd == "CHECKLIMITS") {
        checkServoState();
        return;
    }

    if (cmd == "READLOAD") {
        readServoLoads();
        return;
    }

    if (cmd == "READTUNE") {
        readServoTuning();
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
        cmd.startsWith("KFB ") ||
        cmd.startsWith("KFF ") ||
        cmd.startsWith("KFC ")
    ) {
        rlHandleMotionCommand(cmd);
        return;
    }

    if (cmd == "READSPEED") {
        for (uint8_t i = 0; i < 4; i++) {
            Serial.printf(
                "[SPEED] ID=%u SPEED=%u\n",
                IDS[i],
                servoSpeed[i]
            );
        }
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
            "[STOP] Not hardware E-stop"
        );
        return;
    }

    // Original manual ARM commands.
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

    // ARM HOME with Dashboard software-zero.
    if (cmd.startsWith("ARM HOME ")) {
        unsigned int a, b, c, d;
        int n = 0;

        bool valid =
            sscanf(
                cmd.c_str(),
                "ARM HOME %u %u %u %u %n",
                &a, &b, &c, &d, &n
            ) == 4 &&
            n > 0 &&
            cmd.substring(n).isEmpty() &&
            a <= 1023 &&
            b <= 1023 &&
            c <= 1023 &&
            d <= 1023;

        if (valid) {
            uint16_t z[4] = {
                (uint16_t)a,
                (uint16_t)b,
                (uint16_t)c,
                (uint16_t)d
            };

            armDashboardHome(z);
        } else {
            Serial.println(
                "[ERROR] Usage: "
                "ARM HOME h1 h2 h3 h4"
            );
        }

        return;
    }


    // RL_HORMONE_CPG_V1
    if (cmd.startsWith("CPG H ")) {
        unsigned int value = 0;
        int consumed = 0;

        bool valid =
            sscanf(
                cmd.c_str(),
                "CPG H %u %n",
                &value,
                &consumed
            ) == 1 &&
            consumed > 0 &&
            cmd.substring(consumed).isEmpty() &&
            value <= 1000;

        if (!valid) {
            Serial.println(
                "[ERROR] Usage: CPG H 0..1000"
            );
            return;
        }

        motorHormone = value / 1000.0f;
        motorHormoneAt = millis();

        Serial.printf(
            "[HORMONE OK] H=%u RAW=%u\n",
            value,
            (unsigned)cpgMotorSpeedRaw(0)
        );
        return;
    }

    // Independent Servo speeds.
    if (cmd.startsWith("SPEED ")) {
        unsigned int id = 0;
        unsigned int v = 0;
        int n = 0;

        if (cmd.startsWith("SPEED ALL ")) {
            bool valid =
                sscanf(
                    cmd.c_str(),
                    "SPEED ALL %u %n",
                    &v, &n
                ) == 1 &&
                n > 0 &&
                cmd.substring(n).isEmpty() &&
                v >= MIN_SPEED &&
                v <= MAX_SPEED;

            if (valid) {
                for (uint8_t i = 0; i < 4; i++) {
                    servoSpeed[i] = (uint16_t)v;
                }

                Serial.printf(
                    "[SPEED OK] ALL=%u\n", v
                );
            } else {
                Serial.println(
                    "[ERROR] Usage: SPEED ALL 80"
                );
            }

        } else {
            bool valid =
                sscanf(
                    cmd.c_str(),
                    "SPEED %u %u %n",
                    &id, &v, &n
                ) == 2 &&
                n > 0 &&
                cmd.substring(n).isEmpty() &&
                id >= 1 && id <= 4 &&
                v >= MIN_SPEED &&
                v <= MAX_SPEED;

            if (valid) {
                servoSpeed[id - 1] =
                    (uint16_t)v;

                Serial.printf(
                    "[SPEED OK] ID=%u SPEED=%u\n",
                    id, v
                );
            } else {
                Serial.println(
                    "[ERROR] Usage: "
                    "SPEED 1 80 (40..500)"
                );
            }
        }

        return;
    }

    // TORQUE OFF.
    if (cmd.startsWith("TORQUEOFF ")) {
        unsigned int id = 0;
        int n = 0;

        disarm();

        bool valid =
            sscanf(
                cmd.c_str(),
                "TORQUEOFF %u %n",
                &id, &n
            ) == 1 &&
            n > 0 &&
            cmd.substring(n).isEmpty() &&
            id >= 1 &&
            id <= 4;

        if (valid) {
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

    // MANUAL MOVE.
    if (cmd.startsWith("MOVE ")) {
        unsigned int id = 0;
        unsigned int target = 0;
        unsigned int v = DEFAULT_SPEED;

        int n = 0;

        int count = sscanf(
            cmd.c_str(),
            "MOVE %u %u %u %n",
            &id, &target, &v, &n
        );

        bool valid =
            count == 3 &&
            n > 0 &&
            cmd.substring(n).isEmpty();

        // Also support MOVE 1 300.
        if (!valid) {
            n = 0;
            v = DEFAULT_SPEED;

            count = sscanf(
                cmd.c_str(),
                "MOVE %u %u %n",
                &id, &target, &n
            );

            valid =
                count == 2 &&
                n > 0 &&
                cmd.substring(n).isEmpty();
        }

        disarm();

        if (
            valid &&
            id >= 1 &&
            id <= 4 &&
            target <= 1023 &&
            v >= MIN_SPEED &&
            v <= MAX_SPEED
        ) {
            moveServo(
                (uint8_t)id,
                (uint16_t)target,
                (uint16_t)v
            );
        } else {
            Serial.println(
                "[ERROR] Usage: "
                "MOVE 1 300 80 "
                "(speed 40..500)"
            );
        }

        return;
    }

    // Other commands are emotion names.
    setEmotion(cmd);
}

// ===============================
// SETUP
// ===============================

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
        "=== ROBOT LAMP V1.8 / "
        "ESP32 READY ==="
    );

    setColor("IDLE");

    scanServos();
    printHelp();
    printStatus();

    Serial.println(
        "[READY] Simulation until "
        "ARM EMOTION / ARM HOME"
    );
}

// ===============================
// MAIN LOOP
// ===============================

void loop() {
    while (Serial.available()) {
        char c = (char)Serial.read();

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
    updateWakeTransition();
}
