
"""
Robot Lamp Dashboard V1.6
DIRECT MOVE V2.2

ESP32-WROOM-32
FEETECH SCS15 Servo ID 1-4
COM3 / 115200

Features:
- READPOS / READINFO
- DIRECT MOVE
- Software SET ZERO
- Real-time monitoring
- Browser cache disabled

Requires ESP32 firmware with
MOVE_MAX_STEP = 150
"""

import hashlib
import json
import os
import queue
import re
import threading
import time

from datetime import datetime
from pathlib import Path

import serial

from flask import (
    Flask,
    jsonify,
    make_response,
    request
)


# ==========================================
# 1. CONFIGURATION
# ==========================================

BASE = Path(__file__).resolve().parent

INDEX_FILE = (
    BASE / "templates" / "index.html"
)

ZERO_FILE = (
    BASE / "servo_zeros.json"
)

PORT = "COM3"
BAUD = 115200

SERVO_IDS = (1, 2, 3, 4)

POSITION_INTERVAL = 2.0
INFO_INTERVAL = 30.0

POSITION_TIMEOUT = 6.0
INFO_TIMEOUT = 12.0
MOVE_TIMEOUT = 15.0

MAX_ATTEMPTS = 2

SAFE_MARGIN = 20

# DIRECT MOVE maximum displacement
MAX_MOVE_STEP = 150

MIN_VOLTAGE = 6.5
MAX_VOLTAGE = 8.4
MAX_TEMPERATURE = 55

UI_VERSION = "DIRECT MOVE V2.2"

app = Flask(__name__)

state_lock = threading.Lock()

command_queue = queue.Queue(maxsize=1)

move_busy = False


# ==========================================
# 2. RESPONSE PATTERNS
# ==========================================

POSITION_PATTERN = re.compile(
    r"^\[POSITION\] Servo ID "
    r"(\d+) = (\d+) ticks$"
)

INFO_PATTERN = re.compile(
    r"^\[INFO\] ID=(\d+) "
    r"MIN=(\d+) MAX=(\d+) "
    r"VOLTAGE=([\d.]+) TEMP=(\d+)$"
)

MOVE_PATTERN = re.compile(
    r"^\[MOVE OK\] ID=(\d+) "
    r"FROM=(\d+) TARGET=(\d+) "
    r"SPEED=(\d+)$"
)


def now_text():
    return datetime.now().isoformat(
        timespec="seconds"
    )


# ==========================================
# 3. LOAD SAVED SOFTWARE ZERO
# ==========================================

