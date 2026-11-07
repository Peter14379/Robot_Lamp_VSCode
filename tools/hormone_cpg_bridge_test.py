
"""
Robot Lamp - Hormone to SO(2) CPG
Week 03 + Week 04 Integration Test

Simulation only: NO ESP32 / NO Servo
"""

import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
from emotion_brain import EmotionBrain


ROOT = Path(__file__).resolve().parent.parent

DT = 0.05
DURATION = 30.0
CPG_DT = 0.10

PHI_BASE = 0.20
PHI_GAIN = 0.20
PHI_MIN = 0.10
PHI_MAX = 0.45

ALPHA = 1.12


def clamp(value, low, high):
    return max(low, min(high, value))


def so2_step(o1, o2, phi):
    c = math.cos(phi)
    s = math.sin(phi)

    # Simultaneous update
    a1 = ALPHA * (c * o1 + s * o2)
    a2 = ALPHA * (-s * o1 + c * o2)

    return math.tanh(a1), math.tanh(a2)


def main():

    brain = EmotionBrain(
        sleep_after=float("inf")
    )

    times = []
    hormones = []
    receptors = []
    phis = []
    wave1 = []
    wave2 = []
    baseline_wave = []
    records = []

    o1, o2 = 0.15, 0.0
    base_o1, base_o2 = 0.15, 0.0

    accumulator = 0.0

    steps = int(DURATION / DT) + 1

    for i in range(steps):

        t = round(i * DT, 8)

        # 0-10 s: no stimulus
        # 10-20 s: HAPPY stimulus
        # 20-30 s: recovery

        gesture = (
            "HAPPY"
            if 10.0 <= t < 20.0
            else "IDLE"
        )

        decision = brain.update(
            gesture,
            now=t
        )

        h = decision.hormone["HAPPY"]
        r = decision.receptor["HAPPY"]

        # Week 04 receptor -> bounded CPG parameter
        phi = clamp(
            PHI_BASE + PHI_GAIN * r,
            PHI_MIN,
            PHI_MAX
        )

        # CPG updates every 0.10 seconds
        if i > 0:
            accumulator += DT

        while accumulator + 1e-10 >= CPG_DT:

            accumulator -= CPG_DT

            o1, o2 = so2_step(
                o1, o2, phi
            )

            base_o1, base_o2 = so2_step(
                base_o1, base_o2, PHI_BASE
            )

        times.append(t)
        hormones.append(h)
        receptors.append(r)
        phis.append(phi)
        wave1.append(o1)
        wave2.append(o2)
        baseline_wave.append(base_o1)

        records.append([
            t, gesture, h, r, phi,
            o1, o2, base_o1
        ])

    assert all(
        PHI_MIN <= phi <= PHI_MAX
        for phi in phis
    )

    print("=" * 55)
    print("ROBOT LAMP - HORMONE TO CPG SIMULATION")
    print("=" * 55)

    print("Baseline Phi :", PHI_BASE)
    print("Peak Hormone :", round(max(hormones), 4))
    print("Peak Receptor:", round(max(receptors), 4))
    print("Peak Phi     :", round(max(phis), 4))
    print("Final Hormone:", round(hormones[-1], 4))
    print("Final Phi    :", round(phis[-1], 4))
    print("Safety Bounds: PASS")

    output = ROOT / "data" / "hormone_cpg_bridge.csv"
    output.parent.mkdir(exist_ok=True)

    with output.open(
        "w", newline="", encoding="utf-8"
    ) as f:

        writer = csv.writer(f)
        writer.writerow([
            "time_s", "gesture", "H_HAPPY",
            "R_HAPPY", "phi_rad",
            "o1", "o2", "baseline_o1"
        ])
        writer.writerows(records)

    print("[CSV SAVED]", output)

    fig, axes = plt.subplots(
        3, 1, figsize=(11, 9),
        sharex=True
    )

    axes[0].plot(
        times, hormones, label="HAPPY Hormone"
    )
    axes[0].plot(
        times, receptors, label="HAPPY Receptor"
    )
    axes[0].set_ylabel("H / R")
    axes[0].set_title(
        "Week 04 - Hormone and Receptor"
    )
    axes[0].legend()
    axes[0].grid(True)

    axes[1].plot(
        times, phis, label="Phi Command"
    )
    axes[1].axhline(
        PHI_BASE,
        linestyle="--",
        label="Baseline Phi"
    )
    axes[1].set_ylabel("Phi (rad)")
    axes[1].set_title(
        "Bounded Hormone-to-CPG Mapping"
    )
    axes[1].legend()
    axes[1].grid(True)

    axes[2].plot(
        times, wave1,
        label="Hormone Modulated CPG"
    )
    axes[2].plot(
        times, baseline_wave,
        linestyle="--",
        label="Baseline CPG"
    )
    axes[2].set_ylabel("Oscillator o1")
    axes[2].set_xlabel("Time (seconds)")
    axes[2].set_title(
        "Week 03 - CPG Comparison"
    )
    axes[2].legend()
    axes[2].grid(True)

    for ax in axes:
        ax.axvline(10, linestyle=":", alpha=0.6)
        ax.axvline(20, linestyle=":", alpha=0.6)

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
