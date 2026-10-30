
import time
import serial

from test_4_scs15 import (
    choose_port,
    BAUD,
    read_u16,
    read_reg,
)

with serial.Serial(
    choose_port(),
    BAUD,
    timeout=0.025,
    write_timeout=0.5,
) as ser:

    time.sleep(0.2)

    print("=== READ ALL 4 SERVOS ===")
    print("READ ONLY - NO MOTOR MOVEMENT")

    for sid in (1, 2, 3, 4):
        try:
            pos = read_u16(ser, sid, 0x38)
            low = read_u16(ser, sid, 0x09)
            high = read_u16(ser, sid, 0x0B)
            voltage = read_reg(ser, sid, 0x3E, 1)[0] / 10

            print(
                f"ID {sid}: "
                f"Position={pos}, "
                f"Min={low}, Max={high}, "
                f"Voltage={voltage:.1f}V"
            )

            if not low <= pos <= high:
                print(f"[WARNING] ID {sid} outside limits")

        except Exception as error:
            print(f"[ERROR] ID {sid}: {error}")

print("=== CHECK COMPLETE ===")
