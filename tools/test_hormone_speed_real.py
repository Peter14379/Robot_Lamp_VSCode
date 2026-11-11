
import csv
import json
import math
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

BASE_URL = "http://127.0.0.1:5000"
ROOT = Path(__file__).resolve().parent.parent

DURATION_S = 120
INTERVAL_S = 1.5

OUTPUT = ROOT / "data" / "speed_test_results"
OUTPUT.mkdir(parents=True, exist_ok=True)

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
CSV_PATH = OUTPUT / f"speed_test_{RUN_ID}.csv"

FIELDS = [
    "elapsed_s", "clock", "hormone", "data_status",
    "target_raw", "filtered_raw", "applied_raw",
    "emotion", "servo_mode", "mask", "active",
    "step", "steps_total", "stage", "last_error",
    "actual_1", "actual_2", "actual_3", "actual_4",
    "goal_1", "goal_2", "goal_3", "goal_4",
    "max_position_error", "estimated_tick_per_s",
    "request_error"
]


def get_json(route):
    with urlopen(BASE_URL + route, timeout=3) as response:
        data = json.load(response)
    if not data.get("ok", False):
        raise RuntimeError(str(data.get("error", "API failed")))
    return data


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def positions(value):
    try:
        parts = [int(x) for x in value.split(",")]
        return parts if len(parts) == 4 else [None] * 4
    except (AttributeError, ValueError):
        return [None] * 4


def parse_cpg(line):
    values = dict(re.findall(r"(\w+)=([^\s]+)", line))
    step_text = values.get("STEP", "0/0").split("/")

    return {
        "emotion": values.get("Emotion", ""),
        "servo_mode": values.get("Servo", ""),
        "mask": values.get("MASK", ""),
        "active": integer(values.get("ACTIVE")),
        "step": integer(step_text[0]),
        "steps_total": integer(step_text[1]) if len(step_text) > 1 else None,
        "stage": integer(values.get("STAGE")),
        "last_error": values.get("LAST", ""),
        "actual": positions(values.get("ACT")),
        "goal": positions(values.get("GOAL"))
    }


def level(h):
    if h <= 0.33:
        return "LOW"
    if h <= 0.67:
        return "MEDIUM"
    return "HIGH"


def plot_series(rows, keys, title, ylabel, filename):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Install matplotlib to generate PNG graphs.")
        return

    x = [r["elapsed_s"] for r in rows]

    plt.figure(figsize=(11, 5))
    for key in keys:
        y = [
            float(r[key]) if r[key] is not None
            else math.nan
            for r in rows
        ]
        plt.plot(x, y, label=key)

    plt.title(title)
    plt.xlabel("Time (seconds)")
    plt.ylabel(ylabel)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT / filename, dpi=160)
    plt.close()