def load_zeros():

    try:
        values = json.loads(
            ZERO_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(values, dict):
            return {}

        return {
            sid: values[str(sid)]
            for sid in SERVO_IDS
            if (
                type(values.get(str(sid))) is int
                and 0 <= values[str(sid)] <= 1023
            )
        }

    except FileNotFoundError:
        return {}

    except (OSError, ValueError, TypeError) as error:
        print("[ZERO LOAD ERROR]", error)
        return {}


saved_zeros = load_zeros()


# ==========================================
# 4. LIVE SERVO STATE
# ==========================================

servo_state = {
    sid: {
        "id": sid,
        "actual": None,
        "min": None,
        "max": None,
        "voltage": None,
        "temperature": None,
        "zero": saved_zeros.get(sid),
        "online": False
    }
    for sid in SERVO_IDS
}

system_state = {
    "connected": False,
    "info_ready": False,
    "last_update": None,
    "last_info_update": None,
    "position_sample_at": None,
    "info_sample_at": None,
    "error": None,
    "info_error": None
}


# ==========================================
# 5. SERIAL RESPONSE COLLECTOR
# ==========================================

def collect_response(
    ser,
    command,
    start_marker,
    end_marker,
    timeout
):

    ser.reset_input_buffer()

    ser.write(
        (command + "\n").encode("ascii")
    )

    ser.flush()

    deadline = time.monotonic() + timeout

    started = False
    lines = []
    observed = []
    pending = bytearray()

    while time.monotonic() < deadline:

        chunk = ser.read(
            ser.in_waiting or 1
        )

        if not chunk:
            continue

        pending.extend(chunk)

        if len(pending) > 8192:
            pending.clear()
            continue

        while b"\n" in pending:

            raw, _, rest = pending.partition(
                b"\n"
            )

            pending = bytearray(rest)

            line = raw.decode(
                "utf-8",
                errors="replace"
            ).strip()

            if not line:
                continue

            observed.append(line)

            if len(observed) > 100:
                observed.pop(0)

            if start_marker in line:
                started = True
                lines = []
                continue

            if not started:
                continue

            if end_marker in line:
                return lines, True

            lines.append(line)

    return (
        lines if started else observed
    ), False


# ==========================================
# 6. PARSE READPOS
# ==========================================

def parse_positions(lines):

    found = {}

    for line in lines:

        match = POSITION_PATTERN.match(line)

        if not match:
            continue

        sid = int(match.group(1))
        actual = int(match.group(2))

        if (
            sid in SERVO_IDS
            and 0 <= actual <= 1023
        ):
            found[sid] = actual

    return found


# ==========================================
# 7. PARSE READINFO
# ==========================================

def parse_info(lines):

    found = {}

    for line in lines:

        match = INFO_PATTERN.match(line)

        if not match:
            continue

        sid = int(match.group(1))
        low = int(match.group(2))
        high = int(match.group(3))

        if (
            sid in SERVO_IDS
            and 0 <= low < high <= 1023
        ):
            found[sid] = {
                "min": low,
                "max": high,
                "voltage": float(match.group(4)),
                "temperature": int(match.group(5))
            }

    return found


# ==========================================
# 8. READ WITH RETRY
# ==========================================

def query_servos(
    ser,
    command,
    start,
    end,
    timeout,
    parser
):

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1
    ):

        lines, complete = collect_response(
            ser,
            command,
            start,
            end,
            timeout
        )

        values = (
            parser(lines) if complete else {}
        )

        if (
            complete
            and all(
                sid in values
                for sid in SERVO_IDS
            )
        ):
            if attempt > 1:
                print(
                    f"[RECOVERED] {command} "
                    f"attempt {attempt}"
                )

            return values

        print(
            f"[READ WARNING] {command} "
            f"attempt={attempt} "
            f"complete={complete}"
        )

        print(
            f"[DEBUG {command}]",
            lines[-20:]
        )

        if attempt < MAX_ATTEMPTS:
            time.sleep(0.4)

    return {}


def read_positions(ser):

    return query_servos(
        ser,
        "READPOS",
        "REAL SERVO POSITIONS (READ ONLY)",
        "READ COMPLETE (NO MOVEMENT)",
        POSITION_TIMEOUT,
        parse_positions
    )


def read_info(ser):

    return query_servos(
        ser,
        "READINFO",
        "SERVO READINFO (READ ONLY)",
        "READINFO COMPLETE (NO MOVEMENT)",
        INFO_TIMEOUT,
        parse_info
    )


# ==========================================
# 9. UPDATE LIVE POSITIONS
# ==========================================

def update_positions(values):

    complete = all(
        sid in values for sid in SERVO_IDS
    )

    with state_lock:

        for sid in SERVO_IDS:
            servo_state[sid]["actual"] = (
                values.get(sid)
            )

            servo_state[sid]["online"] = (
                sid in values
            )

        system_state["connected"] = complete

        system_state["error"] = (
            None if complete
            else "Incomplete READPOS"
        )

        system_state["position_sample_at"] = (
            time.monotonic()
            if complete else None
        )

        if complete:
            system_state["last_update"] = (
                now_text()
            )

    print("[SERVO] Positions:", values)


# ==========================================
# 10. UPDATE SERVO INFO
# ==========================================

def update_info(values):

    complete = all(
        sid in values for sid in SERVO_IDS
    )

    with state_lock:

        for sid in SERVO_IDS:

            for field in (
                "min",
                "max",
                "voltage",
                "temperature"
            ):
                servo_state[sid][field] = (
                    values[sid][field]
                    if sid in values
                    else None
                )

        system_state["info_ready"] = complete

        system_state["info_error"] = (
            None if complete
            else "Incomplete READINFO"
        )

        system_state["info_sample_at"] = (
            time.monotonic()
            if complete else None
        )

        if complete:
            system_state["last_info_update"] = (
                now_text()
            )

    print("[SERVO] Info:", values)


# ==========================================
# 11. DISCONNECT HANDLER
# ==========================================

