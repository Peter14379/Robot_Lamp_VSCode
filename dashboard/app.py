
"""
Robot Lamp Flask Dashboard
ESP32 / SCS15 Servo IDs 1-4
COM3 / 115200 baud

- FK / IK Dashboard
- Software Zero
- Direct MOVE up to 300 ticks
- Independent servo speeds
- Emotion ARM from saved Home
- Camera / Emotion HTTP bridge
- Hormone Monitoring API
"""

import csv
import json
import re
import threading
import time
from pathlib import Path

import serial
from flask import Flask, jsonify, request, send_file

# ==========================================
# CONFIGURATION
# ==========================================

BASE = Path(__file__).resolve().parent

PORT = "COM3"
BAUD = 115200

IDS = (1, 2, 3, 4)
SERVO_HARDWARE_ENABLED = True

ZERO_FILE = BASE / "servo_zeros.json"
HTML_FILE = BASE / "templates" / "index.html"

MARGIN = 20
TOLERANCE = 20

DIRECT_MOVE_MAX_DELTA = 300
DIRECT_MOVE_TIMEOUT = 15.0

SPEED_MIN = 40
SPEED_MAX = 800
EMOTIONS = (
    "IDLE",
    "HAPPY",
    "SURPRISED",
    "SAD",
    "SLEEPY",
)

# CURIO is autonomous behavior, not a fifth emotion.
BEHAVIORS = ("CURIO",)
VALID_COMMANDS = EMOTIONS + BEHAVIORS


# ==========================================
# RL_MOTION_EDITOR_V3
# SAVED MOTION PROFILES
# ==========================================

MOTION_FILE = BASE / "emotion_motion.json"

MOTION_NAMES = (
    "HAPPY",
    "SURPRISED",
    "SAD",
    "SLEEPY",
    "CURIO"
)

MOTION_COMMANDS = EMOTIONS + ("CURIO",)

DEFAULT_MOTIONS = {

    "HAPPY": [
        [-12, 8, 8, 0, 1000],
        [12, -8, -8, 0, 1000],
        [-12, 8, 8, 0, 1000],
        [0, 0, 0, 0, 1200]
    ],

    "SURPRISED": [
        [0, 0, 0, 0, 300],
        [-28, 28, -20, 20, 400],
        [0, 0, 0, 0, 600]
    ],

    "SAD": [
        [0, -25, 15, 0, 1200],
        [0, 0, 0, 0, 800]
    ],

    "SLEEPY": [
        [0, -18, 18, 0, 1600],
        [0, -25, 25, 0, 1600],
        [0, 0, 0, 0, 1200]
    ],

    "CURIO": [
        [0, 0, 0, 0, 600],
        [-30, 0, 0, 0, 1000],
        [0, 0, 0, 0, 350],
        [30, 0, 0, 0, 1000],
        [0, 0, 0, 0, 350],
        [0, -25, 0, 0, 1000],
        [0, 0, 0, 0, 350],
        [0, 25, 0, 0, 1000],
        [0, 0, 0, 0, 950]
    ]
}

motion_lock = threading.RLock()

motion_profiles = {}
motion_synced = set()


def validate_motion(name, frames):

    if (
        name not in MOTION_NAMES
        or not isinstance(frames, list)
        or not 1 <= len(frames) <= 12
    ):
        raise ValueError(
            "Select a valid name and 1..12 frames"
        )

    for row in frames:

        if (
            not isinstance(row, list)
            or len(row) != 5
            or any(
                type(v) is not int
                for v in row
            )
            or any(
                abs(v) > 800
                for v in row[:4]
            )
            or not 300 <= row[4] <= 8000
        ):
            raise ValueError(
                "Frame: four offsets (-800..800), "
                "hold 300..8000 ms"
            )

    return frames


