
"""Robot Lamp: Perception Memory.

Save only detection events, not photos or videos.
"""

import csv
from datetime import datetime
from pathlib import Path


class PerceptionMemory:

    def __init__(self, filename, stable_frames=5):
        self.path = Path(filename)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.stable_frames = max(
            1, int(stable_frames)
        )

        self.active = {}
        self.hit = {}
        self.miss = {}

        if (
            not self.path.exists()
            or self.path.stat().st_size == 0
        ):
            with self.path.open(
                "w",
                newline="",
                encoding="utf-8-sig"
            ) as file:
                csv.writer(file).writerow([
                    "timestamp",
                    "event",
                    "label",
                    "detail"
                ])

    def currently_seen(self, label):
        return self.active.get(label, False)

    def observe(self, detections, details=None):
        """
        Example detections:
        {
            "FACE": True,
            "HAND": False,
            "MOTION": True
        }
        """

        details = details or {}
        events = []

        labels = (
            set(self.active)
            | set(detections)
        )

        for label in sorted(labels):
            seen = bool(detections.get(label, False))

            if seen:
                self.hit[label] = (
                    self.hit.get(label, 0) + 1
                )
                self.miss[label] = 0
            else:
                self.hit[label] = 0
                self.miss[label] = (
                    self.miss.get(label, 0) + 1
                )

            was_seen = self.active.get(
                label, False
            )

            if (
                seen
                and not was_seen
                and self.hit[label] >= self.stable_frames
            ):
                self.active[label] = True
                events.append(f"{label}_APPEARED")

            elif (
                not seen
                and was_seen
                and self.miss[label] >= self.stable_frames
            ):
                self.active[label] = False
                events.append(f"{label}_DISAPPEARED")

        if events:
            timestamp = (
                datetime.now()
                .astimezone()
                .isoformat(timespec="seconds")
            )

            with self.path.open(
                "a",
                newline="",
                encoding="utf-8"
            ) as file:
                writer = csv.writer(file)

                for event in events:
                    label = event.rsplit("_", 1)[0]
                    detail = details.get(label, "")

                    writer.writerow([
                        timestamp,
                        event,
                        label,
                        detail
                    ])

                    print(
                        f"[MEMORY] {timestamp} "
                        f"{event} {detail}"
                    )

        return events
