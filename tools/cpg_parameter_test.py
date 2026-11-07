
"""
Robot Lamp - SO(2) CPG Parameter Test
Week 03: No Servo / No ESP32 Commands
"""

import csv
import math
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt

from autonomous_behavior import AnimalAutonomy


ROOT = Path(__file__).resolve().parent.parent

DT = 0.10
STEPS = 1200
WARMUP = 200

INITIAL_STATES = [
    (0.15, 0.00),
    (0.10, 0.08),
    (-0.12, 0.05),
]


# Same curiosity-to-phi mapping as Robot Lamp
def curiosity_to_phi(curiosity):
    receptor = curiosity / (0.27 + curiosity)
    return 0.09 + 0.15 * receptor


BASE_PHI = curiosity_to_phi(0.65)

CONDITIONS = {
    "C1 Baseline": (1.12, BASE_PHI),
    "C2 Fast Phi": (1.12, 0.40),
    "C3 High Gain": (1.30, BASE_PHI),
    "C4 Low Curiosity": (
        1.12, curiosity_to_phi(0.20)
    ),
    "C5 High Curiosity": (
        1.12, curiosity_to_phi(0.90)
    ),
}


# ==========================================
# SO(2) CPG - SAME EQUATION AS ORIGINAL
# ==========================================

def so2_step(o1, o2, alpha, phi):

    c = math.cos(phi)
    s = math.sin(phi)

    a1 = alpha * (c * o1 + s * o2)
    a2 = alpha * (-s * o1 + c * o2)

    next_o1 = math.tanh(a1)
    next_o2 = math.tanh(a2)

    return a1, a2, next_o1, next_o2


# ==========================================
# VERIFY AGAINST REAL ANIMAL AUTONOMY
# ==========================================

def verify_original_cpg():

    animal = AnimalAutonomy()

    animal.curiosity = 0.65
    animal.o1 = 0.15
    animal.o2 = 0.0

    o1, o2 = animal.o1, animal.o2

    for _ in range(10):

        _, _, o1, o2 = so2_step(
            o1, o2, 1.12, BASE_PHI
        )

        animal._cpg(DT)

        assert math.isclose(
            animal.o1, o1, abs_tol=1e-9
        )

        assert math.isclose(
            animal.o2, o2, abs_tol=1e-9
        )

    print("[PASS] Original CPG matches test equation")


# ==========================================
# SIMULATION
# ==========================================

def simulate(name, alpha, phi, seed_id, initial):

    o1, o2 = initial

    times = []
    wave1 = []
    wave2 = []
    records = []

    c = math.cos(phi)
    s = math.sin(phi)

    for step in range(1, STEPS + 1):

        a1, a2, o1, o2 = so2_step(
            o1, o2, alpha, phi
        )

        t = step * DT

        times.append(t)
        wave1.append(o1)
        wave2.append(o2)

        records.append([
            step, t, name, seed_id,
            a1, a2, o1, o2,
            alpha, phi,
            alpha * c,
            alpha * s,
            -alpha * s,
            alpha * c,
        ])

    return times, wave1, wave2, records


# ==========================================
# PERIOD / AMPLITUDE / PHASE LAG
# ==========================================

def find_peaks(values):

    return [
        i for i in range(1, len(values) - 1)
        if values[i] > values[i - 1]
        and values[i] >= values[i + 1]
    ]


def measure(wave1, wave2):

    x = wave1[WARMUP:]
    y = wave2[WARMUP:]

    peaks1 = find_peaks(x)
    peaks2 = find_peaks(y)

    amplitude = (max(x) - min(x)) / 2.0

    if len(peaks1) < 3 or len(peaks2) < 3:
        return amplitude, float("nan"), float("nan")

    period_steps = mean([
        peaks1[i] - peaks1[i - 1]
        for i in range(1, len(peaks1))
    ])

    period_s = period_steps * DT

    phase_values = []

    for p1 in peaks1[1:-1]:

        p2 = min(
            peaks2,
            key=lambda p: abs(p - p1)
        )

        lag = (
            (p2 - p1 + period_steps / 2)
            % period_steps
        ) - period_steps / 2

        phase_values.append(
            360.0 * lag / period_steps
        )

    phase_deg = mean(phase_values)

    return amplitude, period_s, phase_deg


