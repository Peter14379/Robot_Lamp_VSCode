
import unittest

from emotion_brain import (
    EmotionBrain,
    EMOTIONS
)


class TestBrain(unittest.TestCase):

    def test_held_hand_emotion_then_decay(self):

        brain = EmotionBrain(
            sleep_after=100,
            wake_idle=0
        )

        for i in range(1, 51):
            decision = brain.update(
                "HAPPY",
                now=i * 0.05
            )

        self.assertEqual(
            decision.emotion,
            "HAPPY"
        )

        self.assertGreater(
            decision.hormone["HAPPY"],
            0.45
        )

        for i in range(51, 151):
            decision = brain.update(
                "IDLE",
                now=i * 0.05
            )

        self.assertEqual(
            decision.emotion,
            "IDLE"
        )

        self.assertLess(
            decision.hormone["HAPPY"],
            0.04
        )

    def test_single_frame_does_not_trigger(self):

        brain = EmotionBrain(
            sleep_after=100,
            wake_idle=0
        )

        brain.update(
            "IDLE",
            now=0
        )

        decision = brain.update(
            "ANGRY",
            now=0.03
        )

        self.assertEqual(
            decision.emotion,
            "IDLE"
        )

    def test_voice_priority(self):

        brain = EmotionBrain(
            sleep_after=100,
            wake_idle=0
        )

        brain.update(
            "HAPPY",
            now=0
        )

        for i in range(1, 40):
            brain.update(
                "HAPPY",
                now=i * 0.05
            )

        decision = brain.update(
            "HAPPY",
            voice="SAD",
            now=2.05
        )

        self.assertEqual(
            decision.emotion,
            "SAD"
        )

    def test_sleep_and_wake(self):

        brain = EmotionBrain(
            sleep_after=5,
            wake_idle=0.2
        )

        brain.update(
            "IDLE",
            now=0
        )

        decision = brain.update(
            "IDLE",
            now=5.1
        )

        self.assertEqual(
            decision.emotion,
            "SLEEPY"
        )

        brain.update(
            "HAPPY",
            now=5.2
        )

        for i in range(1, 25):
            decision = brain.update(
                "HAPPY",
                now=5.2 + i * 0.05
            )

        self.assertEqual(
            decision.emotion,
            "HAPPY"
        )

    def test_forced_sleep(self):

        brain = EmotionBrain(
            sleep_after=100,
            wake_idle=0
        )

        for i in range(1, 20):
            brain.update(
                "HAPPY",
                now=i * 0.05
            )

        decision = brain.update(
            "HAPPY",
            voice="SLEEPY",
            now=1
        )

        self.assertEqual(
            decision.emotion,
            "SLEEPY"
        )

        decision = brain.update(
            "HAPPY",
            now=1.05
        )

        self.assertEqual(
            decision.emotion,
            "SLEEPY"
        )

        brain.update(
            "IDLE",
            now=1.10
        )

        decision = brain.update(
            "HAPPY",
            now=1.15
        )

        self.assertNotEqual(
            decision.source,
            "VOICE SLEEP"
        )

    def test_hormone_bounded(self):

        brain = EmotionBrain(
            sleep_after=100,
            wake_idle=0
        )

        for i in range(1, 1400):
            decision = brain.update(
                "ANGRY",
                now=i * 0.05
            )

        self.assertTrue(
            all(
                0 <= decision.hormone[k] <= 1
                for k in EMOTIONS
            )
        )


if __name__ == "__main__":
    unittest.main()
