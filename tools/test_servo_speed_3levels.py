
import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "http://127.0.0.1:5000"

SERVO_ID = 1
DISTANCE = 80
REPEATS = 1  # Change to 3 after the first successful test

# Reference hormone -> RAW speed mapping
LEVELS = [
    ("LOW", 0.0, 40),
    ("MEDIUM", 0.5, 270),
    ("HIGH", 1.0, 500),
]

RETURN_SPEED = 80
TOLERANCE = 10
EXTRA_MARGIN = 30

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "speed_test_results"
OUT.mkdir(parents=True, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
CSV_PATH = OUT / f"servo_3levels_{STAMP}.csv"


def api(path, method="GET", payload=None):
    body = None

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = Request(
        BASE + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urlopen(req, timeout=25) as response:
            result = json.load(response)
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"Flask connection failed: {exc}") from exc

    if not result.get("ok", False):
        raise RuntimeError(str(result.get("error", result)))

    return result


def refreshed_state():
    return api("/api/refresh", "POST", {})


def servo_from_state(state):
    for servo in state["servos"]:
        if servo["id"] == SERVO_ID:
            return servo
    raise RuntimeError("Servo not found")


def check_ready(state):
    if not state.get("connected"):
        raise RuntimeError("ESP32 is not connected")

    if state.get("emotion_armed"):
        raise RuntimeError("Please DISARM Emotion first")

    if state.get("busy"):
        raise RuntimeError("Dashboard is busy")

    servo = servo_from_state(state)

    if not servo.get("online"):
        raise RuntimeError("Servo is offline")

    for key in ("actual", "zero", "min", "max"):
        if type(servo.get(key)) is not int:
            raise RuntimeError(f"Invalid servo {key}")

    if abs(servo["actual"] - servo["zero"]) > TOLERANCE:
        raise RuntimeError(
            "Servo is not at saved Home. "
            "Move it to Home manually before this test."
        )

    low = servo["min"] + EXTRA_MARGIN
    high = servo["max"] - EXTRA_MARGIN

    home = servo["zero"]

    if low <= home + DISTANCE <= high:
        target = home + DISTANCE
    elif low <= home - DISTANCE <= high:
        target = home - DISTANCE
    else:
        raise RuntimeError("No valid test target within limits")

    return home, target


def confirmed(prompt):
    answer = input(prompt + " Type YES to continue: ")
    if answer.strip().upper() != "YES":
        raise KeyboardInterrupt("Cancelled by user")


def do_move(target, raw_speed):
    start = time.monotonic()

    result = api(
        f"/api/servo/{SERVO_ID}/move",
        "POST",
        {
            "target": int(target),
            "speed": int(raw_speed),
        },
    )

    elapsed = time.monotonic() - start

    if not result.get("reached"):
        raise RuntimeError("Move did not reach target")

    actual = result.get("actual")
    if type(actual) is not int:
        raise RuntimeError("No valid Actual position")

    if abs(actual - target) > TOLERANCE:
        raise RuntimeError("Position error exceeded tolerance")

    return result, elapsed


def main():
    print("ROBOT LAMP - SERVO SPEED 3 LEVELS")
    print("Manual MOVE calibration - not live Hormone CPG")
    print("Servo:", SERVO_ID)
    print("Distance:", DISTANCE, "ticks")

    state = refreshed_state()
    home, target = check_ready(state)

    print("Saved Home:", home)
    print("Test Target:", target)
    print("Speed levels:", [x[2] for x in LEVELS])

    print("\nCHECK:")
    print("- Camera controller is stopped")
    print("- Emotion is DISARMED")
    print("- Servo path is clear")
    print("- Power and wiring are secure")
    print("- Do not touch moving joints")

    confirmed("Start calibration?")

    fields = [
        "timestamp", "level", "hormone_reference",
        "trial", "servo_id", "raw_speed",
        "start_ticks", "target_ticks",
        "final_ticks", "travel_ticks",
        "duration_s", "approx_ticks_per_s",
        "reached",
    ]

    results = []

    with CSV_PATH.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()

        try:
            for level, hormone, raw in LEVELS:
                for trial in range(1, REPEATS + 1):
                    state = refreshed_state()
                    new_home, new_target = check_ready(state)

                    if new_home != home or new_target != target:
                        raise RuntimeError(
                            "Home or test target changed"
                        )

                    print(
                        f"\n{level} | Trial {trial} | "
                        f"RAW {raw} | {home} -> {target}"
                    )

                    confirmed("Move outward?")

                    result, elapsed = do_move(target, raw)

                    actual = result["actual"]
                    travelled = abs(actual - result["before"])
                    approx_speed = travelled / max(elapsed, 0.001)

                    row = {
                        "timestamp": datetime.now().isoformat(),
                        "level": level,
                        "hormone_reference": hormone,
                        "trial": trial,
                        "servo_id": SERVO_ID,
                        "raw_speed": raw,
                        "start_ticks": result["before"],
                        "target_ticks": target,
                        "final_ticks": actual,
                        "travel_ticks": travelled,
                        "duration_s": round(elapsed, 3),
                        "approx_ticks_per_s": round(
                            approx_speed, 2
                        ),
                        "reached": True,
                    }

                    writer.writerow(row)
                    file.flush()
                    results.append(row)

                    print(
                        f"REACHED | time={elapsed:.3f}s | "
                        f"approx={approx_speed:.1f} ticks/s"
                    )

                    confirmed(
                        f"Return Servo {SERVO_ID} to Home {home}?"
                    )

                    do_move(home, RETURN_SPEED)
                    time.sleep(0.5)

        except KeyboardInterrupt:
            print("\nTEST STOPPED BY USER")
            print("No automatic return move will be sent.")
        except Exception as exc:
            print("\nTEST STOPPED:", exc)
            print("Check actual position before restarting.")
        finally:
            print("\nCSV saved:", CSV_PATH)

    print("\nRESULT SUMMARY")

    for level, _, raw in LEVELS:
        group = [r for r in results if r["level"] == level]

        if not group:
            print(level, ": NOT TESTED")
            continue

        avg_time = statistics.mean(
            r["duration_s"] for r in group
        )
        avg_speed = statistics.mean(
            r["approx_ticks_per_s"] for r in group
        )

        print(
            f"{level} RAW={raw} "
            f"Trials={len(group)} "
            f"AvgTime={avg_time:.3f}s "
            f"ApproxSpeed={avg_speed:.1f} ticks/s"
        )


if __name__ == "__main__":
    main()
