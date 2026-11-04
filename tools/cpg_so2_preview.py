
"""SO(2) CPG Simulation.

This program generates rhythmic servo signals
and saves results as CSV.

It does NOT command ESP32 hardware.
"""

import argparse
import csv
import math

from pathlib import Path


def so2_step(o1, o2, alpha, phi):

    c = math.cos(phi)
    s = math.sin(phi)

    a1 = alpha * (
        c * o1 + s * o2
    )

    a2 = alpha * (
        -s * o1 + c * o2
    )

    return (
        math.tanh(a1),
        math.tanh(a2)
    )


def run(
    steps=450,
    alpha=1.1,
    phi=0.2,
    amplitude_ticks=8.0,
    filename="cpg_preview.csv"
):

    if not (
        1.0 < alpha <= 1.4
        and 0.05 <= phi <= 0.45
    ):
        raise ValueError(
            "CPG parameters outside preview ranges"
        )

    amp = max(
        0.0,
        min(10.0, amplitude_ticks)
    )

    o1 = 0.1
    o2 = 0.0

    with Path(filename).open(
        "w",
        encoding="utf-8",
        newline=""
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "step",
            "o1",
            "o2",
            "alpha",
            "phi_rad",
            "servo1_delta",
            "servo2_delta",
            "servo3_delta",
            "servo4_delta"
        ])

        for t in range(steps):

            delta = [
                amp * o1,
                -amp * o1,
                amp * o2,
                -amp * o2
            ]

            writer.writerow(
                [
                    t,
                    f"{o1:.6f}",
                    f"{o2:.6f}",
                    alpha,
                    phi
                ]
                + [
                    f"{d:.4f}"
                    for d in delta
                ]
            )

            o1, o2 = so2_step(
                o1,
                o2,
                alpha,
                phi
            )

    print(
        f"Saved {filename}: "
        "SO(2) simulation only"
    )


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--steps",
        type=int,
        default=450
    )

    parser.add_argument(
        "--alpha",
        type=float,
        default=1.1
    )

    parser.add_argument(
        "--phi",
        type=float,
        default=0.2
    )

    parser.add_argument(
        "--output",
        default="cpg_preview.csv"
    )

    args = parser.parse_args()

    run(
        steps=args.steps,
        alpha=args.alpha,
        phi=args.phi,
        filename=args.output
    )