def load_motions():

    try:
        stored = json.loads(
            MOTION_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(stored, dict):
            stored = {}

    except (OSError, ValueError):
        stored = {}

    for name in MOTION_NAMES:

        try:
            motion_profiles[name] = validate_motion(
                name,
                stored.get(
                    name,
                    DEFAULT_MOTIONS[name]
                )
            )

        except ValueError:
            motion_profiles[name] = (
                DEFAULT_MOTIONS[name]
            )


def save_motions():

    temp = MOTION_FILE.with_suffix(
        ".json.tmp"
    )

    temp.write_text(
        json.dumps(
            motion_profiles,
            indent=2
        ),
        encoding="utf-8"
    )

    temp.replace(MOTION_FILE)


load_motions()

app = Flask(__name__)

state_lock = threading.RLock()
serial_lock = threading.Lock()
cancel_event = threading.Event()

port = None

# ==========================================
# SERVO STATES
# ==========================================

servos = {
    i: dict(
        id=i,
        actual=None,
        min=None,
        max=None,
        voltage=None,
        temperature=None,
        online=False,
        zero=None,
        speed=80
    )
    for i in IDS
}

state = dict(
    connected=False,
    info_ready=False,
    busy=False,
    error=None,
    position_at=0.0,
    info_at=0.0,
    emotion_armed=False,
    emotion="IDLE"
)

# ==========================================
# ESP32 RESPONSE PATTERNS
# ==========================================

POS_RE = re.compile(
    r"^\[POSITION\] Servo ID (\d+) = (\d+) ticks$"
)

INFO_RE = re.compile(
    r"^\[INFO\] ID=(\d+) MIN=(\d+) MAX=(\d+) "
    r"VOLTAGE=([\d.]+) TEMP=(\d+)$"
)

MOVE_RE = re.compile(
    r"^\[MOVE OK\] ID=(\d+) FROM=(\d+) "
    r"TARGET=(\d+)(?: SPEED=(\d+))?$"
)

# ==========================================
# LOAD / SAVE SOFTWARE ZERO
# ==========================================

def load_zeros():
    try:
        data = json.loads(
            ZERO_FILE.read_text(encoding="utf-8")
        )

        with state_lock:
            for i in IDS:
                value = data.get(str(i))

                if (
                    type(value) is int
                    and 0 <= value <= 1023
                ):
                    servos[i]["zero"] = value

    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError
    ):
        pass


def save_zeros():
    data = {
        str(i): servos[i]["zero"]
        for i in IDS
        if servos[i]["zero"] is not None
    }

    temp = ZERO_FILE.with_suffix(
        ".json.tmp"
    )

    temp.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

    temp.replace(ZERO_FILE)

# ==========================================
# SERIAL CONNECTION
# ==========================================

def open_port():
    global port

    if port is None or not port.is_open:
        motion_synced.clear()
        port = serial.Serial(
            PORT,
            BAUD,
            timeout=0.12,
            write_timeout=0.8
        )

        # ESP32 may reset when COM opens.
        time.sleep(1.8)


def transact(command, timeout=5):
    """
    Send one command to ESP32.

    Caller MUST hold serial_lock.
    """

    open_port()

    port.reset_input_buffer()

    port.write(
        (command + "\n").encode("ascii")
    )

    port.flush()

    deadline = time.monotonic() + timeout

    lines = []
    seen = set()

    while time.monotonic() < deadline:
        raw = port.readline()

        if not raw:
            continue

        line = raw.decode(
            "utf-8",
            errors="replace"
        ).strip()

        if not line:
            continue

        lines.append(line)
        lines = lines[-80:]

        # Error responses.
        if (
            line.startswith("[ERROR]")
            or line.startswith("[SAFE]")
            or line.startswith("[ARM REJECTED]")
            or line.startswith("[MOVE REJECT]")
            or line.startswith("[MOVE ERROR]")
            or line.startswith("[EMOTION ERROR]")
        ):
            if command not in (
                "READPOS",
                "READINFO"
            ):
                raise RuntimeError(line)

        # READPOS.
        if command == "READPOS":
            match = POS_RE.fullmatch(line)

            if match and int(match[1]) in IDS:
                seen.add(int(match[1]))

            if len(seen) == len(IDS):
                return lines

        # READINFO.
        elif command == "READINFO":
            match = INFO_RE.fullmatch(line)

            if match and int(match[1]) in IDS:
                seen.add(int(match[1]))

            if len(seen) == len(IDS):
                return lines

        # MOVE.
        elif command.startswith("MOVE "):
            if MOVE_RE.fullmatch(line):
                return lines

        # SPEED.
        elif command.startswith("SPEED "):
            if line.startswith("[SPEED OK]"):
                return lines

        # RL_HORMONE_CPG_V1
        elif command.startswith("CPG H "):
            if line.startswith("[HORMONE OK]"):
                return lines

        # ARM with Dashboard Home.
        elif command.startswith("ARM HOME "):
            if line.startswith(
                "[EMOTION] ARMED HOME"
            ):
                return lines

        # DISARM.
        elif command == "DISARM":
            if line == "[EMOTION] DISARMED":
                return lines

        # EMOTION / CURIO.
        elif command.startswith(("KFB ", "KFF ", "KFC ")):
            if line.startswith("[KF OK]"):
                return lines

        elif command in MOTION_COMMANDS:
            if line.startswith(
                f"[EMOTION] {command} |"
            ):
                return lines

        elif command == "STATUS":
            if line.startswith("[STATUS]"):
                return lines

    raise TimeoutError(
        f"{command}: no complete response; "
        f"{lines[-8:]}"
    )

