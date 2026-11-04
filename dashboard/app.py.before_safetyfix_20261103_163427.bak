
"""
Robot Lamp FK / IK Dashboard
ESP32 + SCS15 Servo ID 1-4
Run: py -3.11 dashboard/app.py
"""

import json
import re
import threading
import time
from pathlib import Path

import serial
from flask import Flask, jsonify, request, send_file

BASE = Path(__file__).resolve().parent
PORT = "COM3"
BAUD = 115200
IDS = (1, 2, 3, 4)

ZERO_FILE = BASE / "servo_zeros.json"
INDEX_FILE = BASE / "templates" / "index.html"

SAFE_MARGIN = 20
MAX_STEP = 25
POS_MAX_AGE = 8.0
INFO_MAX_AGE = 70.0

app = Flask(__name__)
lock = threading.RLock()
serial_lock = threading.Lock()
device = None

servos = {
    i: {
        "id": i,
        "actual": None,
        "min": None,
        "max": None,
        "voltage": None,
        "temperature": None,
        "online": False,
        "zero": None,
    }
    for i in IDS
}

state = {
    "connected": False,
    "info_ready": False,
    "busy": False,
    "error": None,
    "pos_at": 0.0,
    "info_at": 0.0,
}


def load_zeros():
    try:
        data = json.loads(
            ZERO_FILE.read_text(encoding="utf-8")
        )

        for i in IDS:
            value = data.get(str(i))

            if (
                type(value) is int
                and 0 <= value <= 1023
            ):
                servos[i]["zero"] = value

    except (OSError, ValueError, TypeError):
        pass


def save_zeros():
    data = {
        str(i): servos[i]["zero"]
        for i in IDS
        if servos[i]["zero"] is not None
    }

    temp = ZERO_FILE.with_suffix(".json.tmp")

    temp.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )

    temp.replace(ZERO_FILE)


def transact(command, begin, end, seconds):
    global device

    if device is None or not device.is_open:
        device = serial.Serial(
            PORT,
            BAUD,
            timeout=0.10,
            write_timeout=0.6,
        )

        time.sleep(1.8)

    device.reset_input_buffer()

    device.write(
        (command + "\n").encode("ascii")
    )

    device.flush()

    deadline = time.monotonic() + seconds
    started = False
    lines = []

    while time.monotonic() < deadline:
        raw = device.readline()

        if not raw:
            continue

        line = raw.decode(
            "utf-8",
            errors="replace",
        ).strip()

        if begin in line:
            started = True
            lines = []

        elif started and end in line:
            return lines

        elif started:
            lines.append(line)

    raise TimeoutError(
        f"ESP32 did not complete {command}"
    )


def refresh_positions():
    lines = transact(
        "READPOS",
        "===== REAL SERVO POSITIONS",
        "===== READ COMPLETE",
        7.0,
    )

    positions = {}

    for line in lines:
        m = re.fullmatch(
            r"\[POSITION\] Servo ID (\d+) = (\d+) ticks",
            line,
        )

        if m and int(m[1]) in IDS:
            positions[int(m[1])] = int(m[2])

    with lock:
        for i in IDS:
            servos[i]["actual"] = positions.get(i)
            servos[i]["online"] = i in positions

        state["connected"] = len(positions) == 4

        state["pos_at"] = (
            time.monotonic()
            if state["connected"]
            else 0
        )

    if len(positions) != 4:
        raise RuntimeError(
            "READPOS did not return all four servos"
        )


def refresh_info():
    lines = transact(
        "READINFO",
        "===== SERVO READINFO",
        "===== READINFO COMPLETE",
        12.0,
    )

    values = {}

    for line in lines:
        m = re.fullmatch(
            r"\[INFO\] ID=(\d+) MIN=(\d+) MAX=(\d+) "
            r"VOLTAGE=([\d.]+) TEMP=(\d+)",
            line,
        )

        if m and int(m[1]) in IDS:
            values[int(m[1])] = (
                int(m[2]),
                int(m[3]),
                float(m[4]),
                int(m[5]),
            )

    with lock:
        for i in IDS:
            if i in values:
                low, high, volt, temp = values[i]

                servos[i].update(
                    min=low,
                    max=high,
                    voltage=volt,
                    temperature=temp,
                )
            else:
                servos[i].update(
                    min=None,
                    max=None,
                    voltage=None,
                    temperature=None,
                )

        state["info_ready"] = len(values) == 4

        state["info_at"] = (
            time.monotonic()
            if state["info_ready"]
            else 0
        )

    if len(values) != 4:
        raise RuntimeError(
            "READINFO did not return all four servos"
        )