# ==========================================
# MAIN
# ==========================================

def main():

    verify_original_cpg()

    print("\nROBOT LAMP - CPG PARAMETER TEST")
    print("=" * 65)

    output_dir = ROOT / "data"
    output_dir.mkdir(exist_ok=True)

    output_path = (
        output_dir / "cpg_parameter_results.csv"
    )

    all_records = []
    graph_data = {}

    for name, (alpha, phi) in CONDITIONS.items():

        metrics = []

        for seed_id, initial in enumerate(
            INITIAL_STATES, start=1
        ):

            times, o1, o2, records = simulate(
                name, alpha, phi, seed_id, initial
            )

            all_records.extend(records)

            metrics.append(measure(o1, o2))

            if seed_id == 1:
                graph_data[name] = (
                    times, o1, o2
                )

        amplitude = mean(m[0] for m in metrics)
        period = mean(m[1] for m in metrics)
        phase = mean(m[2] for m in metrics)

        print(f"\n{name}")
        print(f"  Alpha     : {alpha:.3f}")
        print(f"  Phi       : {phi:.4f} rad")
        print(f"  Amplitude : {amplitude:.4f}")
        print(f"  Period    : {period:.3f} s")
        print(f"  Frequency : {1 / period:.3f} Hz")
        print(f"  Phase Lag : {phase:.2f} degrees")

    # Save raw results for Week 03 report
    with output_path.open(
        "w", newline="", encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "step", "time_s", "condition", "seed",
            "a1", "a2", "o1", "o2",
            "alpha", "phi_rad",
            "w11", "w12", "w21", "w22"
        ])

        writer.writerows(all_records)

    print("\n[CSV SAVED]", output_path)

    # ======================================
    # PLOTS
    # ======================================

    fig, axes = plt.subplots(
        2, 2, figsize=(13, 9)
    )

    window = slice(WARMUP, WARMUP + 250)

    t, x, y = graph_data["C1 Baseline"]

    # Graph 1: Baseline oscillators
    axes[0, 0].plot(
        t[window], x[window], label="o1"
    )
    axes[0, 0].plot(
        t[window], y[window], label="o2"
    )

    axes[0, 0].set_title(
        "Baseline SO(2) Oscillators"
    )
    axes[0, 0].set_xlabel("Time (s)")
    axes[0, 0].set_ylabel("Output")
    axes[0, 0].legend()
    axes[0, 0].grid(True)

    # Graph 2: Phase plane
    axes[0, 1].plot(
        x[WARMUP:],
        y[WARMUP:]
    )
    axes[0, 1].set_title("Phase Plane")
    axes[0, 1].set_xlabel("o1")
    axes[0, 1].set_ylabel("o2")
    axes[0, 1].grid(True)
    axes[0, 1].set_aspect(
        "equal", adjustable="box"
    )

    # Graph 3: Alpha and Phi comparison
    for name in [
        "C1 Baseline",
        "C2 Fast Phi",
        "C3 High Gain"
    ]:

        tt, xx, yy = graph_data[name]

        axes[1, 0].plot(
            tt[window],
            xx[window],
            label=name
        )

    axes[1, 0].set_title(
        "Alpha / Phi Comparison"
    )
    axes[1, 0].set_xlabel("Time (s)")
    axes[1, 0].set_ylabel("o1")
    axes[1, 0].legend()
    axes[1, 0].grid(True)

    # Graph 4: Curiosity modulation
    for name in [
        "C4 Low Curiosity",
        "C5 High Curiosity"
    ]:

        tt, xx, yy = graph_data[name]

        axes[1, 1].plot(
            tt[window],
            xx[window],
            label=name
        )

    axes[1, 1].set_title(
        "Curiosity Modulation"
    )
    axes[1, 1].set_xlabel("Time (s)")
    axes[1, 1].set_ylabel("o1")
    axes[1, 1].legend()
    axes[1, 1].grid(True)

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