# ==========================================
# READ ACTUAL POSITIONS
# ==========================================

def read_positions():
    lines = transact("READPOS", 8)

    found = {
        int(match[1]): int(match[2])
        for line in lines
        if (match := POS_RE.fullmatch(line))
    }

    if any(i not in found for i in IDS):
        raise RuntimeError(
            "Missing position feedback"
        )

    with state_lock:
        for i in IDS:
            servos[i]["actual"] = found[i]
            servos[i]["online"] = True

        state["connected"] = True
        state["position_at"] = time.monotonic()

# ==========================================
# READ SERVO INFORMATION
# ==========================================

def read_info():
    lines = transact("READINFO", 15)

    found = {
        int(match[1]): (
            int(match[2]),
            int(match[3]),
            float(match[4]),
            int(match[5])
        )
        for line in lines
        if (match := INFO_RE.fullmatch(line))
    }

    if any(i not in found for i in IDS):
        raise RuntimeError(
            "Missing servo information"
        )

    with state_lock:
        for i in IDS:
            low, high, voltage, temperature = found[i]

            servos[i].update(
                min=low,
                max=high,
                voltage=voltage,
                temperature=temperature
            )

        state["info_ready"] = True
        state["info_at"] = time.monotonic()

# ==========================================
# REFRESH SERVO
# ==========================================

def refresh(force_info=False):
    read_positions()

    if (
        force_info
        or time.monotonic()
        - state["info_at"] > 30
    ):
        read_info()

    with state_lock:
        state["error"] = None

# ==========================================
# VALIDATE FEEDBACK
# ==========================================

def checked_servo(i, target=None):
    with state_lock:
        s = dict(servos[i])

        ready = (
            state["connected"]
            and state["info_ready"]
            and s["online"]
        )

        fresh = (
            time.monotonic()
            - state["position_at"] < 8
            and time.monotonic()
            - state["info_at"] < 70
        )

    if not ready or not fresh:
        raise ValueError(
            f"Servo {i}: feedback missing/stale"
        )

    pos = s["actual"]
    low = s["min"]
    high = s["max"]

    if (
        not all(
            type(v) is int
            for v in (pos, low, high)
        )
        or not (
            0 <= low <= pos <= high <= 1023
        )
    ):
        raise ValueError(
            f"Servo {i}: invalid ticks/limits"
        )

    if (
        s["voltage"] is None
        or not 6.5 <= s["voltage"] <= 8.4
    ):
        raise ValueError(
            f"Servo {i}: voltage out of range"
        )

    if (
        s["temperature"] is None
        or s["temperature"] >= 55
    ):
        raise ValueError(
            f"Servo {i}: temperature too high"
        )

    if target is not None:
        if not (
            low + MARGIN
            <= target
            <= high - MARGIN
        ):
            raise ValueError(
                f"Servo {i}: target must be "
                f"{low+MARGIN}..{high-MARGIN}"
            )

    return s

# ==========================================
# BACKGROUND POLLING
# ==========================================

def background_poll():
    global port

    while True:
        if not SERVO_HARDWARE_ENABLED:
            time.sleep(2)
            continue

        try:
            with serial_lock:
                refresh()

        except Exception as exc:
            with state_lock:
                state.update(
                    connected=False,
                    info_ready=False,
                    position_at=0.0,
                    info_at=0.0,
                    error=str(exc),
                    emotion_armed=False
                )

                for i in IDS:
                    servos[i]["online"] = False

            with serial_lock:
                try:
                    if port is not None:
                        port.close()
                except Exception:
                    pass

                port = None

        time.sleep(2)

# ==========================================
# REAL SERVO MOVE
# ==========================================

def move_absolute(
    i,
    target,
    speed=80
):
    """
    Direct movement:
    - One MOVE command.
    - Maximum 300 ticks.
    - Verify actual feedback.
    - No automatic intermediate moves.
    """

    if cancel_event.is_set():
        raise RuntimeError(
            "Cancelled before MOVE"
        )

    refresh(force_info=True)

    s = checked_servo(i, target)
    before = s["actual"]

    distance = abs(target - before)

    if distance > DIRECT_MOVE_MAX_DELTA:
        raise ValueError(
            f"Servo {i}: {distance} ticks "
            f"exceeds {DIRECT_MOVE_MAX_DELTA}; "
            "no command sent"
        )

    if distance == 0:
        return dict(
            id=i,
            before=before,
            target=target,
            actual=before,
            reached=True,
            firmware_speed=speed
        )

    if cancel_event.is_set():
        raise RuntimeError(
            "Cancelled before MOVE"
        )

    # MCU disarms emotion before manual MOVE.
    with state_lock:
        state["emotion_armed"] = False

    lines = transact(
        f"MOVE {i} {target} {speed}",
        8
    )

    match = next(
        (
            match
            for line in lines
            if (match := MOVE_RE.fullmatch(line))
        ),
        None
    )

    if (
        not match
        or int(match[1]) != i
        or int(match[3]) != target
    ):
        raise RuntimeError(
            f"Unexpected MOVE response: {lines[-8:]}"
        )

    firmware_speed = (
        int(match[4])
        if match[4]
        else speed
    )

    deadline = (
        time.monotonic()
        + DIRECT_MOVE_TIMEOUT
    )

    actual = before

    while time.monotonic() < deadline:
        time.sleep(0.25)

        read_positions()

        actual = checked_servo(i)["actual"]

        if abs(actual - target) <= TOLERANCE:
            return dict(
                id=i,
                before=before,
                target=target,
                actual=actual,
                reached=True,
                firmware_speed=firmware_speed
            )

    raise TimeoutError(
        f"Servo {i} target {target}, "
        f"last actual={actual}; "
        "command sent only once"
    )

