
"""
Robot Lamp - Test 4 FEETECH SCS15 Servo Motors

Hardware:
PC USB -> Waveshare Bus Servo Adapter (A)
Jumper -> B (USB mode)
External servo power -> verified 6-9V
Servo IDs -> 1, 2, 3, 4

Features:
- Detect and ignore UART Echo
- Verify real servo responses
- Read position and safety feedback
- Test small motor movement after confirmation
- Return to starting positions
- Disable torque after testing

No changes to servo IDs, PID, or EEPROM.

Run:
py -3.11 tools\\test_4_scs15.py
"""

import time
import serial
from serial.tools import list_ports


# ==========================================
# 1. USER SETTINGS
# ==========================================

PORT = "COM4"
BAUD = 1000000

SERVO_IDS = (1, 2, 3, 4)

TEST_STEPS = 10
TEST_SPEED = 80

RETRIES = 4
RESPONSE_TIMEOUT = 0.30

SAFE_MARGIN = 5


# ==========================================
# 2. FIND WAVESHARE COM PORT
# ==========================================

def choose_port():

    ports = list(list_ports.comports())

    names = [
        p.device.upper()
        for p in ports
    ]

    if PORT.upper() != "AUTO":

        if PORT.upper() not in names:

            print("Available COM ports:")

            for p in ports:
                print(p.device, "-", p.description)

            raise RuntimeError(
                f"{PORT} not found. Check Waveshare USB."
            )

        return PORT

    matches = [
        p.device
        for p in ports
        if (
            p.vid == 0x1A86
            and p.pid in (0x55D3, 0x7523)
        )
        or "CH343" in p.description.upper()
        or "CH340" in p.description.upper()
    ]

    if len(matches) == 1:
        return matches[0]

    print("Available COM ports:")

    for p in ports:
        print(p.device, "-", p.description)

    raise RuntimeError(
        "Select the Waveshare COM port in PORT."
    )


# ==========================================
# 3. SEND PACKET
# ==========================================

def send_packet(
    ser,
    servo_id,
    instruction,
    params=()
):

    values = [
        servo_id,
        len(params) + 2,
        instruction,
        *params
    ]

    checksum = (~sum(values)) & 0xFF

    packet = bytes([
        0xFF,
        0xFF,
        *values,
        checksum
    ])

    ser.reset_input_buffer()

    ser.write(packet)
    ser.flush()

    return packet


# ==========================================
# 4. READ STATUS AND IGNORE ECHO
# ==========================================

def read_status(
    ser,
    wanted_id,
    timeout=RESPONSE_TIMEOUT,
    echo_packet=None
):

    deadline = time.monotonic() + timeout

    buffer = bytearray()

    while time.monotonic() < deadline:

        next_byte = ser.read(1)

        if not next_byte:
            continue

        buffer.extend(next_byte)

        while len(buffer) >= 4:

            # Search for FF FF header.
            start = buffer.find(b"\xFF\xFF")

            if start < 0:

                # Preserve a possible first FF.
                buffer[:] = (
                    b"\xFF"
                    if buffer[-1] == 0xFF
                    else b""
                )

                break

            if start > 0:
                del buffer[:start]

            if len(buffer) < 4:
                break

            length = buffer[3]

            if not 2 <= length <= 32:

                del buffer[0]
                continue

            total_size = length + 4

            if len(buffer) < total_size:
                break

            packet = bytes(
                buffer[:total_size]
            )

            del buffer[:total_size]

            # Validate checksum.
            checksum = (
                sum(packet[2:]) & 0xFF
            )

            if checksum != 0xFF:
                continue

            # Skip an exact copy of TX packet.
            if (
                echo_packet is not None
                and packet == echo_packet
            ):

                print(
                    f"[ECHO] ID {wanted_id}: "
                    "ignoring transmitted packet"
                )

                continue

            # Ignore responses from other IDs.
            if packet[2] != wanted_id:
                continue

            # A valid status packet.
            error = packet[4]
            data = packet[5:-1]

            return error, data

    return None


# ==========================================
# 5. REQUEST SERVO RESPONSE
# ==========================================

def request(
    ser,
    sid,
    instruction,
    params=(),
    expected_length=0
):

    for attempt in range(1, RETRIES + 1):

        sent_packet = send_packet(
            ser,
            sid,
            instruction,
            params
        )

        reply = read_status(
            ser,
            sid,
            echo_packet=sent_packet
        )

        if reply is None:

            print(
                f"[RETRY] ID {sid}: "
                f"no real response "
                f"({attempt}/{RETRIES})"
            )

            time.sleep(0.05)
            continue

        error, data = reply

        if error != 0:

            raise RuntimeError(
                f"ID {sid}: real servo "
                f"status error 0x{error:02X}"
            )

        if len(data) == expected_length:
            return data

        print(
            f"[RETRY] ID {sid}: "
            f"unexpected response length "
            f"{len(data)}"
        )

        time.sleep(0.05)

    raise RuntimeError(
        f"ID {sid}: no valid servo response "
        f"after {RETRIES} attempts. "
        "Do not run motor movement."
    )


