
"""Robot Lamp - SCS15 Servo Calibration Panel.

Servo IDs: 1, 2, 3, 4
Requires: test_4_scs15.py in the same folder.

Features:
- Individual motor control
- Software Zero
- Position and speed adjustment
- Read P/I/D
- Safety checks
- Torque OFF

PID and EEPROM are READ ONLY.
"""

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

import serial
from serial.tools import list_ports

from test_4_scs15 import (
    BAUD,
    SERVO_IDS,
    read_reg,
    read_u16,
    request,
    send_packet,
    u16,
)

# =====================================
# SETTINGS
# =====================================

ZERO_PATH = Path(__file__).with_name(
    "servo_zeros.json"
)

SAFE_MARGIN = 5
MAX_MOVE_TICKS = 80

TEMP_STOP_C = 55
VOLTAGE_MIN = 6.0
VOLTAGE_MAX = 9.0


# =====================================
# SCS15 COMMUNICATION
# =====================================

def sync_write(ser, address, data_by_id):
    """Write RAM-only Torque or Position."""

    if address not in (0x28, 0x2A):
        raise ValueError(
            "Only RAM Torque/Goal registers are writable"
        )

    if not data_by_id:
        return

    width = len(
        next(iter(data_by_id.values()))
    )

    params = [address, width]

    for sid, payload in data_by_id.items():

        if (
            sid not in SERVO_IDS
            or len(payload) != width
        ):
            raise ValueError(
                "Invalid servo ID or payload length"
            )

        params.extend([sid, *payload])

    send_packet(
        ser,
        0xFE,
        0x83,
        params
    )


# =====================================
# SERVO CONTROL PANEL
# =====================================