# ==========================================
# EMOTION SPEEDS
# ==========================================


# ==========================================
# PERSISTENT MOTION EDITOR ROUTES
# ==========================================

def motion_safe_at_home(name, frames):

    # Check saved Home and hardware limits.
    with state_lock:

        for i in IDS:

            s = servos[i]

            if all(
                type(s.get(k)) is int
                for k in ("zero", "min", "max")
            ):

                for row in frames:

                    target = (
                        s["zero"] +
                        row[i - 1]
                    )

                    if not (
                        s["min"] + MARGIN
                        <= target
                        <= s["max"] - MARGIN
                    ):
                        raise ValueError(
                            f"{name} Servo {i}: "
                            f"target {target} "
                            "exceeds safe limit"
                        )


def sync_motion(name):

    # Caller holds serial_lock.
    # No servo movement during configuration.

    if name in motion_synced:
        return

    with motion_lock:
        frames = [
            row[:]
            for row in motion_profiles[name]
        ]

    motion_safe_at_home(
        name,
        frames
    )

    transact(
        f"KFB {name} {len(frames)}",
        5
    )

    for index, frame in enumerate(frames):

        transact(
            "KFF "
            + name
            + " "
            + str(index)
            + " "
            + " ".join(map(str, frame)),
            5
        )

    transact(
        f"KFC {name}",
        5
    )

    motion_synced.add(name)


@app.get("/api/emotion/motions")
def api_motion_get():

    with motion_lock:

        return jsonify(
            ok=True,
            profiles=motion_profiles,
            names=MOTION_NAMES
        )


@app.put("/api/emotion/motions/<name>")
def api_motion_put(name):

    data = request.get_json(
        silent=True
    ) or {}

    try:
        frames = validate_motion(
            name,
            data.get("frames")
        )

        motion_safe_at_home(
            name,
            frames
        )

        with motion_lock:

            motion_profiles[name] = [
                row[:]
                for row in frames
            ]

            save_motions()
            motion_synced.discard(name)

        # SAVE never starts servo movement.

        with state_lock:
            can_sync = (
                state["connected"]
                and not state["busy"]
            )

        synced = False

        note = (
            "Saved on PC; will upload "
            "at next ARM or TEST"
        )

        if can_sync:

            try:
                with serial_lock:
                    sync_motion(name)

                synced = True

                note = (
                    "Saved on PC and uploaded "
                    "to ESP32 (no movement)"
                )

            except Exception as exc:

                note = (
                    "Saved on PC; "
                    "upload pending: "
                    + str(exc)
                )

        return jsonify(
            ok=True,
            saved=True,
            applied=synced,
            note=note
        )

    except (ValueError, TypeError) as exc:

        return jsonify(
            ok=False,
            error=str(exc)
        ), 400


# ==========================================
# RL_EMOTION_SPEED_V4
# PERSISTENT SPEED PER EMOTION
# ==========================================

EMOTION_SPEED_FILE = (
    BASE / "emotion_speeds.json"
)

SPEED_PROFILE_NAMES = (
    "HAPPY",
    "SURPRISED",
    "SAD",
    "SLEEPY",
    "CURIO",
)

SPEED_PROFILE_DEFAULTS = {
    "HAPPY": {
        "1": 85, "2": 85,
        "3": 80, "4": 75
    },

    "SURPRISED": {
        "1": 100, "2": 100,
        "3": 90, "4": 85
    },

    "SAD": {
        "1": 60, "2": 55,
        "3": 55, "4": 55
    },

    "SLEEPY": {
        "1": 40, "2": 40,
        "3": 40, "4": 40
    },

    "CURIO": {
        "1": 65, "2": 60,
        "3": 50, "4": 50
    },
}

