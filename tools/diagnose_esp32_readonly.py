
"""
Robot Lamp V1.8 - ESP32 Serial Diagnostic
READ ONLY: SCAN, READPOS, READINFO

Run:
py -3.11 tools/diagnose_esp32_readonly.py --port COM3
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Install: py -3.11 -m pip install pyserial")
    raise SystemExit(2)


COMMANDS = (
    ("SCAN", "PING TEST COMPLETE (NO MOVEMENT)", 9),
    ("READPOS", "READ COMPLETE (NO MOVEMENT)", 9),
    ("READINFO", "READINFO COMPLETE (NO MOVEMENT)", 18),
)


def show_ports():
    ports = list(list_ports.comports())

    print(
        "Available ports:",
        ", ".join(
            f"{p.device} ({p.description})"
            for p in ports
        ) or "(none)"
    )


def collect(ser, command, end_text, limit, emit):
    print(
        f"\n========== {command} (read-only) =========="
    )

    ser.reset_input_buffer()

    ser.write(
        (command + "\n").encode("ascii")
    )

    ser.flush()

    lines = []
    deadline = time.monotonic() + limit

    while time.monotonic() < deadline:
        raw = ser.readline()

        if not raw:
            continue

        line = raw.decode(
            "utf-8",
            errors="replace"
        ).strip()

        if not line:
            continue

        lines.append(line)
        emit(line)

        if end_text in line:
            break

    else:
        print(
            f"[DIAGNOSTIC] timeout: "
            f"no completion marker for {command}"
        )

    return lines


def analyze(result):
    scan = result.get("SCAN", [])
    pos = result.get("READPOS", [])
    info = result.get("READINFO", [])

    found = sorted({
        int(m[1])
        for ln in scan
        if (
            m := re.search(
                r"\[FOUND\] Servo ID (\d+)",
                ln
            )
        )
    })

    positions = {
        int(m[1]): int(m[2])
        for ln in pos
        if (
            m := re.search(
                r"\[POSITION\] Servo ID (\d+) = (\d+) ticks",
                ln
            )
        )
    }

    infos = sorted({
        int(m[1])
        for ln in info
        if (
            m := re.search(
                r"\[INFO\] ID=(\d+) MIN=",
                ln
            )
        )
    })

    print("\n========== RESULT ==========")

    print(f"SCAN IDs: {found}")
    print(f"POSITION: {positions}")
    print(f"INFO IDs: {infos}")

    all_ids = {1, 2, 3, 4}

    if not any(result.values()):
        print(
            "No serial response: check COM port, "
            "USB data cable, baud=115200, firmware, "
            "and that Flask is stopped."
        )
        return 1

    if (
        set(positions) != all_ids
        or set(infos) != all_ids
    ):
        print(
            "Some SCS15 IDs are missing. "
            "Check servo power/baud, common GND, "
            "RX/TX wiring, half-duplex bus interface, "
            "assigned IDs and adapter directions."
        )

        print(
            "Do NOT command motor movement "
            "while feedback is incomplete."
        )

        return 1

    print(
        "All four SCS15 servos responded to "
        "READPOS and READINFO. "
        "Safe to continue with calibration checks "
        "(not movement)."
    )

    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Robot Lamp V1.8 "
            "read-only serial diagnostic"
        )
    )

    parser.add_argument(
        "--port",
        default="COM3"
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200
    )

    parser.add_argument(
        "--save",
        default=None,
        help="Optional transcript output .txt file"
    )

    args = parser.parse_args()

    show_ports()

    output = []

    def emit(line):
        print(line)
        output.append(line)

    result = {}

    try:
        with serial.Serial(
            args.port,
            args.baud,
            timeout=0.15,
            write_timeout=1.0
        ) as ser:

            print(
                "Opened",
                args.port,
                "at",
                args.baud,
                "baud. Waiting for ESP32 reset..."
            )

            time.sleep(2.2)

            for cmd, marker, timeout in COMMANDS:
                result[cmd] = collect(
                    ser,
                    cmd,
                    marker,
                    timeout,
                    emit
                )

    except (
        serial.SerialException,
        OSError
    ) as err:

        print("[PORT ERROR]", err)

        print(
            "Close Flask, PlatformIO Serial Monitor "
            "and camera programs before testing COM3."
        )

        return 2

    finally:
        if args.save:
            path = Path(args.save)

            path.write_text(
                "\n".join(output) + "\n",
                encoding="utf-8"
            )

            print(
                "Saved transcript to",
                path
            )

    return analyze(result)


if __name__ == "__main__":
    sys.exit(main())