# ==========================================
# 6. READ SERVO REGISTERS
# ==========================================

def read_reg(
    ser,
    sid,
    address,
    size
):

    return request(
        ser,
        sid,
        0x02,
        (address, size),
        size
    )


def read_u16(
    ser,
    sid,
    address
):

    raw = read_reg(
        ser,
        sid,
        address,
        2
    )

    # SCS15 high byte first.
    return int.from_bytes(
        raw,
        "big"
    )


def u16(value):

    return list(
        int(value).to_bytes(
            2,
            "big"
        )
    )


# ==========================================
# 7. SYNCHRONIZED SERVO WRITE
# ==========================================

def sync_write(
    ser,
    address,
    data_by_id
):

    # Allow only RAM torque and goal registers.
    if address not in (0x28, 0x2A):

        raise ValueError(
            "Only Torque and Goal RAM "
            "registers are writable"
        )

    if not data_by_id:

        raise ValueError(
            "No servo data"
        )

    width = len(
        next(iter(data_by_id.values()))
    )

    if (
        address == 0x28
        and width != 1
    ) or (
        address == 0x2A
        and width != 6
    ):

        raise ValueError(
            "Unexpected SYNC_WRITE width"
        )

    params = [address, width]

    for sid, payload in data_by_id.items():

        if (
            sid not in SERVO_IDS
            or len(payload) != width
        ):

            raise ValueError(
                "Invalid servo ID or payload width"
            )

        params.extend([
            sid,
            *payload
        ])

    send_packet(
        ser,
        0xFE,
        0x83,
        params
    )


# ==========================================
# 8. WRITE SERVO POSITIONS
# ==========================================

def write_positions(
    ser,
    positions
):

    data = {}

    for sid in SERVO_IDS:

        position = positions[sid]

        data[sid] = (
            u16(position)
            + u16(0)
            + u16(TEST_SPEED)
        )

    sync_write(
        ser,
        0x2A,
        data
    )


# ==========================================
# 9. TORQUE CONTROL
# ==========================================

def torque(
    ser,
    enable
):

    data = {
        sid: [1 if enable else 0]
        for sid in SERVO_IDS
    }

    sync_write(
        ser,
        0x28,
        data
    )


# ==========================================
# 10. MAIN PROGRAM
# ==========================================