emotion_speed_lock = threading.RLock()


def validate_emotion_speeds(speeds):

    if not isinstance(speeds, dict):
        raise ValueError(
            "speeds must be an object"
        )

    result = {}

    for sid in IDS:

        value = speeds.get(str(sid))

        if (
            type(value) is not int
            or not SPEED_MIN <= value <= SPEED_MAX
        ):
            raise ValueError(
                f"Servo {sid}: speed must be "
                f"{SPEED_MIN}..{SPEED_MAX} RAW"
            )

        result[str(sid)] = value

    return result


def load_emotion_speeds():

    try:
        stored = json.loads(
            EMOTION_SPEED_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(stored, dict):
            stored = {}

    except (OSError, ValueError):
        stored = {}

    result = {}

    for name in SPEED_PROFILE_NAMES:

        try:
            result[name] = validate_emotion_speeds(
                stored.get(
                    name,
                    SPEED_PROFILE_DEFAULTS[name]
                )
            )

        except ValueError:
            result[name] = (
                SPEED_PROFILE_DEFAULTS[name].copy()
            )

    return result


emotion_speed_profiles = load_emotion_speeds()


@app.get("/api/emotion/profile-speeds")
def api_get_emotion_profile_speeds():

    with emotion_speed_lock:

        return jsonify(
            ok=True,
            profiles={
                name: dict(values)
                for name, values
                in emotion_speed_profiles.items()
            }
        )


@app.put("/api/emotion/profile-speeds/<name>")
def api_put_emotion_profile_speeds(name):

    if name not in SPEED_PROFILE_NAMES:
        return jsonify(
            ok=False,
            error="Unknown profile"
        ), 404

    try:
        payload = request.get_json(
            silent=True
        ) or {}

        values = validate_emotion_speeds(
            payload.get("speeds")
        )

        with emotion_speed_lock:

            updated = {
                key: dict(value)
                for key, value
                in emotion_speed_profiles.items()
            }

            updated[name] = values

            temp = EMOTION_SPEED_FILE.with_suffix(
                ".json.tmp"
            )

            temp.write_text(
                json.dumps(
                    updated,
                    indent=2
                ),
                encoding="utf-8"
            )

            temp.replace(
                EMOTION_SPEED_FILE
            )

            emotion_speed_profiles.clear()
            emotion_speed_profiles.update(
                updated
            )

        return jsonify(
            ok=True,
            name=name,
            speeds=values,
            note=(
                "Saved permanently. "
                "Applied on next emotion command."
            )
        )

    except (ValueError, TypeError) as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 400

    except OSError as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 500


def parse_speeds(payload):
    values = payload.get("speeds")

    if not isinstance(values, dict):
        raise ValueError(
            "speeds must be an object with 1..4"
        )

    speeds = {}

    for i in IDS:
        v = values.get(
            str(i),
            values.get(i)
        )

        if (
            type(v) is not int
            or not SPEED_MIN <= v <= SPEED_MAX
        ):
            raise ValueError(
                f"Servo {i}: speed must be "
                f"{SPEED_MIN}..{SPEED_MAX} RAW"
            )

        speeds[i] = v

    return speeds


def apply_speeds(speeds):
    """
    Caller holds serial_lock.
    Each Servo has its own emotion speed.
    """

    for i in IDS:
        transact(
            f"SPEED {i} {speeds[i]}",
            4
        )

        with state_lock:
            servos[i]["speed"] = speeds[i]

# ==========================================
# FLASK ROUTES
# ==========================================

@app.after_request
def no_cache(response):
    response.headers[
        "Cache-Control"
    ] = "no-store"

    return response


@app.get("/")
def home():
    return send_file(HTML_FILE)

# ==========================================
# STATUS API
# ==========================================

@app.get("/api/status")
def api_status():
    with state_lock:
        return jsonify(
            ok=True,
            **{
                **state,
                "servos": [
                    dict(servos[i])
                    for i in IDS
                ]
            }
        )

# ==========================================
# DIAGNOSTICS API
# ==========================================

@app.get("/api/diagnostics")
def api_diagnostics():
    with state_lock:
        return jsonify(
            ok=True,
            port=PORT,
            baud=BAUD,
            serial_open=bool(
                port and port.is_open
            ),
            last_error=state["error"],
            servo_online={
                str(i): servos[i]["online"]
                for i in IDS
            },
            connected=state["connected"],
            emotion_armed=state["emotion_armed"]
        )

# ==========================================
# REFRESH API
# ==========================================

@app.post("/api/refresh")
def api_refresh():
    try:
        with serial_lock:
            refresh(force_info=True)

        return api_status()

    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)

        return jsonify(
            ok=False,
            error=str(exc)
        ), 503

