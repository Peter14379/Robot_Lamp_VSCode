
from pathlib import Path
import csv

import matplotlib.pyplot as plt


# =====================================
# ROBOT LAMP - HORMONE TESTING WEEK 04
# =====================================

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "emotion_trace.csv"

EMOTIONS = [
    "HAPPY",
    "ANGRY",
    "SURPRISED",
    "SAD"
]


def load_data():

    if not CSV_PATH.is_file():
        raise FileNotFoundError(CSV_PATH)

    with CSV_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    required = (
        ["time_s"]
        + ["H_" + e for e in EMOTIONS]
        + ["R_" + e for e in EMOTIONS]
    )

    if not rows:
        raise RuntimeError("CSV is empty")

    missing = [
        name for name in required
        if name not in rows[0]
    ]

    if missing:
        raise RuntimeError(
            "Missing columns: " + ", ".join(missing)
        )

    time = [float(row["time_s"]) for row in rows]
    start = time[0]
    time = [t - start for t in time]

    hormones = {}
    receptors = {}

    for emotion in EMOTIONS:

        hormones[emotion] = [
            float(row["H_" + emotion])
            for row in rows
        ]

        receptors[emotion] = [
            float(row["R_" + emotion])
            for row in rows
        ]

    return time, hormones, receptors


def calculate_auc(time, values):

    area = 0.0

    for i in range(1, len(time)):
        dt = max(0.0, time[i] - time[i - 1])
        area += (
            (values[i] + values[i - 1])
            * 0.5 * dt
        )

    return area


def main():

    time, hormones, receptors = load_data()

    print("================================")
    print("ROBOT LAMP - HORMONE TESTING")
    print("================================")
    print(f"Samples: {len(time)}")
    print(f"Duration: {time[-1]:.2f} seconds")

    for emotion in EMOTIONS:

        values = hormones[emotion]

        peak = max(values)
        peak_index = values.index(peak)
        peak_time = time[peak_index]

        auc = calculate_auc(time, values)

        saturation = (
            sum(v >= 0.99 for v in values)
            / len(values) * 100.0
        )

        print(f"\n[{emotion}]")
        print(f"Peak: {peak:.4f}")
        print(f"Peak time: {peak_time:.2f} s")
        print(f"AUC: {auc:.4f}")
        print(f"Near saturation: {saturation:.2f}%")

    # =================================
    # GRAPH 1 - HORMONE LEVELS
    # =================================

    fig, axes = plt.subplots(
        2, 1, figsize=(11, 8), sharex=True
    )

    for emotion in EMOTIONS:
        axes[0].plot(
            time,
            hormones[emotion],
            label=emotion
        )

    axes[0].set_title(
        "Robot Lamp - Artificial Hormones"
    )
    axes[0].set_ylabel("Hormone H (0-1)")
    axes[0].set_ylim(-0.05, 1.05)
    axes[0].grid(True)
    axes[0].legend()

    # =================================
    # GRAPH 2 - RECEPTOR RESPONSE
    # =================================

    for emotion in EMOTIONS:
        axes[1].plot(
            time,
            receptors[emotion],
            label="R_" + emotion
        )

    axes[1].set_title(
        "Robot Lamp - Receptor Response"
    )
    axes[1].set_xlabel("Time (seconds)")
    axes[1].set_ylabel("Receptor R (0-1)")
    axes[1].set_ylim(-0.05, 1.05)
    axes[1].grid(True)
    axes[1].legend()

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
