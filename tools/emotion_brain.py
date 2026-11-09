
"""Robot Lamp emotion expression decision layer.

Week 02: Sensorimotor filtering and hysteresis
Week 04: Artificial hormone model

This module decides which expression to show.
It does not drive physical motors.
"""

from __future__ import annotations

from dataclasses import dataclass
import time


EMOTIONS = (
    "HAPPY",
    "SURPRISED",
    "SAD",
)

VALID = set(EMOTIONS) | {
    "IDLE",
    "SLEEPY",
}


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class Decision:
    emotion: str
    source: str
    raw_gesture: str
    hormone: dict[str, float]
    receptor: dict[str, float]
    stimulus: dict[str, float]
    inactive_seconds: float


# RL_HORMONE_CPG_V1
class EmotionBrain:
    """
    Sensor -> Hormone -> Receptor -> Decision

    Production:
        P = kprod * S

    Clearance:
        C = kclear * H

    Binding:
        B = kbind * R

    State update:
        Hnext = clamp(H + dt*(P-C-B), 0, 1)

    Receptor:
        R = H / (H + receptor_k)

    Values are experimental project parameters.
    """

    def __init__(
        self,
        sleep_after=30.0,
        wake_idle=1.5,
        voice_hold=5.0,
    ):
        self.sleep_after = max(
            5.0,
            float(sleep_after)
        )

        self.wake_idle = max(
            0.0,
            float(wake_idle)
        )

        self.voice_hold = max(
            0.1,
            float(voice_hold)
        )

        # Artificial hormone parameters
        self.kprod = 0.8
        self.kclear = 0.6
        self.kbind = 0.2
        self.receptor_k = 0.27

        # Decision thresholds
        self.enter = 0.47
        self.exit = 0.28
        self.switch_gap = 0.13
        self.min_dwell = 0.40

        self.H = {
            name: 0.0
            for name in EMOTIONS
        }

        self.effective = "IDLE"

        self.last_change = -1e8
        self.last_t = None
        self.last_activity = None

        self.voice_emotion = None
        self.voice_until = 0.0

        self.forced_sleep = False
        self.wake_pending = None
        self.wake_until = 0.0

        self.previous_gesture = "IDLE"

    def reset(self, now=None):
        now = (
            time.monotonic()
            if now is None
            else now
        )

        for key in EMOTIONS:
            self.H[key] = 0.0

        self.effective = "IDLE"
        self.forced_sleep = False
        self.voice_emotion = None
        self.voice_until = 0.0
        self.wake_pending = None

        self.last_activity = now
        self.last_change = now
        self.last_t = now

        self.previous_gesture = "IDLE"

    def _receptor(self, h):
        if h <= 0:
            return 0.0

        return clamp(
            h / (self.receptor_k + h)
        )

    def update(
        self,
        gesture,
        *,
        valid=True,
        voice=None,
        now=None,
    ):
        now = (
            time.monotonic()
            if now is None
            else float(now)
        )

        if self.last_t is None:
            self.last_t = now
            self.last_activity = now

        dt = clamp(
            now - self.last_t,
            0.0,
            0.20
        )

        self.last_t = now

        # Invalid camera sample
        if not valid or gesture not in VALID:
            gesture = "IDLE"

        # ==============================
        # VOICE INPUT
        # ==============================

        voice_event = voice in VALID

        if voice_event:

            if voice == "IDLE":
                self.reset(now)

                self.voice_emotion = "IDLE"
                self.voice_until = (
                    now + self.voice_hold
                )

            elif voice == "SLEEPY":
                self.forced_sleep = True
                self.voice_emotion = None
                self.voice_until = 0.0

            else:
                self.forced_sleep = False
                self.voice_emotion = voice

                self.voice_until = (
                    now + self.voice_hold
                )

                self.last_activity = now

        # ==============================
        # GESTURE ACTIVITY
        # ==============================

        gesture_changed = (
            gesture != self.previous_gesture
        )

        self.previous_gesture = gesture

        if gesture in EMOTIONS:

            if (
                self.forced_sleep
                and gesture_changed
            ):
                self.forced_sleep = False

            if not self.forced_sleep:
                self.last_activity = now

        voice_active = (
            self.voice_emotion in VALID
            and now < self.voice_until
        )

        if self.last_activity is None:
            self.last_activity = now

        inactive = max(
            0.0,
            now - self.last_activity
        )

        # ==============================
        # SENSOR STIMULUS
        # ==============================

        stimuli = {
            name: 0.0
            for name in EMOTIONS
        }

        if not self.forced_sleep:

            if gesture in EMOTIONS:
                stimuli[gesture] = 0.8

            if voice_active:

                stimuli = {
                    name: 0.0
                    for name in EMOTIONS
                }

                if self.voice_emotion in EMOTIONS:
                    stimuli[self.voice_emotion] = 1.0

        # ==============================
        # ARTIFICIAL HORMONE
        # ==============================

        old = self.H.copy()

        old_receptor = {
            k: self._receptor(v)
            for k, v in old.items()
        }

        for name in EMOTIONS:

            production = (
                self.kprod * stimuli[name]
            )

            clearance = (
                self.kclear * old[name]
            )

            binding = (
                self.kbind
                * old_receptor[name]
            )

            self.H[name] = clamp(
                old[name]
                + dt * (
                    production
                    - clearance
                    - binding
                )
            )

        receptor = {
            k: self._receptor(h)
            for k, h in self.H.items()
        }

        # ==============================
        # DECISION CONTROLLER
        # ==============================

        desired = "IDLE"
        reason = "BASELINE"

        if self.forced_sleep:

            desired = "SLEEPY"
            reason = "VOICE SLEEP"

        elif voice_active:

            desired = self.voice_emotion
            reason = "VOICE COMMAND"

        elif inactive >= self.sleep_after:

            desired = "SLEEPY"
            reason = "INACTIVITY"

        else:
            best = max(
                EMOTIONS,
                key=lambda k: receptor[k]
            )

            best_r = receptor[best]
            current = self.effective

            if (
                current in EMOTIONS
                and receptor[current] >= self.exit
            ):
                if (
                    best == current
                    or best_r < (
                        receptor[current]
                        + self.switch_gap
                    )
                ):
                    desired = current

                elif best_r >= self.enter:
                    desired = best

            elif best_r >= self.enter:
                desired = best

            if desired != "IDLE":
                reason = "HORMONE + HYSTERESIS"

        # ==============================
        # WAKE TRANSITION
        # ==============================

        if self.wake_pending is not None:

            if self.forced_sleep:
                self.wake_pending = None

            elif now < self.wake_until:

                if desired in EMOTIONS:
                    self.wake_pending = desired

                desired = "IDLE"
                reason = "WAKING"

            else:

                if desired in EMOTIONS:
                    self.wake_pending = desired

                desired = self.wake_pending
                self.wake_pending = None

                reason = "WAKE FINISHED"

        elif (
            self.effective == "SLEEPY"
            and desired in EMOTIONS
            and self.wake_idle > 0
        ):
            self.wake_pending = desired

            self.wake_until = (
                now + self.wake_idle
            )

            desired = "IDLE"
            reason = "WAKING"

        # ==============================
        # MINIMUM DWELL TIME
        # ==============================

        immediate = reason in {
            "VOICE COMMAND",
            "VOICE SLEEP",
            "WAKING",
            "WAKE FINISHED",
        }

        if (
            desired != self.effective
            and not immediate
            and now - self.last_change
                < self.min_dwell
        ):
            desired = self.effective
            reason = "MIN DWELL"

        if desired != self.effective:
            self.effective = desired
            self.last_change = now

        return Decision(
            emotion=self.effective,
            source=reason,
            raw_gesture=gesture,
            hormone=self.H.copy(),
            receptor=receptor,
            stimulus=stimuli,
            inactive_seconds=inactive,
        )
