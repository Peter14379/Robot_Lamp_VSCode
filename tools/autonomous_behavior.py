
"""Robot Lamp - Animal-Like Autonomous Behavior.

Internal drives:
    Curiosity
    Energy
    Arousal

Behavior modes:
    REST
    EXPLORE
    ENGAGED

Uses SO(2) CPG for exploratory movement.
No fixed inactivity-to-sleep timer.
"""

from dataclasses import dataclass
import json
import math
import queue
import threading
import time

from urllib.request import Request, urlopen


# ==========================================
# UTILITY
# ==========================================

def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class Behavior:
    mode: str
    expression: str
    look: str | None
    curiosity: float
    energy: float
    arousal: float


# ==========================================
# AUTONOMOUS BEHAVIOR CONTROLLER
# ==========================================

class AnimalAutonomy:

    def __init__(self):

        self.mode = "REST"

        # Initial internal states
        self.curiosity = 0.24
        self.energy = 0.78
        self.arousal = 0.12

        self.last_time = None

        # SO(2) CPG oscillator
        self.o1 = 0.15
        self.o2 = 0.0

        self.osc_accum = 0.0
        self.last_look = "CENTER"

    # ======================================
    # SO(2) CPG OSCILLATOR
    # ======================================

    def _cpg(self, dt):

        # Curiosity receptor
        r = self.curiosity / (
            0.27 + self.curiosity
        )

        # Receptor controls oscillation
        phi = 0.09 + 0.15 * r
        alpha = 1.12

        self.osc_accum += dt

        while self.osc_accum >= 0.10:

            self.osc_accum -= 0.10

            c = math.cos(phi)
            s = math.sin(phi)

            a = self.o1
            b = self.o2

            self.o1 = math.tanh(
                alpha * (c * a + s * b)
            )

            self.o2 = math.tanh(
                alpha * (-s * a + c * b)
            )

        return self.o1

    # ======================================
    # UPDATE AUTONOMOUS BEHAVIOR
    # ======================================

    def update(
        self,
        now,
        base_emotion,
        hand_present=False,
        voice_event=False
    ):

        now = float(now)

        if self.last_time is None:
            dt = 0.0

        else:
            dt = clamp(
                now - self.last_time,
                0.0,
                0.2
            )

        self.last_time = now

        # Hand, face or voice input
        contact = (
            hand_present
            or voice_event
            or base_emotion != "IDLE"
        )

        look = None

        # ==================================
        # ENGAGED MODE
        # ==================================

        if contact:

            self.mode = "ENGAGED"

            # FIX 1:
            # Curiosity approaches 0.50.
            # It no longer decreases to zero.
            self.curiosity = clamp(
                self.curiosity
                + dt * 0.06
                * (0.50 - self.curiosity)
            )

            # FIX 2:
            # Arousal approaches 0.70.
            # It no longer stays at 1.00.
            self.arousal = clamp(
                self.arousal
                + dt * 0.40
                * (0.70 - self.arousal)
            )

            # FIX 3:
            # Energy approaches 0.55.
            # It no longer drains to zero
            # during continuous interaction.
            self.energy = clamp(
                self.energy
                + dt * 0.04
                * (0.55 - self.energy)
            )

            # Stop issuing automatic head
            # movements during interaction.
            self.last_look = "CENTER"

            return Behavior(
                mode=self.mode,
                expression=base_emotion,
                look=None,
                curiosity=self.curiosity,
                energy=self.energy,
                arousal=self.arousal
            )

        # ==================================
        # INTERNAL DRIVE DYNAMICS
        # ==================================

        # Arousal decreases without stimulus.
        self.arousal = clamp(
            self.arousal - 0.08 * dt
        )

        # Return to rest after interaction.
        if self.mode == "ENGAGED":
            self.mode = "REST"

        # ==================================
        # EXPLORATION MODE
        # ==================================

        if self.mode == "EXPLORE":

            # Exploration consumes energy.
            self.energy = clamp(
                self.energy
                - (
                    0.11
                    + 0.018 * self.arousal
                ) * dt
            )

            # Curiosity decreases as
            # the robot explores.
            self.curiosity = clamp(
                self.curiosity - 0.09 * dt
            )

            # Leave exploration when tired
            # or curiosity becomes low.
            if (
                self.energy < 0.28
                or self.curiosity < 0.24
            ):

                self.mode = "REST"

                if self.last_look != "CENTER":
                    look = "CENTER"

                self.last_look = "CENTER"

        # ==================================
        # REST AND RECOVERY MODE
        # ==================================

        if self.mode == "REST":

            # Recover energy.
            self.energy = clamp(
                self.energy + 0.035 * dt
            )

            # Curiosity grows naturally.
            self.curiosity = clamp(
                self.curiosity + 0.065 * dt
            )

            # Start exploring when ready.
            if (
                self.energy > 0.46
                and self.curiosity > 0.59
            ):
                self.mode = "EXPLORE"

        # ==================================
        # AUTONOMOUS HEAD MOVEMENT
        # ==================================

        if self.mode == "EXPLORE":

            wave = self._cpg(dt)

            if wave > 0.46:
                new_look = "RIGHT"

            elif wave < -0.46:
                new_look = "LEFT"

            else:
                new_look = "CENTER"

            # Report only when look changes.
            if new_look != self.last_look:

                look = new_look
                self.last_look = new_look

            # Playful expression
            if self.curiosity > 0.40:
                expression = "HAPPY"

            else:
                expression = "IDLE"

        else:

            # SLEEPY depends on simulated energy,
            # not an inactivity timer.
            if self.energy < 0.36:
                expression = "SLEEPY"

            else:
                expression = "IDLE"

        return Behavior(
            mode=self.mode,
            expression=expression,
            look=look,
            curiosity=self.curiosity,
            energy=self.energy,
            arousal=self.arousal
        )


