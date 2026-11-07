
import matplotlib.pyplot as plt

from emotion_brain import EmotionBrain


# ========================================
# ROBOT LAMP - WEEK 04
# CONTROLLED HORMONE EXPERIMENT
# ========================================

DT = 0.05
DURATION = 20.0

START_STIMULUS = 2.0
STOP_STIMULUS = 5.0

CONDITIONS = {
    "C1 - Baseline": (2.10, 1.35, 0.12),
    "C2 - Low Production": (1.20, 1.35, 0.12),
    "C3 - Low Clearance": (2.10, 0.25, 0.12),
    "C4 - No Binding": (2.10, 1.35, 0.00),
}


def simulate(kprod, kclear, kbind):

    brain = EmotionBrain(
        sleep_after=float("inf")
    )

    brain.kprod = kprod
    brain.kclear = kclear
    brain.kbind = kbind

    times = []
    hormones = []
    receptors = []

    steps = int(DURATION / DT) + 1

    for i in range(steps):

        t = i * DT

        gesture = (
            "HAPPY"
            if START_STIMULUS <= t < STOP_STIMULUS
            else "IDLE"
        )

        result = brain.update(
            gesture,
            now=t
        )

        times.append(t)
        hormones.append(result.hormone["HAPPY"])
        receptors.append(result.receptor["HAPPY"])

    return times, hormones, receptors


def calculate_auc(times, values):

    auc = 0.0

    for i in range(1, len(times)):

        dt = times[i] - times[i - 1]

        auc += (
            (values[i] + values[i - 1])
            * 0.5 * dt
        )

    return auc


def calculate_recovery(times, values):

    for t, h in zip(times, values):

        if t >= STOP_STIMULUS and h <= 0.10:
            return t - STOP_STIMULUS

    return None


def main():

    print("=" * 58)
    print("ROBOT LAMP - CONTROLLED HORMONE EXPERIMENT")
    print("=" * 58)

    for name, params in CONDITIONS.items():

        times, h, r = simulate(*params)

        peak = max(h)
        auc = calculate_auc(times, h)
        recovery = calculate_recovery(times, h)

        saturation = (
            sum(value >= 0.99 for value in h)
            / len(h) * 100
        )

        print(f"\n{name}")
        print(f"  Peak H        : {peak:.4f}")
        print(f"  Peak Receptor : {max(r):.4f}")
        print(f"  AUC           : {auc:.4f}")
        print(f"  Saturation    : {saturation:.2f}%")

        if recovery is None:
            print("  Recovery      : Not reached")
        else:
            print(f"  Recovery      : {recovery:.2f} s")

        plt.plot(
            times,
            h,
            label=name
        )

    plt.title(
        "Robot Lamp - Hormone Parameter Comparison"
    )

    plt.xlabel("Time (seconds)")
    plt.ylabel("HAPPY Hormone Level (0-1)")
    plt.ylim(-0.05, 1.05)

    plt.axvline(
        START_STIMULUS,
        linestyle="--",
        alpha=0.5
    )

    plt.axvline(
        STOP_STIMULUS,
        linestyle="--",
        alpha=0.5
    )

    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