def set_disconnected(error):

    with state_lock:

        system_state.update({
            "connected": False,
            "info_ready": False,
            "position_sample_at": None,
            "info_sample_at": None,
            "error": str(error),
            "info_error": str(error)
        })

        for sid in SERVO_IDS:
            servo_state[sid]["online"] = False

            for field in (
                "actual",
                "min",
                "max",
                "voltage",
                "temperature"
            ):
                servo_state[sid][field] = None


# ==========================================
# 12. SEND DIRECT MOVE TO ESP32
# ==========================================

def perform_move(ser, sid, target):

    lines, complete = collect_response(
        ser,
        f"MOVE {sid} {target}",
        "===== MOVE RESPONSE =====",
        "===== MOVE RESPONSE END =====",
        MOVE_TIMEOUT
    )

    print("[MOVE RESPONSE]", lines)

    if not complete:
        raise RuntimeError(
            "MOVE response incomplete; "
            "check Actual before retry"
        )

    for line in lines:

        if line.startswith("[MOVE ERROR]"):
            raise RuntimeError(line)

    for line in lines:

        match = MOVE_PATTERN.match(line)

        if (
            match
            and int(match.group(1)) == sid
            and int(match.group(3)) == target
        ):
            return {
                "ok": True,
                "id": sid,
                "from": int(match.group(2)),
                "target": target,
                "speed": int(match.group(4)),
                "message": (
                    "Command accepted; "
                    "verify Actual before retry"
                )
            }

    raise RuntimeError(
        "MOVE not confirmed; "
        "check Actual before retry"
    )


# ==========================================
# 13. PROCESS MOVE QUEUE
# ==========================================

def process_move(ser):

    global move_busy

    try:
        job = command_queue.get_nowait()

    except queue.Empty:
        return False

    try:
        expired = (
            time.monotonic()
            - job["created"] > 30
        )

        if job["cancelled"] or expired:
            job["error"] = (
                "MOVE request expired"
            )

        else:
            job["result"] = perform_move(
                ser,
                job["id"],
                job["target"]
            )

    except Exception as error:
        job["error"] = str(error)

    finally:
        with state_lock:
            move_busy = False

        job["event"].set()
        command_queue.task_done()

    return True


def cancel_queued(reason):

    global move_busy

    while True:

        try:
            job = command_queue.get_nowait()

        except queue.Empty:
            break

        job["cancelled"] = True
        job["error"] = reason
        job["event"].set()

        command_queue.task_done()

    with state_lock:
        move_busy = False


# ==========================================
# 14. BACKGROUND SERIAL WORKER
# ==========================================

def serial_worker():

    while True:

        try:
            print(
                "[SERIAL] Connecting to",
                PORT
            )

            with serial.Serial(
                PORT,
                BAUD,
                timeout=0.2,
                write_timeout=1
            ) as ser:

                print("[SERIAL] Port opened")

                time.sleep(2.5)

                next_info = 0.0
                next_pos = 0.0

                while True:

                    if process_move(ser):
                        next_pos = 0.0

                    now = time.monotonic()

                    if now >= next_info:
                        update_info(
                            read_info(ser)
                        )

                        next_info = (
                            time.monotonic()
                            + INFO_INTERVAL
                        )

                    if time.monotonic() >= next_pos:
                        update_positions(
                            read_positions(ser)
                        )

                        next_pos = (
                            time.monotonic()
                            + POSITION_INTERVAL
                        )

                    time.sleep(0.05)

        except (
            serial.SerialException,
            OSError,
            TimeoutError,
            ValueError
        ) as error:

            print("[SERIAL ERROR]", error)

            set_disconnected(error)

            cancel_queued(
                "ESP32 disconnected"
            )

            time.sleep(3)


# ==========================================
# 15. SAVE SOFTWARE ZERO
# ==========================================

def save_zero_map(values):

    temp = ZERO_FILE.with_name(
        "servo_zeros.json.tmp"
    )

    data = {
        str(sid): values[sid]
        for sid in SERVO_IDS
        if values.get(sid) is not None
    }

    try:
        with temp.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                indent=2
            )

            file.flush()
            os.fsync(file.fileno())

        os.replace(temp, ZERO_FILE)

    finally:
        if temp.exists():
            temp.unlink()