def main():

    port = choose_port()

    print()
    print("====================================")
    print(" ROBOT LAMP - FOUR SCS15 SERVOS")
    print("====================================")

    print("COM PORT :", port)
    print("BAUD RATE:", BAUD)
    print("SERVO IDs:", SERVO_IDS)

    print()
    print("[STEP 1] Checking all servos")
    print("No movement commands yet.")

    with serial.Serial(
        port,
        BAUD,
        timeout=0.025,
        write_timeout=0.5
    ) as ser:

        time.sleep(0.2)

        # ----------------------------------
        # CHECK ALL SERVO IDs
        # ----------------------------------

        for sid in SERVO_IDS:

            request(
                ser,
                sid,
                0x01,
                expected_length=0
            )

            print(
                f"[FOUND] Servo ID {sid}"
            )

        print()
        print("[OK] Four servos responding")

        # ----------------------------------
        # READ SERVO FEEDBACK
        # ----------------------------------

        initial = {}
        targets = {}

        print()
        print("[STEP 2] Reading positions")

        for sid in SERVO_IDS:

            pos = read_u16(
                ser,
                sid,
                0x38
            )

            min_pos = read_u16(
                ser,
                sid,
                0x09
            )

            max_pos = read_u16(
                ser,
                sid,
                0x0B
            )

            voltage = (
                read_reg(
                    ser,
                    sid,
                    0x3E,
                    1
                )[0] / 10.0
            )

            temp = read_reg(
                ser,
                sid,
                0x3F,
                1
            )[0]

            print("--------------------------------")

            print(f"SERVO ID : {sid}")
            print(f"Position : {pos}")
            print(f"Min pos  : {min_pos}")
            print(f"Max pos  : {max_pos}")
            print(f"Voltage  : {voltage:.1f} V")
            print(f"Temp     : {temp} C")

            # Check valid position and limits.
            if not (
                0 <= min_pos < max_pos <= 1023
                and min_pos <= pos <= max_pos
            ):

                raise RuntimeError(
                    f"ID {sid}: invalid "
                    "position or limits. "
                    "No movement."
                )

            # Check voltage.
            if not 6.0 <= voltage <= 9.0:

                raise RuntimeError(
                    f"ID {sid}: voltage "
                    "outside 6-9V. "
                    "No movement."
                )

            # Check temperature.
            if not 0 <= temp < 55:

                raise RuntimeError(
                    f"ID {sid}: unsafe temperature. "
                    "No movement."
                )

            # Calculate small safe movement.
            if (
                pos + TEST_STEPS
                <= max_pos - SAFE_MARGIN
            ):

                target = pos + TEST_STEPS

            elif (
                pos - TEST_STEPS
                >= min_pos + SAFE_MARGIN
            ):

                target = pos - TEST_STEPS

            else:

                raise RuntimeError(
                    f"ID {sid}: insufficient "
                    "safe movement range."
                )

            initial[sid] = pos
            targets[sid] = target

        # ----------------------------------
        # CONFIRM TEST
        # ----------------------------------

        print()
        print("====================================")
        print("[READY] ALL FOUR SERVOS CHECKED")
        print("====================================")

        for sid in SERVO_IDS:

            print(
                f"ID {sid}: "
                f"{initial[sid]} -> "
                f"{targets[sid]} -> "
                f"{initial[sid]}"
            )

        print()
        print("TEST SETTINGS")

        print("Movement:", TEST_STEPS, "ticks")
        print("Speed   :", TEST_SPEED)
        print("Cycle   : 1")

        print()
        print("WARNING:")
        print("Remove servo horns and mechanical loads.")
        print("Keep hands clear of servo shafts.")

        confirm = input(
            "\nType MOVE to start testing: "
        ).strip()

        if confirm != "MOVE":

            print(
                "[CANCELLED] No movement"
            )

            return

        # ----------------------------------
        # MOVE ALL SERVOS
        # ----------------------------------

        torque_attempted = False
        motion_started = False

        try:

            print()
            print("[STEP 3] Preparing motors")

            # Set current position as goal.
            write_positions(
                ser,
                initial
            )

            time.sleep(0.2)

            # Enable torque.
            torque_attempted = True

            torque(
                ser,
                True
            )

            time.sleep(0.25)

            # Move to test positions.
            write_positions(
                ser,
                targets
            )

            motion_started = True

            print()
            print("[MOVE] FOUR MOTORS TOGETHER")

            time.sleep(1.2)

            # ----------------------------------
            # READ POSITION FEEDBACK
            # ----------------------------------

            print()
            print("[STEP 4] Motor feedback")

            for sid in SERVO_IDS:

                actual = read_u16(
                    ser,
                    sid,
                    0x38
                )

                moved = abs(
                    actual - initial[sid]
                )

                print(
                    f"ID {sid}: "
                    f"position={actual}, "
                    f"moved={moved} ticks"
                )

                if moved < 3:

                    print(
                        f"[WARNING] ID {sid}: "
                        "movement not confirmed"
                    )

        finally:

            if torque_attempted:

                # ----------------------------------
                # RETURN TO INITIAL POSITIONS
                # ----------------------------------

                if motion_started:

                    try:

                        print()
                        print("[RETURN] Returning motors")

                        write_positions(
                            ser,
                            initial
                        )

                        time.sleep(1.2)

                        for sid in SERVO_IDS:

                            final_pos = read_u16(
                                ser,
                                sid,
                                0x38
                            )

                            print(
                                f"[FINAL] ID {sid}: "
                                f"{final_pos}"
                            )

                    except Exception as error:

                        print(
                            "[WARNING] Return or "
                            "feedback failed:",
                            error
                        )

                # ----------------------------------
                # ALWAYS ATTEMPT TORQUE OFF
                # ----------------------------------

                try:

                    torque(
                        ser,
                        False
                    )

                    print()
                    print(
                        "[TORQUE OFF] Command sent"
                    )

                except Exception as error:

                    print(
                        "[WARNING] Torque OFF "
                        "command failed:",
                        error
                    )

                    print(
                        "Disconnect servo power "
                        "safely if necessary."
                    )

        print()
        print("====================================")
        print("[DONE] FOUR SERVO TEST FINISHED")
        print("====================================")


# ==========================================
# 11. PROGRAM START
# ==========================================

if __name__ == "__main__":

    try:

        main()

    except (
        RuntimeError,
        ValueError,
        serial.SerialException
    ) as error:

        print()
        print("[STOP]", error)

    except KeyboardInterrupt:

        print()
        print("[STOP] Interrupted by user")

        print(
            "Disconnect servo power "
            "safely if necessary."
        )