def main():
    print("ROBOT LAMP - REAL HORMONE / CPG SPEED TEST")
    print("Read-only monitoring: NO movement commands.")

    diag = get_json("/api/diagnostics")

    online = diag.get("servo_online", {})
    all_online = all(
        online.get(str(i), False)
        for i in range(1, 5)
    )

    if not (
        diag.get("connected")
        and diag.get("emotion_armed")
        and all_online
    ):
        print("STOP: REAL Servo 1-4 must be connected and ARMED.")
        print("Open Dashboard, check Home, then ARM Emotion.")
        return

    print("REAL / ARMED / Servo 1-4 ONLINE")
    print("Recording:", CSV_PATH)
    print("Duration:", DURATION_S, "seconds")
    print("Press Ctrl+C to finish early.")

    rows = []
    previous = None
    start = time.monotonic()

    try:
        with CSV_PATH.open(
            "w", newline="", encoding="utf-8-sig"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()

            while time.monotonic() - start < DURATION_S:
                loop_start = time.monotonic()
                elapsed = loop_start - start

                row = {
                    field: None for field in FIELDS
                }
                row["elapsed_s"] = round(elapsed, 3)
                row["clock"] = datetime.now().isoformat(
                    timespec="milliseconds"
                )

                try:
                    h = get_json("/api/hormone/drive")
                    c = get_json("/api/emotion/cpg-status")
                    p = parse_cpg(c.get("status", ""))

                    row["hormone"] = number(
                        h.get("hormone")
                    )
                    row["data_status"] = h.get(
                        "data_status"
                    )

                    # Supports both old and new Dashboard fields.
                    row["target_raw"] = number(
                        h.get(
                            "target_speed_raw",
                            h.get("speed_raw")
                        )
                    )
                    row["filtered_raw"] = number(
                        h.get("filtered_speed_raw")
                    )
                    row["applied_raw"] = number(
                        h.get("last_applied_speed_raw")
                    )

                    for key in (
                        "emotion", "servo_mode", "mask",
                        "active", "step", "steps_total",
                        "stage", "last_error"
                    ):
                        row[key] = p[key]

                    for i in range(4):
                        row[f"actual_{i + 1}"] = p["actual"][i]
                        row[f"goal_{i + 1}"] = p["goal"][i]

                    if all(x is not None for x in p["actual"] + p["goal"]):
                        row["max_position_error"] = max(
                            abs(a - b)
                            for a, b in zip(p["actual"], p["goal"])
                        )

                    # Estimate real motion speed from position feedback.
                    if (
                        previous is not None
                        and p["servo_mode"] == "REAL"
                        and p["active"] == 1
                        and previous["active"] == 1
                        and p["emotion"] == previous["emotion"]
                        and all(x is not None for x in p["actual"])
                    ):
                        dt = elapsed - previous["time"]
                        if dt > 0.1:
                            row["estimated_tick_per_s"] = round(
                                max(
                                    abs(a - b)
                                    for a, b in zip(
                                        p["actual"],
                                        previous["actual"]
                                    )
                                ) / dt,
                                2
                            )

                    previous = {
                        "time": elapsed,
                        "actual": p["actual"],
                        "active": p["active"],
                        "emotion": p["emotion"]
                    }

                except Exception as exc:
                    row["request_error"] = str(exc)
                    previous = None

                writer.writerow(row)
                file.flush()
                rows.append(row)

                print(
                    f"H={row['hormone']} "
                    f"Target={row['target_raw']} "
                    f"Filtered={row['filtered_raw']} "
                    f"Applied={row['applied_raw']} "
                    f"Emotion={row['emotion']} "
                    f"Step={row['step']}/{row['steps_total']}"
                )

                remaining = INTERVAL_S - (
                    time.monotonic() - loop_start
                )
                if remaining > 0:
                    time.sleep(remaining)

    except KeyboardInterrupt:
        print("\nRecording stopped by user.")

    print("\nRESULT SUMMARY")

    valid = [
        r for r in rows
        if r["data_status"] == "LIVE"
        and r["hormone"] is not None
        and r["servo_mode"] == "REAL"
    ]

    for band in ("LOW", "MEDIUM", "HIGH"):
        selected = [
            r for r in valid
            if level(r["hormone"]) == band
        ]
        targets = [
            r["target_raw"] for r in selected
            if r["target_raw"] is not None
        ]
        motion_speeds = [
            r["estimated_tick_per_s"]
            for r in selected
            if r["estimated_tick_per_s"] is not None
        ]

        print(
            f"{band}: samples={len(selected)} "
            f"average_target_raw="
            f"{round(statistics.mean(targets), 1) if targets else 'N/A'} "
            f"average_motion_ticks_s="
            f"{round(statistics.mean(motion_speeds), 1) if motion_speeds else 'N/A'}"
        )

    errors = [
        r for r in rows
        if r["request_error"]
        or (
            r["last_error"]
            and r["last_error"] != "NONE"
        )
    ]
    print("Rows reporting error:", len(errors))
    print("CSV:", CSV_PATH)

    if rows:
        plot_series(
            rows, ["hormone"],
            "Hormone Level", "Hormone (0-1)",
            f"hormone_{RUN_ID}.png"
        )
        plot_series(
            rows,
            ["target_raw", "filtered_raw", "applied_raw"],
            "Servo Speed Commands",
            "RAW speed",
            f"speed_{RUN_ID}.png"
        )
        plot_series(
            rows,
            ["actual_1", "actual_2", "actual_3", "actual_4"],
            "Servo Actual Positions",
            "Ticks",
            f"servo_{RUN_ID}.png"
        )
        print("Graphs saved in:", OUTPUT)


if __name__ == "__main__":
    main()