# ==========================================
# 16. DISABLE BROWSER CACHE
# ==========================================

@app.after_request
def disable_browser_cache(response):

    response.headers["Cache-Control"] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    if "ETag" in response.headers:
        del response.headers["ETag"]

    return response


# ==========================================
# 17. DASHBOARD PAGE
# ==========================================

@app.route("/")
def dashboard():

    if not INDEX_FILE.is_file():
        return make_response(
            f"index.html not found: {INDEX_FILE}",
            500
        )

    html = INDEX_FILE.read_text(
        encoding="utf-8-sig"
    )

    # DIRECT MOVE V2.2 ONLY
    if UI_VERSION not in html:
        return make_response(
            "Wrong index.html version.\n"
            f"File: {INDEX_FILE}\n"
            f"Expected {UI_VERSION}",
            503
        )

    response = make_response(html)

    response.headers["Content-Type"] = (
        "text/html; charset=utf-8"
    )

    response.headers["X-Robot-Lamp-UI"] = (
        "DIRECT-MOVE-V2.2"
    )

    return response


# ==========================================
# 18. VERSION API
# ==========================================

@app.route("/api/version")
def api_version():

    content = (
        INDEX_FILE.read_bytes()
        if INDEX_FILE.is_file()
        else b""
    )

    html = content.decode(
        "utf-8-sig",
        errors="replace"
    )

    return jsonify({
        "backend": "Robot Lamp Flask V1.6",
        "ui_expected": UI_VERSION,
        "ui_found": UI_VERSION in html,
        "old_message_found": (
            "AUTO STEP V2.1" in html
            or "ระยะหมุนเกิน 40 ticks" in html
        ),
        "template_path": str(INDEX_FILE),
        "sha256_short": hashlib.sha256(
            content
        ).hexdigest()[:12],
        "server_pid": os.getpid()
    })


# ==========================================
# 19. STATUS API
# ==========================================

@app.route("/api/status")
def api_status():

    with state_lock:

        output = []

        for sid in SERVO_IDS:

            row = dict(servo_state[sid])

            if (
                row["online"]
                and row["actual"] is not None
                and row["zero"] is not None
            ):
                row["relative"] = (
                    row["actual"] - row["zero"]
                )

            else:
                row["relative"] = None

            output.append(row)

        return jsonify({
            "connected": system_state["connected"],
            "info_ready": system_state["info_ready"],
            "move_busy": move_busy,
            "last_update": system_state["last_update"],
            "last_info_update": system_state[
                "last_info_update"
            ],
            "error": system_state["error"],
            "info_error": system_state["info_error"],
            "servos": output
        })


# ==========================================
# 20. DIRECT MOVE API
# ==========================================