# ==========================================
# SOFTWARE SETZERO API
# ==========================================

@app.post("/api/servo/<int:sid>/zero")
def api_zero(sid):
    if sid not in IDS:
        return jsonify(
            ok=False,
            error="Unknown servo"
        ), 404

    if request.get_json(silent=True) != {
        "confirm": True
    }:
        return jsonify(
            ok=False,
            error="Confirmation missing"
        ), 400

    with state_lock:
        if (
            state["busy"]
            or state["emotion_armed"]
        ):
            return jsonify(
                ok=False,
                error="DISARM before SETZERO"
            ), 409

        state["busy"] = True

    try:
        with serial_lock:
            refresh(force_info=True)

            s = checked_servo(sid)

            with state_lock:
                servos[sid]["zero"] = s["actual"]
                save_zeros()

        return jsonify(
            ok=True,
            zero=s["actual"]
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 409

    finally:
        with state_lock:
            state["busy"] = False

# ==========================================
# MANUAL MOVE API
# ==========================================

@app.post("/api/servo/<int:sid>/move")
def api_move(sid):
    if sid not in IDS:
        return jsonify(
            ok=False,
            error="Unknown servo"
        ), 404

    data = request.get_json(
        silent=True
    ) or {}

    target = data.get("target")
    speed = data.get("speed", 80)

    if (
        type(target) is not int
        or not 0 <= target <= 1023
    ):
        return jsonify(
            ok=False,
            error="Target must be integer 0..1023"
        ), 400

    if (
        type(speed) is not int
        or not SPEED_MIN <= speed <= SPEED_MAX
    ):
        return jsonify(
            ok=False,
            error="Speed must be integer 40..800"
        ), 400

    with state_lock:
        if state["busy"]:
            return jsonify(
                ok=False,
                error="Another command running"
            ), 409

        state["busy"] = True

    cancel_event.clear()

    try:
        with serial_lock:
            result = move_absolute(
                sid, target, speed
            )

        return jsonify(
            ok=True,
            **result
        )

    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)

        return jsonify(
            ok=False,
            error=str(exc),
            note="Refresh actual before retrying"
        ), 409

    finally:
        with state_lock:
            state["busy"] = False

# ==========================================
# CANCEL API
# ==========================================

@app.post("/api/cancel")
def api_cancel():
    cancel_event.set()

    return jsonify(
        ok=True,
        message=(
            "Cancel pending steps only; "
            "NOT hardware E-stop"
        )
    )

# ==========================================
# EMOTION SPEED API
# ==========================================