# ==========================================
# OPTIONAL REAL SERVO HEAD MOVEMENT
# ==========================================

class LookBridge:
    """
    Send LEFT / CENTER / RIGHT movements
    to the Flask Dashboard.

    The head-yaw servo must be identified
    and mechanically calibrated first.

    Uses:
        /api/status
        /api/servo/<id>/move

    Does not provide collision avoidance
    or a hardware emergency stop.
    """

    def __init__(
        self,
        servo_id,
        amplitude=3,
        base_url="http://127.0.0.1:5000"
    ):

        if servo_id not in (1, 2, 3, 4):
            raise ValueError(
                "Servo ID must be 1, 2, 3 or 4"
            )

        if not 1 <= amplitude <= 5:
            raise ValueError(
                "Amplitude must be 1..5 ticks"
            )

        self.servo_id = servo_id
        self.amplitude = amplitude

        self.base_url = base_url.rstrip("/")

        # Initial servo center
        self.center = None

        # Only keep latest movement request
        self.queue = queue.Queue(maxsize=1)

        self.stop_event = threading.Event()
        self.paused = threading.Event()

        self.last_move = 0.0

        # Start background movement worker
        self.thread = threading.Thread(
            target=self._worker,
            daemon=True
        )

        self.thread.start()

    # ======================================
    # PAUSE AUTONOMOUS LOOK
    # ======================================

    def set_paused(self, paused):

        if paused:

            self.paused.set()

            # Remove pending commands
            try:
                while True:
                    self.queue.get_nowait()

            except queue.Empty:
                pass

        else:
            self.paused.clear()

    # ======================================
    # SUBMIT LOOK COMMAND
    # ======================================

    def submit(self, look):

        if self.paused.is_set():
            return

        if look not in (
            "LEFT",
            "CENTER",
            "RIGHT"
        ):
            return

        # Remove previous pending request
        try:
            self.queue.get_nowait()

        except queue.Empty:
            pass

        # Add newest request
        try:
            self.queue.put_nowait(look)

        except queue.Full:
            pass

    # ======================================
    # FLASK API COMMUNICATION
    # ======================================

    def _request(
        self,
        path,
        payload=None,
        timeout=5
    ):

        raw = (
            None
            if payload is None
            else json.dumps(
                payload
            ).encode("utf-8")
        )

        headers = (
            {
                "Content-Type": "application/json"
            }
            if raw
            else {}
        )

        req = Request(
            self.base_url + path,
            data=raw,
            headers=headers,
            method=(
                "POST" if raw else "GET"
            )
        )

        with urlopen(
            req,
            timeout=timeout
        ) as response:

            return json.loads(
                response.read().decode("utf-8")
            )

    # ======================================
    # REAL SERVO WORKER
    # ======================================

    def _worker(self):

        while not self.stop_event.is_set():

            try:
                look = self.queue.get(
                    timeout=0.25
                )

            except queue.Empty:
                continue

            if self.paused.is_set():
                continue

            # Limit frequency of commands
            if (
                time.monotonic()
                - self.last_move < 1.5
            ):
                continue

            try:

                # ==========================
                # READ SERVO STATUS
                # ==========================

                status = self._request(
                    "/api/status"
                )

                if not (
                    status.get("connected")
                    and status.get("info_ready")
                ):
                    continue

                if status.get("busy"):
                    continue

                servo = next(
                    (
                        item
                        for item in status.get(
                            "servos", []
                        )
                        if item.get("id")
                        == self.servo_id
                    ),
                    None
                )

                if (
                    servo is None
                    or not servo.get("online")
                ):
                    continue

                actual = servo.get("actual")
                low = servo.get("min")
                high = servo.get("max")

                if not all(
                    type(x) is int
                    for x in (
                        actual,
                        low,
                        high
                    )
                ):
                    continue

                # ==========================
                # SAVE INITIAL CENTER
                # ==========================

                if self.center is None:

                    self.center = actual

                    print(
                        "[AUTO LOOK] Center captured: "
                        f"{self.center} ticks"
                    )

                # Detect manual movement outside
                # the small autonomous range.
                if (
                    abs(actual - self.center)
                    > self.amplitude + 2
                ):

                    print(
                        "[AUTO LOOK] Skipping: "
                        "servo outside auto range"
                    )

                    continue

                # ==========================
                # TARGET POSITION
                # ==========================

                offset = {
                    "LEFT": -self.amplitude,
                    "CENTER": 0,
                    "RIGHT": self.amplitude
                }[look]

                target = self.center + offset

                if not (
                    low + 20
                    <= target
                    <= high - 20
                ):
                    continue

                if target == actual:
                    continue

                if self.paused.is_set():
                    continue

                # ==========================
                # SEND MOVE COMMAND
                # ==========================

                self.last_move = time.monotonic()

                result = self._request(
                    f"/api/servo/{self.servo_id}/move",
                    {"target": target},
                    timeout=30
                )

                if not result.get("ok"):
                    raise RuntimeError(
                        str(result)
                    )

                print(
                    f"[AUTO LOOK] {look}: "
                    f"{actual} -> "
                    f"{result.get('actual')} ticks"
                )

            except Exception as exc:

                print(
                    f"[AUTO LOOK] {exc}; "
                    "no automatic retry"
                )

    # ======================================
    # CLOSE CONTROLLER
    # ======================================

    def close(self):
        self.stop_event.set()