class ServoPanel(tk.Tk):

    def __init__(self):

        super().__init__()

        self.title(
            "Robot Lamp | SCS15 Calibration"
        )

        self.geometry("1130x505")
        self.minsize(1020, 475)

        self.ser = None
        self.data = {}
        self.zero = self.load_zeros()
        self.rows = {}

        self.port_name = tk.StringVar()

        self.status = tk.StringVar(
            value="Disconnected | No servo movement"
        )

        self.build_ui()
        self.update_ports()

        self.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )

    # =================================
    # SAVE AND LOAD SOFTWARE ZERO
    # =================================

    @staticmethod
    def load_zeros():

        if not ZERO_PATH.is_file():
            return {}

        try:

            saved = json.loads(
                ZERO_PATH.read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(saved, dict):
                return {}

            return {
                int(k): int(v)
                for k, v in saved.items()
                if (
                    int(k) in SERVO_IDS
                    and 0 <= int(v) <= 1023
                )
            }

        except (
            OSError,
            ValueError,
            TypeError
        ):
            return {}

    def save_zeros(self):

        payload = {
            str(k): v
            for k, v in sorted(
                self.zero.items()
            )
        }

        ZERO_PATH.write_text(
            json.dumps(
                payload,
                indent=2
            ),
            encoding="utf-8"
        )

    # =================================
    # USER INTERFACE
    # =================================

    def build_ui(self):

        top = ttk.Frame(
            self,
            padding=12
        )

        top.pack(fill="x")

        ttk.Label(
            top,
            text="Waveshare COM:"
        ).pack(side="left")

        self.port_box = ttk.Combobox(
            top,
            textvariable=self.port_name,
            width=13,
            state="readonly"
        )

        self.port_box.pack(
            side="left",
            padx=5
        )

        ttk.Button(
            top,
            text="Refresh COM",
            command=self.update_ports
        ).pack(side="left", padx=4)

        ttk.Button(
            top,
            text="Connect",
            command=self.connect
        ).pack(side="left", padx=4)

        ttk.Button(
            top,
            text="Refresh / Read PID",
            command=self.refresh_all
        ).pack(side="left", padx=4)

        ttk.Button(
            top,
            text="TORQUE OFF ALL",
            command=self.torque_off_all
        ).pack(side="right", padx=4)

        ttk.Label(
            self,
            text=(
                "SCS15 ID 1-4 | "
                "Software Zero | "
                "PID READ ONLY | "
                "No EEPROM changes"
            ),
            padding=(16, 5)
        ).pack(anchor="w")

        table = ttk.Frame(
            self,
            padding=12
        )

        table.pack(fill="x")

        headers = [
            "Servo",
            "Actual ticks",
            "Zero ticks",
            "Target relative",
            "Speed",
            "P / I / D",
            "Actions"
        ]

        for col, name in enumerate(headers):

            ttk.Label(
                table,
                text=name,
                font=(
                    "Segoe UI",
                    9,
                    "bold"
                )
            ).grid(
                row=0,
                column=col,
                padx=7,
                pady=(0, 12),
                sticky="w"
            )

        for row_index, sid in enumerate(
            SERVO_IDS,
            start=1
        ):

            actual = tk.StringVar(
                value="-"
            )

            zero = tk.StringVar(
                value=str(
                    self.zero.get(sid, "-")
                )
            )

            target = tk.StringVar(
                value="0"
            )

            speed = tk.StringVar(
                value="80"
            )

            pid = tk.StringVar(
                value="-"
            )

            self.rows[sid] = {
                "actual": actual,
                "zero": zero,
                "target": target,
                "speed": speed,
                "pid": pid
            }

            ttk.Label(
                table,
                text=f"ID {sid}"
            ).grid(
                row=row_index,
                column=0,
                padx=7,
                pady=9
            )

            ttk.Label(
                table,
                textvariable=actual,
                width=14
            ).grid(
                row=row_index,
                column=1,
                padx=7
            )

            ttk.Label(
                table,
                textvariable=zero,
                width=12
            ).grid(
                row=row_index,
                column=2,
                padx=7
            )

            ttk.Entry(
                table,
                textvariable=target,
                width=15
            ).grid(
                row=row_index,
                column=3,
                padx=7
            )

            ttk.Entry(
                table,
                textvariable=speed,
                width=8
            ).grid(
                row=row_index,
                column=4,
                padx=7
            )

            ttk.Label(
                table,
                textvariable=pid,
                width=15
            ).grid(
                row=row_index,
                column=5,
                padx=7
            )

            buttons = ttk.Frame(table)

            buttons.grid(
                row=row_index,
                column=6,
                padx=4
            )

            ttk.Button(
                buttons,
                text="Set Zero",
                command=lambda s=sid:
                    self.set_zero(s)
            ).pack(side="left", padx=2)

            ttk.Button(
                buttons,
                text="Move",
                command=lambda s=sid:
                    self.move(s)
            ).pack(side="left", padx=2)

            ttk.Button(
                buttons,
                text="Torque OFF",
                command=lambda s=sid:
                    self.torque_off(s)
            ).pack(side="left", padx=2)

        ttk.Label(
            self,
            text=(
                "Connect -> Refresh -> Set Zero "
                "-> Target (+20 / -20) -> Move\n"
                f"Maximum movement per command: "
                f"{MAX_MOVE_TICKS} ticks. "
                "Connect and Set Zero never move motors."
            ),
            padding=(16, 12)
        ).pack(anchor="w")

        ttk.Separator(
            self
        ).pack(
            fill="x",
            padx=14,
            pady=5
        )

        ttk.Label(
            self,
            textvariable=self.status,
            padding=12,
            wraplength=1080
        ).pack(anchor="w")

        ttk.Label(
            self,
            text=(
                "WARNING: Torque OFF is not an "
                "emergency stop. "
                "Disconnect servo power if jammed."
            ),
            foreground="#9b3d20",
            padding=(12, 0)
        ).pack(anchor="w")

    # =================================
    # COM PORT
    # =================================

    def update_ports(self):

        ports = list(
            list_ports.comports()
        )

        names = [
            p.device
            for p in ports
        ]

        old = self.port_name.get()

        self.port_box["values"] = names

        if old in names:

            selected = old

        else:

            waveshare = [
                p.device
                for p in ports
                if (
                    (
                        p.vid == 0x1A86
                        and p.pid in (
                            0x55D3,
                            0x7523
                        )
                    )
                    or "CH343" in
                    p.description.upper()
                    or "CH340" in
                    p.description.upper()
                )
            ]

            selected = (
                waveshare[0]
                if len(waveshare) == 1
                else ""
            )

        self.port_name.set(selected)

        self.status.set(
            "Available COM: " +
            (
                ", ".join(names)
                if names
                else "none"
            )
        )

    def need_connection(self):

        if (
            self.ser is None
            or not self.ser.is_open
        ):

            raise RuntimeError(
                "กรุณา Connect Waveshare ก่อน"
            )

        return self.ser

    # =================================
    # READ SERVO HEALTH AND POSITION
    # =================================

    def read_servo(self, sid):

        ser = self.need_connection()

        actual = read_u16(
            ser, sid, 0x38
        )

        low = read_u16(
            ser, sid, 0x09
        )

        high = read_u16(
            ser, sid, 0x0B
        )

        voltage = (
            read_reg(
                ser, sid, 0x3E, 1
            )[0] / 10.0
        )

        temp = read_reg(
            ser, sid, 0x3F, 1
        )[0]

        if not (
            0 <= low < high <= 1023
            and low <= actual <= high
        ):

            raise RuntimeError(
                f"ID {sid}: position {actual} "
                f"outside limits {low}..{high}"
            )

        if not (
            VOLTAGE_MIN
            <= voltage
            <= VOLTAGE_MAX
        ):

            raise RuntimeError(
                f"ID {sid}: unsafe voltage "
                f"{voltage:.1f}V"
            )

        if not (
            0 <= temp < TEMP_STOP_C
        ):

            raise RuntimeError(
                f"ID {sid}: unsafe temperature "
                f"{temp}C"
            )

        result = {
            "actual": actual,
            "low": low,
            "high": high,
            "voltage": voltage,
            "temp": temp
        }

        self.data[sid] = result

        self.rows[sid]["actual"].set(
            f"{actual} ({voltage:.1f}V)"
        )

        return result

    # =================================
    # CONNECT TO SERVOS
    # =================================

    def connect(self):

        port = self.port_name.get().strip()

        if not port:

            messagebox.showwarning(
                "COM port",
                "เลือก COM ของ Waveshare ก่อน"
            )

            return

        try:

            if (
                self.ser
                and self.ser.is_open
            ):

                try:

                    sync_write(
                        self.ser,
                        0x28,
                        {
                            sid: [0]
                            for sid in SERVO_IDS
                        }
                    )

                finally:

                    self.ser.close()

            self.ser = serial.Serial(
                port,
                BAUD,
                timeout=0.025,
                write_timeout=0.5
            )

            for sid in SERVO_IDS:

                request(
                    self.ser,
                    sid,
                    0x01,
                    expected_length=0
                )

            self.status.set(
                f"Connected: {port} | "
                "Servo IDs 1,2,3,4 responding"
            )

            self.refresh_all()

        except Exception as error:

            if self.ser:

                try:
                    self.ser.close()

                except Exception:
                    pass

            self.ser = None

            messagebox.showerror(
                "Connection failed",
                str(error)
            )

    # =================================
    # REFRESH AND READ PID
    # =================================

    def refresh_all(self):

        try:

            self.need_connection()

            for sid in SERVO_IDS:

                self.read_servo(sid)

                # SCS15 internal PID registers
                p = read_reg(
                    self.ser,
                    sid,
                    0x15,
                    1
                )[0]

                d = read_reg(
                    self.ser,
                    sid,
                    0x16,
                    1
                )[0]

                i = read_reg(
                    self.ser,
                    sid,
                    0x17,
                    1
                )[0]

                self.rows[sid]["pid"].set(
                    f"{p} / {i} / {d}"
                )

                self.rows[sid]["zero"].set(
                    str(
                        self.zero.get(
                            sid, "-"
                        )
                    )
                )

            self.status.set(
                "All 4 servos refreshed | "
                "P/I/D READ ONLY"
            )

        except Exception as error:

            messagebox.showerror(
                "Refresh failed",
                str(error)
            )

    # =================================
    # SET SOFTWARE ZERO
    # =================================

    def set_zero(self, sid):

        try:

            actual = self.read_servo(
                sid
            )["actual"]

            self.zero[sid] = actual

            self.save_zeros()

            self.rows[sid]["zero"].set(
                str(actual)
            )

            self.rows[sid]["target"].set(
                "0"
            )

            self.status.set(
                f"ID {sid}: Software Zero "
                f"= {actual} ticks | Saved"
            )

        except Exception as error:

            messagebox.showerror(
                "Set Zero failed",
                str(error)
            )

    # =================================
    # MOVE INDIVIDUAL SERVO
    # =================================

    def move(self, sid):

        torque_may_be_on = False

        try:

            servo = self.read_servo(
                sid
            )

            if sid not in self.zero:

                raise RuntimeError(
                    f"ID {sid}: "
                    "กด Set Zero ก่อน"
                )

            relative = int(
                self.rows[sid][
                    "target"
                ].get().strip()
            )

            speed = int(
                self.rows[sid][
                    "speed"
                ].get().strip()
            )

            if not (
                1 <= speed <= 300
            ):

                raise ValueError(
                    "Speed must be 1..300"
                )

            absolute = (
                self.zero[sid]
                + relative
            )

            safe_low = (
                servo["low"]
                + SAFE_MARGIN
            )

            safe_high = (
                servo["high"]
                - SAFE_MARGIN
            )

            if not (
                safe_low
                <= absolute
                <= safe_high
            ):

                raise RuntimeError(
                    f"ID {sid}: target "
                    f"{absolute} outside "
                    f"{safe_low}..{safe_high}"
                )

            distance = abs(
                absolute
                - servo["actual"]
            )

            if distance > MAX_MOVE_TICKS:

                raise RuntimeError(
                    f"ID {sid}: movement "
                    f"{distance} ticks exceeds "
                    f"{MAX_MOVE_TICKS}"
                )

            if distance == 0:

                self.status.set(
                    f"ID {sid}: "
                    "Already at target"
                )

                return

            confirmed = messagebox.askyesno(
                "Confirm Move",
                (
                    f"Servo ID {sid}\n"
                    f"Actual: {servo['actual']}\n"
                    f"Target: {absolute}\n"
                    f"Relative: {relative:+}\n"
                    f"Speed: {speed}\n\n"
                    "Move now?"
                )
            )

            if not confirmed:
                return

            ser = self.need_connection()

            # Set current goal first.
            sync_write(
                ser,
                0x2A,
                {
                    sid:
                    u16(servo["actual"])
                    + u16(0)
                    + u16(speed)
                }
            )

            torque_may_be_on = True

            # Enable torque for selected motor.
            sync_write(
                ser,
                0x28,
                {sid: [1]}
            )

            # Send movement target.
            sync_write(
                ser,
                0x2A,
                {
                    sid:
                    u16(absolute)
                    + u16(0)
                    + u16(speed)
                }
            )

            self.status.set(
                f"ID {sid}: "
                f"{servo['actual']} -> "
                f"{absolute} ticks | "
                f"Speed={speed} | "
                "Click Refresh to read actual"
            )

        except Exception as error:

            if (
                torque_may_be_on
                and self.ser
                and self.ser.is_open
            ):

                try:

                    sync_write(
                        self.ser,
                        0x28,
                        {sid: [0]}
                    )

                except Exception:
                    pass

            messagebox.showerror(
                "Move blocked / failed",
                str(error)
            )

    # =================================
    # TORQUE OFF INDIVIDUAL
    # =================================

    def torque_off(self, sid):

        try:

            sync_write(
                self.need_connection(),
                0x28,
                {sid: [0]}
            )

            self.status.set(
                f"ID {sid}: "
                "Torque OFF command sent"
            )

        except Exception as error:

            messagebox.showerror(
                "Torque OFF",
                str(error)
            )

    # =================================
    # TORQUE OFF ALL
    # =================================

    def torque_off_all(self):

        try:

            sync_write(
                self.need_connection(),
                0x28,
                {
                    sid: [0]
                    for sid in SERVO_IDS
                }
            )

            self.status.set(
                "Torque OFF ALL command sent"
            )

        except Exception as error:

            messagebox.showerror(
                "Torque OFF ALL",
                str(error)
            )

    # =================================
    # EXIT PROGRAM
    # =================================

    def close(self):

        if (
            self.ser
            and self.ser.is_open
        ):

            try:

                sync_write(
                    self.ser,
                    0x28,
                    {
                        sid: [0]
                        for sid in SERVO_IDS
                    }
                )

            except Exception as error:

                print(
                    "WARNING: Torque OFF failed:",
                    error
                )

            finally:

                self.ser.close()

        self.destroy()


# =====================================
# START PROGRAM
# =====================================

if __name__ == "__main__":
    ServoPanel().mainloop()