@app.route(
    "/api/servo/<int:sid>/move",
    methods=["POST"]
)
def api_move(sid):

    global move_busy

    if sid not in SERVO_IDS:
        return jsonify({
            "ok": False,
            "error": "Invalid Servo ID"
        }), 404

    body = request.get_json(silent=True)

    if (
        not isinstance(body, dict)
        or body.get("confirm") is not True
    ):
        return jsonify({
            "ok": False,
            "error": "Confirmation required"
        }), 400

    target = body.get("target")

    if type(target) is not int:
        return jsonify({
            "ok": False,
            "error": "Target must be an integer"
        }), 400

    with state_lock:

        if move_busy:
            return jsonify({
                "ok": False,
                "error": "Another MOVE in progress"
            }), 409

        servo = servo_state[sid]

        pos_time = system_state[
            "position_sample_at"
        ]

        info_time = system_state[
            "info_sample_at"
        ]

        fresh = (
            system_state["connected"]
            and system_state["info_ready"]
            and servo["online"]
            and pos_time is not None
            and time.monotonic() - pos_time <= 8
            and info_time is not None
            and time.monotonic() - info_time <= 70
        )

        if not fresh:
            return jsonify({
                "ok": False,
                "error": "Servo data unavailable or stale"
            }), 409

        actual = servo["actual"]
        low = servo["min"]
        high = servo["max"]

        if not all(
            type(value) is int
            for value in (actual, low, high)
        ):
            return jsonify({
                "ok": False,
                "error": "Missing position or limits"
            }), 409

        if not (
            0 <= low < high <= 1023
            and low <= actual <= high
            and low + SAFE_MARGIN <= target
            <= high - SAFE_MARGIN
        ):
            return jsonify({
                "ok": False,
                "error": "Target outside safe limits"
            }), 400

        # One direct movement, max 150 ticks
        if abs(actual - target) > MAX_MOVE_STEP:
            return jsonify({
                "ok": False,
                "error": (
                    f"Max {MAX_MOVE_STEP} ticks per MOVE"
                )
            }), 400

        voltage = servo["voltage"]
        temperature = servo["temperature"]

        if not (
            isinstance(voltage, (int, float))
            and MIN_VOLTAGE <= voltage <= MAX_VOLTAGE
            and type(temperature) is int
            and 0 <= temperature < MAX_TEMPERATURE
        ):
            return jsonify({
                "ok": False,
                "error": "Voltage/temperature outside range"
            }), 409

        move_busy = True

    job = {
        "id": sid,
        "target": target,
        "created": time.monotonic(),
        "cancelled": False,
        "event": threading.Event(),
        "result": None,
        "error": None
    }

    try:
        command_queue.put_nowait(job)

    except queue.Full:

        with state_lock:
            move_busy = False

        return jsonify({
            "ok": False,
            "error": "MOVE queue full"
        }), 503

    if not job["event"].wait(timeout=45):

        job["cancelled"] = True

        return jsonify({
            "ok": False,
            "error": (
                "MOVE confirmation timed out; "
                "check Actual before retry"
            )
        }), 504

    if job["error"]:
        return jsonify({
            "ok": False,
            "error": job["error"]
        }), 409

    return jsonify(job["result"])


# ==========================================
# 21. SET ZERO API
# ==========================================

@app.route(
    "/api/servo/<int:sid>/zero",
    methods=["POST"]
)
def api_zero(sid):

    if sid not in SERVO_IDS:
        return jsonify({
            "ok": False,
            "error": "Invalid Servo ID"
        }), 404

    body = request.get_json(silent=True)

    if (
        not isinstance(body, dict)
        or body.get("confirm") is not True
    ):
        return jsonify({
            "ok": False,
            "error": "Confirmation required"
        }), 400

    with state_lock:

        servo = servo_state[sid]

        stamp = system_state[
            "position_sample_at"
        ]

        ready = (
            system_state["connected"]
            and system_state["info_ready"]
            and servo["online"]
            and not move_busy
            and stamp is not None
            and time.monotonic() - stamp <= 8
        )

        if not ready:
            return jsonify({
                "ok": False,
                "error": "Servo unavailable or moving"
            }), 409

        actual = servo["actual"]
        low = servo["min"]
        high = servo["max"]

        if (
            not all(
                type(value) is int
                for value in (actual, low, high)
            )
            or not low <= actual <= high
        ):
            return jsonify({
                "ok": False,
                "error": "Invalid actual position"
            }), 409

        new_zeros = {
            motor_id: servo_state[motor_id]["zero"]
            for motor_id in SERVO_IDS
        }

        new_zeros[sid] = actual

        try:
            save_zero_map(new_zeros)

        except OSError as error:
            print("[ZERO SAVE ERROR]", error)

            return jsonify({
                "ok": False,
                "error": "Could not save Zero"
            }), 500

        servo["zero"] = actual

        print(
            f"[SET ZERO] Servo {sid} "
            f"= {actual} ticks"
        )

        return jsonify({
            "ok": True,
            "id": sid,
            "zero": actual,
            "relative": 0,
            "message": "Software Zero saved"
        })


# ==========================================
# 22. START FLASK
# ==========================================

if __name__ == "__main__":

    print("=" * 50)
    print("ROBOT LAMP DASHBOARD V1.6")
    print("Index file:", INDEX_FILE)

    print(
        "Correct V2.2 UI:",
        INDEX_FILE.is_file()
        and UI_VERSION in INDEX_FILE.read_text(
            encoding="utf-8-sig"
        )
    )

    print(
        "Dashboard: http://127.0.0.1:5000/"
    )

    print(
        "Version: http://127.0.0.1:5000/api/version"
    )

    print("=" * 50)

    worker = threading.Thread(
        target=serial_worker,
        daemon=True
    )

    worker.start()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False,
        threaded=True
    )
