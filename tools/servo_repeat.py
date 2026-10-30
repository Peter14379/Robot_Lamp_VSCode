
"""Robot Lamp - Repeat 4 SCS15 servo movements.

Run:
    py tools\servo_repeat.py

Stop:
    Ctrl+C in the PowerShell terminal

Uses tested functions from test_4_scs15.py.
No servo IDs or EEPROM parameters are changed.
"""

import time
import serial

from test_4_scs15 import (
    choose_port,
    BAUD,
    SERVO_IDS,
    request,
    read_reg,
    read_u16,
    sync_write,
    u16,
)

# =============== SETTINGS ===============

MOVE_STEPS = 100      # หมุนประมาณ 20 องศา
SPEED = 120           # ความเร็วเท่าเดิม
HOLD_SECONDS = 3.0    # ค้างแต่ละตำแหน่ง 3 วินาที

CYCLES = 5            # ทดลอง 5 รอบก่อน
MIN_PROGRESS = 6
MAX_TEMPERATURE = 55
SAFE_MARGIN = 5

# ========================================


def send_positions(ser, positions):
    data = {}

    for sid in SERVO_IDS:
        data[sid] = (
            u16(positions[sid])
            + u16(0)
            + u16(SPEED)
        )

    sync_write(ser, 0x2A, data)


def set_torque(ser, enabled):
    sync_write(
        ser,
        0x28,
        {
            sid: [1 if enabled else 0]
            for sid in SERVO_IDS
        }
    )


def read_positions_and_health(ser):
    positions = {}

    for sid in SERVO_IDS:
        pos = read_u16(ser, sid, 0x38)
        voltage = read_reg(ser, sid, 0x3E, 1)[0] / 10
        temp = read_reg(ser, sid, 0x3F, 1)[0]

        if not 0 <= pos <= 1023:
            raise RuntimeError(
                f"ID {sid}: invalid position {pos}"
            )

        if not 6.0 <= voltage <= 9.0:
            raise RuntimeError(
                f"ID {sid}: unsafe voltage {voltage}V"
            )

        if temp >= MAX_TEMPERATURE:
            raise RuntimeError(
                f"ID {sid}: temperature {temp}C"
            )

        positions[sid] = pos

    return positions


def verify_movement(before, after, destination):
    for sid in SERVO_IDS:
        expected = destination[sid] - before[sid]
        actual = after[sid] - before[sid]

        # Do not demand movement if already near the goal.
        if abs(expected) < MIN_PROGRESS:
            continue

        direction = 1 if expected > 0 else -1
        progress = actual * direction

        print(
            f"ID {sid}: {before[sid]} -> {after[sid]}"
            f" | target={destination[sid]}"
            f" | progress={progress}"
        )

        if progress < MIN_PROGRESS:
            raise RuntimeError(
                f"ID {sid}: movement not confirmed. "
                "Test stopped to protect the motor."
            )


def move_and_check(ser, destination):
    before = read_positions_and_health(ser)

    send_positions(ser, destination)

    time.sleep(HOLD_SECONDS)

    after = read_positions_and_health(ser)

    verify_movement(before, after, destination)


def main():
    port = choose_port()

    print("====================================")
    print(" ROBOT LAMP - REPEAT 4 SCS15 SERVOS")
    print("====================================")
    print("PORT:", port)
    print("BAUD:", BAUD)
    print("IDs:", SERVO_IDS)

    with serial.Serial(
        port,
        BAUD,
        timeout=0.025,
        write_timeout=0.5
    ) as ser:

        time.sleep(0.3)

        initial = {}
        target = {}

        print("\n[CHECK] Reading all four servos")

        for sid in SERVO_IDS:
            request(
                ser,
                sid,
                0x01,
                expected_length=0
            )

        current = read_positions_and_health(ser)

        for sid in SERVO_IDS:
            pos = current[sid]
            minimum = read_u16(ser, sid, 0x09)
            maximum = read_u16(ser, sid, 0x0B)

            if not (
                0 <= minimum < maximum <= 1023
                and minimum <= pos <= maximum
            ):
                raise RuntimeError(
                    f"ID {sid}: invalid position/limits"
                )

            # Select direction away from the nearby limit.
            if pos + MOVE_STEPS <= maximum - SAFE_MARGIN:
                goal = pos + MOVE_STEPS

            elif pos - MOVE_STEPS >= minimum + SAFE_MARGIN:
                goal = pos - MOVE_STEPS

            else:
                raise RuntimeError(
                    f"ID {sid}: insufficient safe range"
                )

            initial[sid] = pos
            target[sid] = goal

            print(
                f"ID {sid}: {pos} <-> {goal}"
                f" | limits={minimum}-{maximum}"
            )

        print("\nAll four servos passed initial checks.")
        print("Keep shafts unloaded and motors secure.")
        print("The test repeats until Ctrl+C.")
        print("Stop immediately if a motor strains or stalls.")

        confirm = input(
            "\nType START to begin: "
        ).strip()

        if confirm != "START":
            print("[CANCELLED]")
            return

        torque_may_be_on = False

        try:
            # Prepare goals before enabling torque.
            send_positions(ser, initial)
            time.sleep(0.2)

            torque_may_be_on = True
            set_torque(ser, True)
            time.sleep(0.3)

            cycle = 0

            while CYCLES == 0 or cycle < CYCLES:
                cycle += 1

                print(f"\n=== CYCLE {cycle} ===")

                print("[FORWARD] Moving together")
                move_and_check(ser, target)

                print("[BACKWARD] Returning together")
                move_and_check(ser, initial)

                print(f"[DONE] Cycle {cycle}")

        finally:
            if torque_may_be_on:
                try:
                    set_torque(ser, False)
                    print("\n[TORQUE OFF] Command sent")
                except Exception as error:
                    print("[WARNING] Torque OFF failed:", error)
                    print("Switch off servo power immediately.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] User requested stop.")
    except (
        RuntimeError,
        ValueError,
        serial.SerialException
    ) as error:
        print("\n[STOP]", error)
        print("Check motors before restarting.")
