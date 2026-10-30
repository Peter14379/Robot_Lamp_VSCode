
import serial
from test_4_scs15 import send, status, read_reg

PORT = "COM4"
BAUD = 1000000

NEW_ID = 1
KNOWN_IDS = [2]


def ping(ser, sid):
    send(ser, sid, 0x01)
    return status(ser, sid, timeout=0.12) is not None


def write_byte(ser, sid, address, value):
    send(ser, sid, 0x03, [address, value])
    status(ser, sid, timeout=0.06)


def main():
    print("=== CHANGE SCS15 SERVO ID ===")
    print("Connect ONE servo only.")

    with serial.Serial(PORT, BAUD, timeout=0.025) as ser:
        found = [sid for sid in KNOWN_IDS if ping(ser, sid)]

        print("Detected IDs:", found)

        if len(found) != 1:
            print("[STOP] Expected exactly one responding ID.")
            return

        old_id = found[0]

        if ping(ser, NEW_ID):
            print("[STOP] New ID is already in use.")
            return

        stored = read_reg(ser, old_id, 0x05, 1)[0]

        if stored != old_id:
            print("[STOP] Stored ID does not match.")
            return

        print(f"Change ID {old_id} -> {NEW_ID}")
        confirm = input("Type CHANGE to confirm: ").strip()

        if confirm != "CHANGE":
            print("[CANCELLED]")
            return

        # Unlock EEPROM protection.
        write_byte(ser, old_id, 0x30, 0)

        unlocked = read_reg(ser, old_id, 0x30, 1)[0]
        if unlocked != 0:
            print("[STOP] Cannot unlock EEPROM.")
            return

        # Save the new servo ID.
        write_byte(ser, old_id, 0x05, NEW_ID)

        try:
            # Verify through the new ID.
            new_stored_id = read_reg(ser, NEW_ID, 0x05, 1)[0]

            if new_stored_id != NEW_ID:
                print("[STOP] ID verification failed.")
                return

            print(f"[SUCCESS] ID changed to {NEW_ID}")

        finally:
            # Restore EEPROM write protection.
            for sid in (NEW_ID, old_id):
                if ping(ser, sid):
                    write_byte(ser, sid, 0x30, 1)
                    print("[LOCK] EEPROM protection restored")
                    break


if __name__ == "__main__":
    try:
        main()
    except (serial.SerialException, RuntimeError) as error:
        print("[ERROR]", error)