def safe_readings(i, target=None):
    with lock:
        s = dict(servos[i])
        now = time.monotonic()

        if not (
            state["connected"]
            and state["info_ready"]
            and s["online"]
        ):
            raise ValueError(
                "ESP32/Servo is not ready"
            )

        if (
            now - state["pos_at"] > POS_MAX_AGE
            or now - state["info_at"] > INFO_MAX_AGE
        ):
            raise ValueError(
                "Servo feedback is stale; press Refresh"
            )

    actual = s["actual"]
    low = s["min"]
    high = s["max"]

    if not all(
        type(x) is int
        for x in (actual, low, high)
    ):
        raise ValueError(
            "Missing actual position or hardware limits"
        )

    if not (
        0 <= low < high <= 1023
        and low <= actual <= high
    ):
        raise ValueError(
            "Invalid hardware limits or actual position"
        )

    if (
        s["voltage"] is None
        or not (6.5 <= s["voltage"] <= 8.4)
    ):
        raise ValueError(
            "Servo voltage is outside 6.5-8.4 V"
        )

    if (
        s["temperature"] is None
        or s["temperature"] >= 55
    ):
        raise ValueError(
            "Servo temperature is unsafe"
        )

    if target is not None:
        if not (
            low + SAFE_MARGIN
            <= target
            <= high - SAFE_MARGIN
        ):
            raise ValueError(
                f"Target must be within "
                f"{low + SAFE_MARGIN}.."
                f"{high - SAFE_MARGIN}"
            )

        if abs(target - actual) > MAX_STEP:
            raise ValueError(
                f"Max {MAX_STEP} ticks per command"
            )

    return s


def serial_poll_loop():
    global device

    while True:
        try:
            with serial_lock:
                refresh_positions()

                with lock:
                    need_info = (
                        time.monotonic()
                        - state["info_at"] > 30
                    )

                if need_info:
                    refresh_info()

            with lock:
                state["error"] = None

        except Exception as exc:
            with lock:
                state.update(
                    connected=False,
                    info_ready=False,
                    pos_at=0,
                    info_at=0,
                    error=str(exc),
                )

                for i in IDS:
                    servos[i]["online"] = False

            try:
                if device is not None:
                    device.close()
            except Exception:
                pass

            device = None

        time.sleep(2.0)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def home():
    return send_file(INDEX_FILE)


@app.get("/api/status")
def api_status():
    with lock:
        return jsonify(
            ok=True,
            connected=state["connected"],
            info_ready=state["info_ready"],
            busy=state["busy"],
            error=state["error"],
            servos=[
                dict(servos[i]) for i in IDS
            ],
        )


@app.post("/api/refresh")
def api_refresh():
    try:
        with serial_lock:
            refresh_positions()

            with lock:
                need_info = (
                    time.monotonic()
                    - state["info_at"] > 30
                )

            if need_info:
                refresh_info()

        return api_status()

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc),
        ), 503


@app.post("/api/servo/<int:sid>/zero")
def api_zero(sid):
    if sid not in IDS:
        return jsonify(
            ok=False,
            error="Invalid servo ID",
        ), 404

    if request.get_json(silent=True) != {
        "confirm": True
    }:
        return jsonify(
            ok=False,
            error="Confirmation required",
        ), 400

    try:
        with serial_lock:
            refresh_positions()

            with lock:
                if state["busy"]:
                    raise ValueError(
                        "Movement in progress"
                    )

            s = safe_readings(sid)

            with lock:
                servos[sid]["zero"] = s["actual"]
                save_zeros()

        return jsonify(
            ok=True,
            zero=s["actual"],
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc),
        ), 409


@app.post("/api/servo/<int:sid>/move")
def api_move(sid):
    if sid not in IDS:
        return jsonify(
            ok=False,
            error="Invalid servo ID",
        ), 404

    body = request.get_json(silent=True) or {}
    target = body.get("target")

    if (
        body.get("confirm") is not True
        or type(target) is not int
    ):
        return jsonify(
            ok=False,
            error=(
                "Integer target and "
                "confirmation required"
            ),
        ), 400

    try:
        with serial_lock:
            with lock:
                if state["busy"]:
                    raise ValueError(
                        "Another movement is in progress"
                    )

                state["busy"] = True

            try:
                refresh_positions()

                s = safe_readings(
                    sid,
                    target,
                )

                if s["actual"] == target:
                    return jsonify(
                        ok=True,
                        target=target,
                        message="Already at target",
                    )

                lines = transact(
                    f"MOVE {sid} {target}",
                    "===== MOVE RESPONSE =====",
                    "===== MOVE RESPONSE END =====",
                    16.0,
                )

                match = next(
                    (
                        re.fullmatch(
                            r"\[MOVE OK\] ID=(\d+) "
                            r"FROM=(\d+) TARGET=(\d+) "
                            r"SPEED=(\d+)",
                            line,
                        )
                        for line in lines
                        if line.startswith("[MOVE OK]")
                    ),
                    None,
                )

                if (
                    not match
                    or int(match[1]) != sid
                    or int(match[3]) != target
                ):
                    raise ValueError(
                        "MOVE rejected by ESP32: "
                        + " | ".join(lines[-4:])
                    )

                return jsonify(
                    ok=True,
                    target=target,
                    firmware_speed=int(match[4]),
                )

            finally:
                with lock:
                    state["busy"] = False

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc),
        ), 409


if __name__ == "__main__":
    load_zeros()

    threading.Thread(
        target=serial_poll_loop,
        daemon=True,
    ).start()

    print(
        "Robot Lamp Dashboard: "
        "http://127.0.0.1:5000"
    )

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )
