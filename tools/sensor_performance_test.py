
"""
Robot Lamp - Sensor Performance Test
Week 02: Biological Sensing

Read existing emotion_trace.csv
No camera / No ESP32 / No Servo
"""

import csv
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "emotion_trace.csv"


def load_data():

    if not CSV_FILE.exists():
        raise FileNotFoundError(CSV_FILE)

    with CSV_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        reader = csv.DictReader(f)

        required = {
            "time_s",
            "raw",
            "stable",
            "hand_area",
            "face_count",
        }

        if not required.issubset(
            set(reader.fieldnames or [])
        ):
            raise RuntimeError(
                "Missing required CSV columns"
            )

        rows = list(reader)

    if not rows:
        raise RuntimeError("No data found")

    return rows


def count_switches(values):

    return sum(
        values[i] != values[i - 1]
        for i in range(1, len(values))
    )


def main():

    rows = load_data()

    times = [
        float(row["time_s"])
        for row in rows
    ]

    raw = [
        row["raw"].strip().upper()
        for row in rows
    ]

    stable = [
        row["stable"].strip().upper()
        for row in rows
    ]

    hand = [
        float(row["hand_area"]) > 0.0
        for row in rows
    ]

    faces = [
        int(row["face_count"]) > 0
        for row in rows
    ]

    duration = times[-1] - times[0]

    raw_switch = count_switches(raw)
    stable_switch = count_switches(stable)

    hand_rate = (
        sum(hand) / len(hand) * 100
    )

    face_rate = (
        sum(faces) / len(faces) * 100
    )

    # ==================================
    # RAW -> STABLE DELAY
    # ==================================

    response_delays = []
    recovery_delays = []

    for i in range(1, len(rows)):

        if stable[i] == stable[i - 1]:
            continue

        target = stable[i]

        if raw[i] != target:
            continue

        # Find the beginning of the
        # current continuous raw state.
        j = i

        while (
            j > 0
            and raw[j - 1] == target
        ):
            j -= 1

        delay = max(
            0.0,
            times[i] - times[j]
        )

        if target == "IDLE":
            recovery_delays.append(delay)
        else:
            response_delays.append(delay)

    # ==================================
    # PRINT RESULTS
    # ==================================

    print("=" * 55)
    print("ROBOT LAMP - SENSOR PERFORMANCE")
    print("=" * 55)

    print(f"Samples: {len(rows)}")
    print(f"Duration: {duration:.2f} s")

    print("\n--- DETECTION ---")

    print(f"Hand detection: {hand_rate:.2f}%")
    print(f"Face detection: {face_rate:.2f}%")

    print("\n--- SWITCHING ---")

    print(f"Raw switches: {raw_switch}")
    print(f"Stable switches: {stable_switch}")

    if raw_switch > 0:
        reduction = (
            (raw_switch - stable_switch)
            / raw_switch * 100
        )
        print(
            f"Switch reduction: {reduction:.2f}%"
        )

    print("\n--- STABILIZATION ---")

    if response_delays:
        print(
            "Mean Raw-to-Stable delay:",
            f"{mean(response_delays):.3f} s"
        )
        print(
            "Observed transitions:",
            len(response_delays)
        )
    else:
        print("No matched gesture transitions")

    print("\n--- RECOVERY ---")

    if recovery_delays:
        print(
            "Mean return-to-IDLE delay:",
            f"{mean(recovery_delays):.3f} s"
        )
    else:
        print("No matched IDLE transitions")

    print("\n[TEST COMPLETE]")


if __name__ == "__main__":
    main()
