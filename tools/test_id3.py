
"""Robot Lamp - Test SCS15 Servo ID 3 only."""

import time
import serial

from test_4_scs15 import (
    choose_port,
    BAUD,
    read_u16,
    read_reg,
    send_packet,
    u16,
)

SERVO_ID = 3
TARGET = 100
SPEED = 50


def write_register(ser, address, values):
    send_packet(
        ser,
        SERVO_ID,
        0x03,
        [address, *values]
    )
    time.sleep(0.05)


def main():
    print("=== SERVO ID 3 MOVEMENT TEST ===")

    with serial.Serial(
        choose_port(),
        BAUD,
        timeout=0.025,
        write_timeout=0.5
    ) as ser:

        time.sleep(0.3)

        position = read_u16(ser, SERVO_ID, 0x38)
        minimum = read_u16(ser, SERVO_ID, 0x09)
        maximum = read_u16(ser, SERVO_ID, 0x0B)
        voltage = read_reg(ser, SERVO_ID, 0x3E, 1)[0] / 10
        temperature = read_reg(ser, SERVO_ID, 0x3F, 1)[0]

        print("Current position:", position)
        print("Position limits:", minimum, maximum)
        print("Voltage:", voltage, "V")
        print("Temperature:", temperature, "C")
        print("Target:", TARGET)

        # Stop if values differ significantly from earlier checks.
        if not (
            0 <= position <= 80
            and minimum == 20
            and maximum == 1003
            and minimum <= TARGET <= maximum
            and 6.0 <= voltage <= 9.0
            and 0 <= temperature < 65
        ):
            print("[STOP] Unsafe or unexpected servo data.")
            return

        print("\nTest only SERVO ID 3.")
        print("Make sure the shaft is unloaded and clear.")

        confirm = input("Type MOVE3 to test: ").strip()

        if confirm != "MOVE3":
            print("[CANCELLED]")
            return

        try:
            # Set the goal before enabling torque.
            write_register(
                ser,
                0x2A,
                u16(TARGET) + u16(0) + u16(SPEED)
            )

            # Enable servo torque.
            write_register(ser, 0x28, [1])

            print("\n[MOVE] ID 3 -> Position 50")

            for step in range(5):
                time.sleep(0.4)

                actual = read_u16(
                    ser,
                    SERVO_ID,
                    0x38
                )

                print(
                    f"[FEEDBACK {step + 1}] "
                    f"Position = {actual}"
                )

            print("\n[COMPLETE] Test finished")

        finally:
            # Release motor torque.
            try:
                write_register(ser, 0x28, [0])
                print("[TORQUE OFF] Command sent")
            except Exception as error:
                print("[WARNING] Torque OFF failed:", error)
                print("Turn off servo power immediately.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, serial.SerialException) as error:
        print("[STOP]", error)
    except KeyboardInterrupt:
        print("[STOP] Interrupted. Turn off servo power.")