@app.post("/api/emotion/speed")
def api_emotion_speed():
    try:
        speeds = parse_speeds(
            request.get_json(silent=True) or {}
        )

    except ValueError as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 400

    with state_lock:
        if state["busy"]:
            return jsonify(
                ok=False,
                error="System busy"
            ), 409

        state["busy"] = True

    try:
        with serial_lock:
            apply_speeds(speeds)

        return jsonify(
            ok=True,
            speeds=speeds
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 503

    finally:
        with state_lock:
            state["busy"] = False

# ==========================================
# EMOTION ARM USING SAVED HOME
# ==========================================

@app.post("/api/emotion/arm")
def api_emotion_arm():
    """
    ARM Servo 1-4 using saved Software Zero.
    ARM does not move motors.
    """

    try:
        speeds = parse_speeds(
            request.get_json(silent=True) or {}
        )

    except ValueError as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 400

    with state_lock:
        if state["busy"]:
            return jsonify(
                ok=False,
                error="System busy"
            ), 409

        state["busy"] = True

    try:
        with serial_lock:
            refresh(force_info=True)

            zeros = []

            for i in IDS:
                s = checked_servo(i)
                z = s["zero"]

                if type(z) is not int:
                    raise ValueError(
                        f"SETZERO Servo {i} first"
                    )

                if not (
                    s["min"] + MARGIN + 32
                    <= z
                    <= s["max"] - MARGIN - 32
                ):
                    raise ValueError(
                        f"Servo {i} home too close "
                        "to hardware limit"
                    )

                if abs(s["actual"] - z) > TOLERANCE:
                    raise ValueError(
                        f"Servo {i} not at Home: "
                        f"actual {s['actual']}, "
                        f"home {z}; GO ZERO first"
                    )

                zeros.append(z)

            # Update independent servo speeds.
            for name in MOTION_NAMES:
                sync_motion(name)

            apply_speeds(speeds)

            # Use saved zeros.
            transact(
                "ARM HOME " +
                " ".join(map(str, zeros)),
                15
            )

        with state_lock:
            state["emotion_armed"] = True

        return jsonify(
            ok=True,
            armed=True,
            home=zeros,
            speeds=speeds
        )

    except Exception as exc:
        with state_lock:
            state["emotion_armed"] = False
            state["error"] = str(exc)

        return jsonify(
            ok=False,
            error=str(exc)
        ), 409

    finally:
        with state_lock:
            state["busy"] = False

# ==========================================
# EMOTION DISARM API
# ==========================================

@app.post("/api/emotion/disarm")
def api_emotion_disarm():
    with state_lock:
        if state["busy"]:
            return jsonify(
                ok=False,
                error="System busy"
            ), 409

        state["busy"] = True

    try:
        with serial_lock:
            transact("DISARM", 4)

        with state_lock:
            state["emotion_armed"] = False

        return jsonify(
            ok=True,
            armed=False
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 503

    finally:
        with state_lock:
            state["busy"] = False

# ==========================================
# EMOTION BRIDGE
# ==========================================


# RL_HORMONE_CPG_V1
# Hormone updates are independent of emotion changes.


# RL_HORMONE_SPEED_FILTER_V1
# Filter outgoing motor commands only.
# Original hormone production/decay is unchanged.

HORMONE_FILTER_RISE_TAU = 1.0
HORMONE_FILTER_FALL_TAU = 1.4
HORMONE_FILTER_SLEW_PER_SECOND = 0.35


@app.post("/api/hormone/drive")
def api_hormone_drive():
    payload = request.get_json(silent=True) or {}
    value = payload.get("hormone")

    try:
        if type(value) not in (int, float):
            raise ValueError("Invalid hormone")

        h = float(value)

        if not 0.0 <= h <= 1.0:
            raise ValueError("Hormone must be 0..1")

    except (ValueError, TypeError) as exc:
        return jsonify(
            ok=False, error=str(exc)
        ), 400

    now = time.monotonic()
    target_raw = round(40 + 460 * h)

    with state_lock:
        armed = bool(state["emotion_armed"])
        busy = bool(state["busy"])

        previous_at = state.get(
            "hormone_filter_at"
        )
        previous = float(
            state.get("hormone_filtered_value", 0.0)
        )

        # Start slowly after first sample
        # or after an input interruption.
        if (
            previous_at is None
            or now - previous_at > 3.0
        ):
            previous = 0.0
            dt = 0.8
        else:
            dt = max(
                0.0,
                min(now - previous_at, 2.0)
            )

        # Separate smoothing for rising
        # and falling hormone.
        tau = (
            HORMONE_FILTER_RISE_TAU
            if h >= previous
            else HORMONE_FILTER_FALL_TAU
        )

        alpha = (
            dt / (tau + dt)
            if dt > 0.0 else 0.0
        )

        desired = previous + alpha * (
            h - previous
        )

        # Limit sudden changes in
        # the motor hormone command.
        max_delta = (
            HORMONE_FILTER_SLEW_PER_SECOND * dt
        )

        filtered = previous + max(
            -max_delta,
            min(
                max_delta,
                desired - previous
            )
        )

        filtered = max(
            0.0,
            min(1.0, filtered)
        )

        # Do not retain previous speed
        # when the system is disarmed.
        if not armed:
            filtered = 0.0

        filtered_raw = round(
            40 + 460 * filtered
        )

        # Keep the original hormone value
        # for monitoring and comparison.
        state["hormone_value"] = round(h, 4)
        state["hormone_speed_raw"] = target_raw
        state["hormone_filtered_value"] = round(
            filtered, 4
        )
        state["hormone_filtered_speed_raw"] = (
            filtered_raw
        )
        state["hormone_filter_at"] = now
        state["hormone_seen_at"] = now

    response = dict(
        ok=True,
        hormone=round(h, 4),
        speed_raw=target_raw,
        target_speed_raw=target_raw,
        filtered_hormone=round(filtered, 4),
        filtered_speed_raw=filtered_raw,
        armed=armed
    )

    # RL_EMOTION_PROFILE_SPEED_ONLY
    # Hormone is for monitoring and Emotion Brain.
    # Never use Hormone to command motor speed.
    return jsonify(
        **response,
        applied=False,
        reason="EMOTION_PROFILE_SPEED_ONLY"
    )

@app.get("/api/hormone/drive")
def api_hormone_drive_status():
    with state_lock:
        h = state.get(
            "hormone_value", 0.0
        )
        target = state.get(
            "hormone_speed_raw", 40
        )
        filtered_h = state.get(
            "hormone_filtered_value", 0.0
        )
        filtered_raw = state.get(
            "hormone_filtered_speed_raw", 40
        )
        seen = state.get(
            "hormone_seen_at", 0.0
        )
        armed = state["emotion_armed"]
        applied_at = state.get(
            "hormone_applied_at", 0.0
        )
        last_applied = state.get(
            "hormone_applied_speed_raw"
        )

    now = time.monotonic()

    age = (
        now - seen
        if seen > 0 else None
    )

    applied_fresh = bool(
        armed
        and applied_at
        and (now - applied_at) < 3.0
    )

    return jsonify(
        ok=True,
        hormone=h,
        speed_raw=target,
        target_speed_raw=target,
        filtered_hormone=filtered_h,
        filtered_speed_raw=filtered_raw,
        last_applied_speed_raw=(
            last_applied
            if applied_fresh
            else None
        ),
        armed=armed,
        age_s=(
            round(age, 2)
            if age is not None
            else None
        ),
        data_status=(
            "LIVE"
            if age is not None and age < 3.0
            else "STALE"
        )
    )


@app.post("/api/emotion")
def api_emotion():
    data = request.get_json(
        silent=True
    ) or {}

    e = data.get("emotion", "")

    if (
        type(e) is not str
        or e not in MOTION_COMMANDS
    ):
        return jsonify(
            ok=False,
            error="Unknown emotion or behavior"
        ), 400

    with state_lock:
        if state["busy"]:
            return jsonify(
                ok=False,
                error="System busy"
            ), 409

    try:
        with serial_lock:
            if e in MOTION_NAMES:
                sync_motion(e)

            # RL_EMOTION_SPEED_V4
            if e in SPEED_PROFILE_NAMES:
                with emotion_speed_lock:
                    selected = emotion_speed_profiles[e].copy()
                apply_speeds({
                    sid: selected[str(sid)] for sid in IDS
                })
            transact(e, 6)

        with state_lock:
            state["emotion"] = e

        return jsonify(
            ok=True,
            emotion=e,
            acknowledged=True,
            armed=state["emotion_armed"]
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 503



# RL_CPG_STEP_FIX_V2
@app.get("/api/emotion/cpg-status")
def api_emotion_cpg_status():
    try:
        with serial_lock:
            lines = transact("STATUS", 5)

        return jsonify(
            ok=True,
            status=next(
                line for line in lines
                if line.startswith("[STATUS]")
            )
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 503


@app.get("/api/emotion/capabilities")
def api_emotion_capabilities():
    return jsonify(
        ok=True,
        emotions=EMOTIONS,
        behaviors=BEHAVIORS,
        protocol=(
            "Serial emotion/behavior; "
            "manual ARM HOME required"
        )
    )

# ==========================================
# HORMONE MONITORING API
# READ ONLY
# ==========================================

@app.get("/api/monitor/hormone")
def api_monitor_hormone():
    csv_path = (
        BASE.parent / "emotion_trace.csv"
    )

    if not csv_path.is_file():
        return jsonify(
            ok=False,
            error="emotion_trace.csv not found"
        ), 404

    try:
        latest = None

        with csv_path.open(
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            for row in csv.DictReader(file):
                if row.get("time_s"):
                    latest = row

        if latest is None:
            return jsonify(
                ok=False,
                error="No monitoring data"
            ), 503

        age = max(
            0.0,
            time.time()
            - csv_path.stat().st_mtime
        )

        emotions = (
            "HAPPY",
            "SURPRISED",
            "SAD",
            "SLEEPY"
        )

        return jsonify(
            ok=True,

            data_status=(
                "LIVE"
                if age <= 5.0
                else "STALE_DATA"
            ),
            data_age_s=round(age, 2),

            emotion=latest.get(
                "expression", "IDLE"
            ),
            brain=latest.get(
                "brain_expression", "IDLE"
            ),
            mode=latest.get(
                "behavior_mode", "REST"
            ),
            gesture=latest.get(
                "stable", "IDLE"
            ),

            hormone={
                e: float(latest.get("H_" + e) or 0.0)
                for e in emotions
            },

            receptor={
                e: float(latest.get("R_" + e) or 0.0)
                for e in emotions
            },

            curiosity=float(
                latest["curiosity"]
            ),
            energy=float(
                latest["energy"]
            ),
            arousal=float(
                latest["arousal"]
            ),
            sample_time_s=float(
                latest["time_s"]
            )
        )

    except (
        OSError,
        ValueError,
        KeyError,
        TypeError
    ) as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 503

# ==========================================
# START FLASK SERVER
# ==========================================

if __name__ == "__main__":
    load_zeros()

    if SERVO_HARDWARE_ENABLED:
        threading.Thread(
            target=background_poll,
            daemon=True
        ).start()

    print(
        "================================"
    )
    print("ROBOT LAMP FK / IK DASHBOARD")
    print("ESP32 PORT:", PORT)
    print("BAUD RATE:", BAUD)
    print("SERVO IDS:", IDS)
    print("http://127.0.0.1:5000")
    print(
        "================================"
    )

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True
    )
